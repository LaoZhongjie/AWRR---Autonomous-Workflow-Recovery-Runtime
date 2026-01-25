import json
import time
import argparse
from dataclasses import dataclass
from state import WorldState, Budget, StepContext, TraceEvent
from trace_logger import TraceLogger
from oracle_checker import check_success, check_consistency
import mock_api
from saga import SagaManager, TransactionStack


class BudgetTracker:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.budget.start_time = time.perf_counter()
    
    def estimate_tokens(self, data: dict) -> int:
        """估算 token 使用"""
        return len(json.dumps(data)) // 4
    
    def check_budget(self) -> dict:
        """检查预算剩余"""
        elapsed = time.perf_counter() - self.budget.start_time
        return {
            "tokens": self.budget.max_tokens - self.budget.used_tokens,
            "tool_calls": self.budget.max_tool_calls - self.budget.used_tool_calls,
            "time": self.budget.max_time_s - elapsed
        }

    def snapshot(self) -> dict:
        """返回预算剩余和已用"""
        elapsed = time.perf_counter() - self.budget.start_time
        remaining = self.check_budget()
        used = {
            "tokens": self.budget.used_tokens,
            "tool_calls": self.budget.used_tool_calls,
            "time": elapsed
        }
        return {"remaining": remaining, "used": used}
    
    def is_exhausted(self) -> bool:
        """检查预算是否耗尽"""
        remaining = self.check_budget()
        return any(v <= 0 for v in remaining.values())
    
    def consume(self, tokens: int = 0, tool_calls: int = 0):
        """消耗预算"""
        self.budget.used_tokens += tokens
        self.budget.used_tool_calls += tool_calls


class WorkflowRunner:
    def __init__(self, use_saga: bool = True):
        self.logger = TraceLogger()
        self.use_saga = use_saga


@dataclass
class ToolSpec:
    name: str
    do: callable
    compensate: callable | None
    irreversible: bool
    compensate_arg_keys: tuple[str, ...] = ()
    
    def run_task(self, task: dict) -> dict:
        """执行单个任务"""
        task_id = task["task_id"]
        
        # 初始化状态
        initial_state = task["initial_world_state"]
        world_state = WorldState(
            records=initial_state["records"].copy(),
            inventory=initial_state["inventory"].copy(),
            audit_log=initial_state["audit_log"].copy()
        )
        saga_manager = SagaManager(self.logger, TransactionStack())
        compensation_needed = False
        
        # 初始化预算
        budget = Budget(
            max_tokens=10000,
            max_tool_calls=50,
            max_time_s=60.0
        )
        tracker = BudgetTracker(budget)
        
        # 执行步骤
        steps = task["steps"]
        fault_injections = task.get("fault_injections", [])
        
        checkpoint = world_state.deep_copy()
        failure_history = []  # 记录失败历史用于死循环检测
        retry_counts = {}
        
        step_idx = 0
        while step_idx < len(steps):
            if tracker.is_exhausted():
                self._escalate_human(task_id, step_idx, "budget_exhausted", world_state, tracker)
                self._append_final_event(task_id, step_idx, tracker, "escalated", "budget_exhausted")
                return {"task_id": task_id, "status": "escalated", "reason": "budget_exhausted"}
            
            step = steps[step_idx]
            tool_name = step["tool_name"]
            params = step["params"]
            attempt_idx = retry_counts.get(step_idx, 0)
            
            # 查找故障注入
            fault_injection = None
            for fi in fault_injections:
                if fi["step_idx"] == step_idx:
                    injected = mock_api.FaultInjector.should_inject(
                        fi, step_idx, task_id, world_state
                    )
                    if injected:
                        fault_injection = injected
                    break
            
            # 执行工具
            tool_spec = self._tool_specs().get(tool_name)
            result = self._execute_step(world_state, tool_name, tool_spec, params, fault_injection)
            
            # 记录轨迹
            state_hash = world_state.compute_hash()
            budget_snapshot = tracker.snapshot()
            event = TraceEvent(
                task_id=task_id,
                step_idx=step_idx,
                step_name=step["step_name"],
                tool_name=tool_name,
                params=params,
                status=result.status,
                latency_ms=result.latency_ms,
                error_type=result.error_type,
                injected_fault=result.injected_fault,
                state_hash=state_hash,
                budget=budget_snapshot["remaining"],
                recovery_action=None,
                ts_ms=int(time.time() * 1000),
                attempt_idx=attempt_idx,
                event_type="tool_call",
                budget_remaining_tokens=budget_snapshot["remaining"]["tokens"],
                budget_remaining_tool_calls=budget_snapshot["remaining"]["tool_calls"],
                budget_remaining_time_s=budget_snapshot["remaining"]["time"],
                budget_used_tokens=budget_snapshot["used"]["tokens"],
                budget_used_tool_calls=budget_snapshot["used"]["tool_calls"],
                budget_used_time_s=budget_snapshot["used"]["time"],
                compensation_action=None,
                saga_stack_depth=saga_manager.stack.depth(),
                diagnosis=None
            )
            
            # 消耗预算
            tokens = tracker.estimate_tokens(params)
            tracker.consume(tokens=tokens, tool_calls=1)
            
            if result.status == "ok":
                self.logger.append(event)
                if tool_spec and tool_spec.compensate and not tool_spec.irreversible:
                    compensate_args = tuple(
                        params[key] for key in tool_spec.compensate_arg_keys
                    )
                    saga_manager.stack.push(tool_spec.compensate, compensate_args)
                checkpoint = world_state.deep_copy()
                failure_history.clear()
                retry_counts[step_idx] = 0
                step_idx += 1
            else:
                # 恢复策略
                recovery_action = self._recover(
                    world_state, checkpoint, result, step_idx, failure_history
                )
                event.recovery_action = recovery_action
                event.diagnosis = {
                    "layer_pred": None,
                    "action_pred": None,
                    "confidence": None,
                    "rationale_short": None
                }
                self.logger.append(event)
                
                if recovery_action == "escalate":
                    self._escalate_human(task_id, step_idx, result.error_type, world_state, tracker)
                    self._append_final_event(task_id, step_idx, tracker, "escalated", result.error_type)
                    return {"task_id": task_id, "status": "escalated", "reason": result.error_type}
                elif recovery_action == "retry":
                    # 重试同一步骤
                    retry_counts[step_idx] = retry_counts.get(step_idx, 0) + 1
                    continue
                elif recovery_action == "rollback":
                    if saga_manager.stack.depth() > 0:
                        compensation_needed = True
                    # 优先恢复 checkpoint 再执行 saga 补偿，避免补偿被旧快照覆盖
                    rollback_result = mock_api.rollback(world_state, checkpoint, fault_injection=None)
                    rollback_snapshot = tracker.snapshot()
                    rollback_event = TraceEvent(
                        task_id=task_id,
                        step_idx=step_idx,
                        step_name="rollback",
                        tool_name="rollback",
                        params={},
                        status=rollback_result.status,
                        latency_ms=rollback_result.latency_ms,
                        error_type=rollback_result.error_type,
                        injected_fault=rollback_result.injected_fault,
                        state_hash=world_state.compute_hash(),
                        budget=rollback_snapshot["remaining"],
                        recovery_action="rollback",
                        ts_ms=int(time.time() * 1000),
                        attempt_idx=0,
                        event_type="recovery",
                        budget_remaining_tokens=rollback_snapshot["remaining"]["tokens"],
                        budget_remaining_tool_calls=rollback_snapshot["remaining"]["tool_calls"],
                        budget_remaining_time_s=rollback_snapshot["remaining"]["time"],
                        budget_used_tokens=rollback_snapshot["used"]["tokens"],
                        budget_used_tool_calls=rollback_snapshot["used"]["tool_calls"],
                        budget_used_time_s=rollback_snapshot["used"]["time"],
                        compensation_action=None,
                        saga_stack_depth=saga_manager.stack.depth(),
                        diagnosis=None
                    )
                    self.logger.append(rollback_event)
                    tracker.consume(tokens=tracker.estimate_tokens(rollback_event.params), tool_calls=1)
                    if self.use_saga and saga_manager.stack.depth() > 0:
                        rollback_result = saga_manager.rollback_saga(
                            world_state=world_state,
                            task_id=task_id,
                            step_idx=step_idx,
                            tracker=tracker,
                            token_estimator=tracker.estimate_tokens,
                        )
                        if rollback_result.status != "ok":
                            self._escalate_human(task_id, step_idx, rollback_result.reason or "compensation_failed", world_state, tracker)
                            self._append_final_event(task_id, step_idx, tracker, "escalated", rollback_result.reason)
                            return {"task_id": task_id, "status": "escalated", "reason": rollback_result.reason}
                    retry_counts[step_idx] = retry_counts.get(step_idx, 0) + 1
                    continue
        
        # 检查成功
        success = check_success(world_state, task)
        final_outcome = "success" if success else "failed"
        srr_eligible = compensation_needed
        srr_pass = None
        if srr_eligible:
            srr_ok, _ = check_consistency(world_state, initial_state)
            srr_pass = srr_ok
        self._append_final_event(
            task_id,
            len(steps) - 1,
            tracker,
            final_outcome,
            None,
            srr_eligible=srr_eligible,
            srr_pass=srr_pass
        )
        return {
            "task_id": task_id,
            "status": "success" if success else "failed",
            "steps_executed": len(steps)
        }
    
    def _execute_step(
        self,
        world_state: WorldState,
        tool_name: str,
        tool_spec: ToolSpec | None,
        params: dict,
        fault_injection,
    ):
        """执行单个步骤"""
        if not tool_spec:
            raise ValueError(f"Unknown tool: {tool_name}")
        return tool_spec.do(world_state, **params, fault_injection=fault_injection)
    
    def _recover(self, world_state: WorldState, checkpoint: WorldState, result, step_idx: int, failure_history: list) -> str:
        """恢复策略"""
        error_type = result.error_type
        
        # 死循环检测
        state_hash = world_state.compute_hash()
        failure_history.append((step_idx, state_hash, error_type))
        
        if len(failure_history) >= 3:
            last_three = failure_history[-3:]
            if all(h[1] == last_three[0][1] for h in last_three):
                # 连续3次失败且状态未变
                return "escalate"
        
        # 恢复策略
        if error_type in ["Timeout", "HTTP_500"]:
            if len([h for h in failure_history if h[0] == step_idx]) <= 3:
                time.sleep(0.1 * (2 ** (len(failure_history) - 1)))  # 指数退避
                return "retry"
            else:
                return "escalate"
        
        elif error_type == "Conflict":
            return "rollback"
        
        elif error_type in ["PolicyRejected", "AuthDenied"]:
            mock_api.create_ticket(
                world_state,
                summary=f"Step failed: {error_type}",
                severity="high"
            )
            return "escalate"
        
        elif error_type in ["BadRequest", "NotFound", "StateCorruption"]:
            return "escalate"
        
        return "escalate"
    
    def _escalate_human(self, task_id: str, step_idx: int, reason: str, world_state: WorldState, tracker: BudgetTracker):
        """升级到人工"""
        mock_api.create_ticket(
            world_state,
            summary=f"Task {task_id} escalated at step {step_idx}: {reason}",
            severity="critical"
        )

    def _tool_specs(self) -> dict:
        return {
            "get_record": ToolSpec("get_record", mock_api.get_record, None, False),
            "policy_check": ToolSpec("policy_check", mock_api.policy_check, None, False),
            "update_record": ToolSpec("update_record", mock_api.update_record, None, False),
            "send_message": ToolSpec("send_message", mock_api.send_message, None, True),
            "commit": ToolSpec("commit", mock_api.commit, None, True),
            "rollback": ToolSpec("rollback", mock_api.rollback, None, True),
            "lock_inventory": ToolSpec(
                "lock_inventory",
                mock_api.lock_inventory,
                mock_api.unlock_inventory,
                False,
                compensate_arg_keys=("item_id", "qty"),
            ),
            "process_payment": ToolSpec(
                "process_payment",
                mock_api.process_payment,
                mock_api.refund_payment,
                False,
                compensate_arg_keys=("order_id", "amount"),
            ),
        }

    def _append_final_event(
        self,
        task_id: str,
        step_idx: int,
        tracker: BudgetTracker,
        final_outcome: str,
        reason: str | None,
        srr_eligible: bool | None = None,
        srr_pass: bool | None = None
    ):
        budget_snapshot = tracker.snapshot()
        final_event = TraceEvent(
            task_id=task_id,
            step_idx=step_idx,
            step_name="final",
            tool_name="final",
            params={},
            status="final",
            latency_ms=0,
            error_type=None,
            injected_fault=None,
            state_hash="",
            budget=budget_snapshot["remaining"],
            recovery_action=None,
            ts_ms=int(time.time() * 1000),
            attempt_idx=0,
            event_type="final",
            budget_remaining_tokens=budget_snapshot["remaining"]["tokens"],
            budget_remaining_tool_calls=budget_snapshot["remaining"]["tool_calls"],
            budget_remaining_time_s=budget_snapshot["remaining"]["time"],
            budget_used_tokens=budget_snapshot["used"]["tokens"],
            budget_used_tool_calls=budget_snapshot["used"]["tool_calls"],
            budget_used_time_s=budget_snapshot["used"]["time"],
            final_outcome=final_outcome,
            final_reason=reason,
            compensation_action=None,
            saga_stack_depth=0,
            diagnosis=None,
            srr_eligible=srr_eligible,
            srr_pass=srr_pass
        )
        self.logger.append(final_event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="tasks.jsonl")
    parser.add_argument("--out", default="traces.jsonl")
    parser.add_argument("--saga", action="store_true", help="Enable saga compensation")
    parser.add_argument("--no-saga", dest="saga", action="store_false", help="Disable saga compensation")
    parser.set_defaults(saga=True)
    args = parser.parse_args()
    
    # 加载任务
    tasks = []
    with open(args.tasks, 'r') as f:
        for line in f:
            tasks.append(json.loads(line))
    
    print(f"Loaded {len(tasks)} tasks from {args.tasks}")
    
    # 运行任务
    runner = WorkflowRunner(use_saga=args.saga)
    results = []
    
    for task in tasks:
        result = runner.run_task(task)
        results.append(result)
        print(f"{result['task_id']}: {result['status']}")
    
    # 保存轨迹
    runner.logger.flush_jsonl(args.out)
    
    # 统计
    success_count = sum(1 for r in results if r["status"] == "success")
    escalated_count = sum(1 for r in results if r["status"] == "escalated")
    
    print(f"\n=== Summary ===")
    print(f"Success: {success_count}/{len(tasks)}")
    print(f"Escalated: {escalated_count}/{len(tasks)}")


if __name__ == "__main__":
    main()
