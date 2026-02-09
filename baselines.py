import json
import time
import argparse
import random
from dataclasses import dataclass
from typing import Optional

from state import WorldState, Budget, TraceEvent, StepContext
from trace_logger import TraceLogger
from oracle_checker import check_success
import mock_api
from constants import SEED
from learning import FaultSignature, MemoryBank


@dataclass
class RecoveryDecision:
    action: str
    payload: dict
    source: str  # rule | diagnosis | memory


class BudgetTracker:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.budget.start_time = time.perf_counter()
    
    def estimate_tokens(self, data: dict) -> int:
        return len(json.dumps(data)) // 4
    
    def check_budget(self) -> dict:
        elapsed = time.perf_counter() - self.budget.start_time
        return {
            "tokens": self.budget.max_tokens - self.budget.used_tokens,
            "tool_calls": self.budget.max_tool_calls - self.budget.used_tool_calls,
            "time": self.budget.max_time_s - elapsed
        }

    def snapshot(self) -> dict:
        elapsed = time.perf_counter() - self.budget.start_time
        remaining = self.check_budget()
        used = {
            "tokens": self.budget.used_tokens,
            "tool_calls": self.budget.used_tool_calls,
            "time": elapsed
        }
        return {"remaining": remaining, "used": used}
    
    def is_exhausted(self) -> bool:
        remaining = self.check_budget()
        return any(v <= 0 for v in remaining.values())
    
    def consume(self, tokens: int = 0, tool_calls: int = 0):
        self.budget.used_tokens += tokens
        self.budget.used_tool_calls += tool_calls


class BaselineRunner:
    def __init__(
        self,
        mode: str,
        seed: int = SEED,
        diagnosis_mode: str = "mock",
        memory_path: Optional[str] = None,
        memory_threshold: float = 0.8,
    ):
        """
        Args:
            mode: "B0" | "B1" | "B2" | "B3" | "B4"
            seed: Random seed for reproducibility
            diagnosis_mode: "mock" | "llm" (for B3/B4)
            memory_path: optional path to memory bank JSON (for B4)
        """
        if mode not in ["B0", "B1", "B2", "B3", "B4"]:
            raise ValueError(f"Invalid mode: {mode}")

        self.mode = mode
        self.logger = TraceLogger()
        self.seed = seed
        random.seed(seed)
        self.llm_calls = 0
        self.memory_threshold = memory_threshold

        # B3/B4: Initialize diagnosis agent
        if mode in ["B3", "B4"]:
            from diagnosis import DiagnosisAgent
            self.diagnosis_agent = DiagnosisAgent(mode=diagnosis_mode)
        else:
            self.diagnosis_agent = None

        # B4: Initialize memory bank
        self.memory_bank = None
        if mode == "B4":
            self.memory_bank = MemoryBank(memory_path)
    
    def run_task(self, task: dict) -> dict:
        """执行单个任务"""
        task_id = task["task_id"]
        
        # 初始化状态
        initial_state = task["initial_world_state"]
        world_state = WorldState(
            records=json.loads(json.dumps(initial_state["records"])),
            inventory=json.loads(json.dumps(initial_state["inventory"])),
            audit_log=json.loads(json.dumps(initial_state["audit_log"]))
        )
        
        # 初始化预算
        budget = Budget(
            max_tokens=10000,
            max_tool_calls=50,
            max_time_s=60.0
        )
        tracker = BudgetTracker(budget)
        
        steps = task["steps"]
        fault_injections = task.get("fault_injections", [])
        
        checkpoint = world_state.deep_copy()
        retry_counts = {}
        first_failure_signature: Optional[FaultSignature] = None
        first_failure_action: Optional[str] = None
        
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
            state_hash_pre = world_state.compute_hash()
            
            # 查找故障注入
            fault_injection = None
            for fi in fault_injections:
                if fi["step_idx"] == step_idx:
                    injected = mock_api.FaultInjector.should_inject(
                        fi, step_idx, task_id, world_state, attempt_idx
                    )
                    if injected:
                        fault_injection = injected
                    break

            # 执行工具
            result = self._execute_step(world_state, tool_name, params, fault_injection)
            
            # 记录轨迹
            state_hash = world_state.compute_hash()
            budget_snapshot = tracker.snapshot()
            
            # 准备 StepContext (for B3)
            step_context = StepContext(
                task_id=task_id,
                step_idx=step_idx,
                step_name=step["step_name"],
                tool_name=tool_name,
                params=params,
                state_hash=state_hash,
                budget_remaining=tracker.check_budget()
            )
            
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
                saga_stack_depth=0,
                diagnosis=None
            )
            
            # 消耗预算
            tokens = tracker.estimate_tokens(params)
            tracker.consume(tokens=tokens, tool_calls=1)
            
            if result.status == "ok":
                self.logger.append(event)
                checkpoint = world_state.deep_copy()
                retry_counts[step_idx] = 0
                step_idx += 1
            else:
                # 根据 baseline 模式选择恢复策略
                fault_signature = FaultSignature.from_failure(step_context, result)
                if first_failure_signature is None:
                    first_failure_signature = fault_signature

                decision = self._get_recovery_action(
                    result, step_idx, retry_counts, world_state, checkpoint,
                    step_context=step_context,
                    history_events=self.logger.events,
                    fault_signature=fault_signature
                )
                action = decision.action
                payload = decision.payload

                recovery_label = action
                if self.mode == "B4" and decision.source in ["memory", "diagnosis"]:
                    recovery_label = f"{decision.source}:{action}"
                event.recovery_action = recovery_label
                event.diagnosis = payload
                self.logger.append(event)

                if first_failure_action is None:
                    first_failure_action = action

                if action == "fail":
                    self._append_final_event(task_id, step_idx, tracker, "failed", result.error_type)
                    return {"task_id": task_id, "status": "failed", "reason": result.error_type}
                
                elif action == "escalate":
                    self._escalate_human(task_id, step_idx, result.error_type, world_state, tracker)
                    self._append_final_event(task_id, step_idx, tracker, "escalated", result.error_type)
                    return {"task_id": task_id, "status": "escalated", "reason": result.error_type}
                
                elif action == "retry":
                    retry_counts[step_idx] = retry_counts.get(step_idx, 0) + 1
                    if self.mode == "B1":
                        time.sleep(0.05)
                    elif self.mode in ["B2", "B3", "B4"]:
                        backoff = 0.1 * (2 ** (retry_counts[step_idx] - 1))
                        time.sleep(min(backoff, 0.4))
                    continue
                
                elif action == "rollback_then_retry":
                    # Internal rollback: deep-copy checkpoint state and record rollback in audit_log
                    # (used by stateful faults that clear on rollback observation)
                    world_state.records = json.loads(json.dumps(checkpoint.records))
                    world_state.inventory = json.loads(json.dumps(checkpoint.inventory))
                    world_state.audit_log = json.loads(json.dumps(checkpoint.audit_log)) + [
                        {"action": "rollback", "timestamp": int(time.time())}
                    ]
                    retry_counts[step_idx] = retry_counts.get(step_idx, 0) + 1
                    continue
                
                elif action == "rollback":
                    # Same behavior as rollback_then_retry in this simplified runner
                    world_state.records = json.loads(json.dumps(checkpoint.records))
                    world_state.inventory = json.loads(json.dumps(checkpoint.inventory))
                    world_state.audit_log = json.loads(json.dumps(checkpoint.audit_log)) + [
                        {"action": "rollback", "timestamp": int(time.time())}
                    ]
                    retry_counts[step_idx] = retry_counts.get(step_idx, 0) + 1
                    continue
                
                elif action == "compensate":
                    # B3 可能返回 compensate，暂时视为 escalate
                    self._escalate_human(task_id, step_idx, result.error_type, world_state, tracker)
                    self._append_final_event(task_id, step_idx, tracker, "escalated", "compensate_needed")
                    return {"task_id": task_id, "status": "escalated", "reason": "compensate_needed"}
        
        # 检查成功
        success = check_success(world_state, task)
        if self.mode == "B4" and self.memory_bank is not None:
            if first_failure_signature is not None and first_failure_action is not None:
                self.memory_bank.upsert(
                    first_failure_signature,
                    first_failure_action,
                    success=success,
                )
        final_outcome = "success" if success else "failed"
        self._append_final_event(task_id, len(steps) - 1, tracker, final_outcome, None)
        return {
            "task_id": task_id,
            "status": "success" if success else "failed",
            "steps_executed": len(steps)
        }
    
    def _execute_step(self, world_state: WorldState, tool_name: str, params: dict, fault_injection):
        """执行单个步骤"""
        tool_map = {
            "get_record": mock_api.get_record,
            "auth_check": mock_api.auth_check,
            "policy_check": mock_api.policy_check,
            "update_record": mock_api.update_record,
            "send_message": mock_api.send_message,
            "notify_user": mock_api.notify_user,
            "create_ticket": mock_api.create_ticket,
            "commit": mock_api.commit,
            "rollback": mock_api.rollback,
            "lock_inventory": mock_api.lock_inventory,
            "unlock_inventory": mock_api.unlock_inventory,
            "process_payment": mock_api.process_payment,
            "refund_payment": mock_api.refund_payment,
            "write_audit": mock_api.write_audit,
        }
        
        tool_func = tool_map.get(tool_name)
        if not tool_func:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        return tool_func(world_state, **params, fault_injection=fault_injection)
    
    def _default_payload(self, error_type: str, action: str, source: str, note: str) -> dict:
        default_layer = (
            "semantic"
            if error_type in ["PolicyRejected", "AuthDenied", "BadRequest"]
            else "persistent"
        )
        return {
            "layer_pred": default_layer,
            "action_pred": action,
            "confidence": 0.5,
            "rationale_short": note,
            "source": source,
        }

    def _low_confidence_fallback_action(
        self,
        error_type: str,
        b2_action: str,
        current_retries: int,
        step_context: Optional[StepContext],
        result,
    ) -> str:
        """
        When diagnosis confidence is low, avoid immediately escalating for
        potentially transient errors like NotFound (eventual consistency).
        """
        if error_type == "NotFound" and step_context is not None:
            # Allow a couple of quick retries before escalating; keeps B3/B4 from
            # over-escalating due to mock diagnosis noise.
            if current_retries < 2:
                return "retry"
        return b2_action

    def _apply_safety_guard(
        self,
        action: str,
        current_retries: int,
        step_context: Optional[StepContext],
    ) -> str:
        if step_context and step_context.budget_remaining.get("tool_calls", 0) <= 1:
            if action in ["retry", "rollback"]:
                return "escalate"
        if action in ["retry", "rollback"] and current_retries >= 3:
            return "escalate"
        return action

    def _get_recovery_action(
        self,
        result,
        step_idx: int,
        retry_counts: dict,
        world_state: WorldState,
        checkpoint: WorldState,
        step_context: StepContext | None = None,
        history_events: list | None = None,
        fault_signature: Optional[FaultSignature] = None,
    ) -> RecoveryDecision:
        """根据 baseline 模式决定恢复动作"""
        error_type = result.error_type
        current_retries = retry_counts.get(step_idx, 0)
        b2_action = self._get_recovery_action_b2(result, step_idx, retry_counts)

        if self.mode == "B0":
            return RecoveryDecision(
                "fail",
                self._default_payload(error_type, "fail", "rule", "no-recovery"),
                "rule",
            )

        if self.mode == "B1":
            action = "retry" if current_retries < 3 else "fail"
            return RecoveryDecision(
                action,
                self._default_payload(error_type, action, "rule", "naive-retry"),
                "rule",
            )

        if self.mode == "B2":
            return RecoveryDecision(
                b2_action,
                self._default_payload(error_type, b2_action, "rule", "rule-based"),
                "rule",
            )

        if self.mode == "B3":
            # diagnosis-driven recovery with safety fallback
            if step_context is None or history_events is None:
                return RecoveryDecision(
                    b2_action,
                    self._default_payload(error_type, b2_action, "rule", "missing-context"),
                    "rule",
                )

            diagnosis = self.diagnosis_agent.diagnose(step_context, result, history_events)
            # 模拟 LLM 诊断延迟
            time.sleep(0.05)
            self.llm_calls += 1
            payload = {
                "layer_pred": diagnosis.layer,
                "action_pred": diagnosis.action,
                "confidence": diagnosis.confidence,
                "rationale_short": diagnosis.reasoning[:120],
                "source": "diagnosis",
            }

            action = diagnosis.action
            if diagnosis.confidence < 0.7:
                fallback = self._low_confidence_fallback_action(
                    error_type, b2_action, current_retries, step_context, result
                )
                action = fallback
                payload["rationale_short"] = "diagnosis_low_confidence_fallback"
                payload["fallback_action"] = fallback

            guarded = self._apply_safety_guard(action, current_retries, step_context)
            if guarded != action:
                payload["final_action"] = guarded
            return RecoveryDecision(guarded, payload, "diagnosis")

        if self.mode == "B4":
            if step_context is None or history_events is None:
                return RecoveryDecision(
                    b2_action,
                    self._default_payload(error_type, b2_action, "rule", "missing-context"),
                    "rule",
                )

            if self.memory_bank is not None and fault_signature is not None:
                mem_action, mem_conf, matched_key = self.memory_bank.query(
                    fault_signature
                )
                if mem_action and mem_conf >= self.memory_threshold:
                    payload = {
                        "layer_pred": None,
                        "action_pred": mem_action,
                        "confidence": mem_conf,
                        "rationale_short": "memory-hit",
                        "source": "memory",
                        "signature": fault_signature.to_key(),
                        "matched_key": matched_key,
                    }
                    guarded = self._apply_safety_guard(
                        mem_action, current_retries, step_context
                    )
                    if guarded != mem_action:
                        payload["final_action"] = guarded
                        payload["overridden"] = True
                    return RecoveryDecision(guarded, payload, "memory")

            diagnosis = self.diagnosis_agent.diagnose(step_context, result, history_events)
            self.llm_calls += 1
            payload = {
                "layer_pred": diagnosis.layer,
                "action_pred": diagnosis.action,
                "confidence": diagnosis.confidence,
                "rationale_short": diagnosis.reasoning[:120],
                "source": "diagnosis",
            }
            action = diagnosis.action
            if diagnosis.confidence < 0.7:
                fallback = self._low_confidence_fallback_action(
                    error_type, b2_action, current_retries, step_context, result
                )
                action = fallback
                payload["rationale_short"] = "diagnosis_low_confidence_fallback"
                payload["fallback_action"] = fallback
            guarded = self._apply_safety_guard(action, current_retries, step_context)
            if guarded != action:
                payload["final_action"] = guarded
            return RecoveryDecision(guarded, payload, "diagnosis")

        return RecoveryDecision(
            "fail",
            self._default_payload(error_type, "fail", "rule", "fallback"),
            "rule",
        )
    
    def _get_recovery_action_b2(self, result, step_idx: int, retry_counts: dict) -> str:
        """B2 logic for fallback"""
        error_type = result.error_type
        current_retries = retry_counts.get(step_idx, 0)
        
        if error_type in ["Timeout", "HTTP_500"]:
            if current_retries < 3:
                return "retry"
            else:
                return "escalate"
        elif error_type == "Conflict":
            if current_retries < 3:
                return "rollback"
            else:
                return "escalate"
        elif error_type in ["PolicyRejected", "AuthDenied"]:
            return "escalate"
        else:
            return "escalate"
    
    def _escalate_human(self, task_id: str, step_idx: int, reason: str, 
                       world_state: WorldState, tracker: BudgetTracker):
        """升级到人工"""
        mock_api.create_ticket(
            world_state,
            summary=f"[{self.mode}] Task {task_id} escalated at step {step_idx}: {reason}",
            severity="critical"
        )

    def _append_final_event(
        self,
        task_id: str,
        step_idx: int,
        tracker: BudgetTracker,
        final_outcome: str,
        reason: str | None
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
            saga_stack_depth=0
        )
        self.logger.append(final_event)


def run(
    tasks_path: str,
    mode: str,
    seed: int = SEED,
    diagnosis_mode: str = "mock",
    out_path: Optional[str] = None,
    memory_path: Optional[str] = None,
) -> str:
    """运行 baseline 实验"""
    
    if mode == "B4" and memory_path is None:
        memory_path = "memory_bank.json"

    # 加载任务
    tasks = []
    with open(tasks_path, 'r') as f:
        for line in f:
            tasks.append(json.loads(line))
    
    print(f"\n{'='*60}")
    if mode in ["B3", "B4"]:
        print(f"Running Baseline: {mode} (diagnosis_mode={diagnosis_mode})")
    else:
        print(f"Running Baseline: {mode}")
    print(f"Tasks: {len(tasks)}, Seed: {seed}")
    print(f"{'='*60}\n")
    
    # 设置随机种子确保可复现
    random.seed(seed)
    
    # 运行任务
    runner = BaselineRunner(
        mode=mode,
        seed=seed,
        diagnosis_mode=diagnosis_mode,
        memory_path=memory_path,
    )
    results = []
    
    for i, task in enumerate(tasks):
        result = runner.run_task(task)
        results.append(result)
        
        status_symbol = "✓" if result["status"] == "success" else "✗"
        print(f"[{i+1:2d}/{len(tasks)}] {status_symbol} {result['task_id']}: {result['status']}")
    
    # 保存轨迹
    traces_path = out_path or f"traces_{mode}.jsonl"
    runner.logger.flush_jsonl(traces_path)
    
    # 统计
    success_count = sum(1 for r in results if r["status"] == "success")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    escalated_count = sum(1 for r in results if r["status"] == "escalated")
    
    print(f"\n{'='*60}")
    print(f"Results for {mode}:")
    print(f"  Success:   {success_count:2d}/{len(tasks)} ({success_count/len(tasks)*100:.1f}%)")
    print(f"  Failed:    {failed_count:2d}/{len(tasks)} ({failed_count/len(tasks)*100:.1f}%)")
    print(f"  Escalated: {escalated_count:2d}/{len(tasks)} ({escalated_count/len(tasks)*100:.1f}%)")
    if mode in ["B3", "B4"]:
        print(f"  LLM Diagnose Calls: {runner.llm_calls}")
    if mode == "B4":
        if memory_path:
            print(f"  Memory Bank: {memory_path}")
    print(f"{'='*60}\n")
    
    return traces_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="tasks.jsonl")
    parser.add_argument("--mode", choices=["B0", "B1", "B2", "B3", "B4"], required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--diagnosis-mode", choices=["mock", "llm"], default="mock")
    parser.add_argument("--out", default=None)
    parser.add_argument("--memory", default=None)
    args = parser.parse_args()
    
    traces_path = run(
        args.tasks,
        args.mode,
        args.seed,
        args.diagnosis_mode,
        out_path=args.out,
        memory_path=args.memory,
    )
    print(f"Traces saved to: {traces_path}")
