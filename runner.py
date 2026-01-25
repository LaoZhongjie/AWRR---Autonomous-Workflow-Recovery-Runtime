import json
import time
import argparse
from state import WorldState, Budget, StepContext, TraceEvent
from trace_logger import TraceLogger
from oracle_checker import check_success
import mock_api


class BudgetTracker:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.budget.start_time = time.time()
    
    def estimate_tokens(self, data: dict) -> int:
        """估算 token 使用"""
        return len(json.dumps(data)) // 4
    
    def check_budget(self) -> dict:
        """检查预算剩余"""
        elapsed = time.time() - self.budget.start_time
        return {
            "tokens": self.budget.max_tokens - self.budget.used_tokens,
            "tool_calls": self.budget.max_tool_calls - self.budget.used_tool_calls,
            "time": self.budget.max_time_s - elapsed
        }
    
    def is_exhausted(self) -> bool:
        """检查预算是否耗尽"""
        remaining = self.check_budget()
        return any(v <= 0 for v in remaining.values())
    
    def consume(self, tokens: int = 0, tool_calls: int = 0):
        """消耗预算"""
        self.budget.used_tokens += tokens
        self.budget.used_tool_calls += tool_calls


class WorkflowRunner:
    def __init__(self):
        self.logger = TraceLogger()
    
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
        
        step_idx = 0
        while step_idx < len(steps):
            if tracker.is_exhausted():
                self._escalate_human(task_id, step_idx, "budget_exhausted", world_state, tracker)
                return {"task_id": task_id, "status": "escalated", "reason": "budget_exhausted"}
            
            step = steps[step_idx]
            tool_name = step["tool_name"]
            params = step["params"]
            
            # 查找故障注入
            fault_injection = None
            for fi in fault_injections:
                if fi["step_idx"] == step_idx:
                    injected = mock_api.FaultInjector.should_inject(fi, step_idx)
                    if injected:
                        fault_injection = injected
                    break
            
            # 执行工具
            result = self._execute_step(world_state, tool_name, params, fault_injection)
            
            # 记录轨迹
            state_hash = world_state.compute_hash()
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
                budget=tracker.check_budget(),
                recovery_action=None
            )
            
            # 消耗预算
            tokens = tracker.estimate_tokens(params)
            tracker.consume(tokens=tokens, tool_calls=1)
            
            if result.status == "ok":
                self.logger.append(event)
                checkpoint = world_state.deep_copy()
                failure_history.clear()
                step_idx += 1
            else:
                # 恢复策略
                recovery_action = self._recover(
                    world_state, checkpoint, result, step_idx, failure_history
                )
                event.recovery_action = recovery_action
                self.logger.append(event)
                
                if recovery_action == "escalate":
                    self._escalate_human(task_id, step_idx, result.error_type, world_state, tracker)
                    return {"task_id": task_id, "status": "escalated", "reason": result.error_type}
                elif recovery_action == "retry":
                    # 重试同一步骤
                    continue
                elif recovery_action == "rollback":
                    # 回滚后重试
                    continue
        
        # 检查成功
        success = check_success(world_state, task)
        return {
            "task_id": task_id,
            "status": "success" if success else "failed",
            "steps_executed": len(steps)
        }
    
    def _execute_step(self, world_state: WorldState, tool_name: str, params: dict, fault_injection):
        """执行单个步骤"""
        tool_map = {
            "get_record": mock_api.get_record,
            "policy_check": mock_api.policy_check,
            "update_record": mock_api.update_record,
            "send_message": mock_api.send_message,
            "create_ticket": mock_api.create_ticket,
            "commit": mock_api.commit,
            "rollback": mock_api.rollback
        }
        
        tool_func = tool_map.get(tool_name)
        if not tool_func:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        return tool_func(world_state, **params, fault_injection=fault_injection)
    
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
            mock_api.rollback(world_state, checkpoint)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="tasks.jsonl")
    parser.add_argument("--out", default="traces.jsonl")
    args = parser.parse_args()
    
    # 加载任务
    tasks = []
    with open(args.tasks, 'r') as f:
        for line in f:
            tasks.append(json.loads(line))
    
    print(f"Loaded {len(tasks)} tasks from {args.tasks}")
    
    # 运行任务
    runner = WorkflowRunner()
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