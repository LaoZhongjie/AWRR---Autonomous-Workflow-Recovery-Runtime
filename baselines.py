import json
import time
import argparse
import random
from state import WorldState, Budget, TraceEvent
from trace_logger import TraceLogger
from oracle_checker import check_success
import mock_api


class BudgetTracker:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.budget.start_time = time.time()
    
    def estimate_tokens(self, data: dict) -> int:
        return len(json.dumps(data)) // 4
    
    def check_budget(self) -> dict:
        elapsed = time.time() - self.budget.start_time
        return {
            "tokens": self.budget.max_tokens - self.budget.used_tokens,
            "tool_calls": self.budget.max_tool_calls - self.budget.used_tool_calls,
            "time": self.budget.max_time_s - elapsed
        }
    
    def is_exhausted(self) -> bool:
        remaining = self.check_budget()
        return any(v <= 0 for v in remaining.values())
    
    def consume(self, tokens: int = 0, tool_calls: int = 0):
        self.budget.used_tokens += tokens
        self.budget.used_tool_calls += tool_calls


class BaselineRunner:
    def __init__(self, mode: str, seed: int = 42):
        self.mode = mode  # "B0" | "B1" | "B2"
        self.logger = TraceLogger()
        self.seed = seed
        random.seed(seed)
    
    def run_task(self, task: dict) -> dict:
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
            max_tool_calls=100,
            max_time_s=60.0
        )
        tracker = BudgetTracker(budget)
        
        steps = task["steps"]
        fault_injections = task.get("fault_injections", [])
        
        checkpoint = world_state.deep_copy()
        retry_counts = {}  # 每个 step 的重试次数
        
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
                retry_counts[step_idx] = 0
                step_idx += 1
            else:
                # 根据 baseline 模式选择恢复策略
                recovery_action = self._get_recovery_action(
                    result, step_idx, retry_counts, world_state, checkpoint
                )
                event.recovery_action = recovery_action
                self.logger.append(event)
                
                if recovery_action == "fail":
                    # B0: 直接失败
                    return {"task_id": task_id, "status": "failed", "reason": result.error_type}
                
                elif recovery_action == "escalate":
                    self._escalate_human(task_id, step_idx, result.error_type, world_state, tracker)
                    return {"task_id": task_id, "status": "escalated", "reason": result.error_type}
                
                elif recovery_action == "retry":
                    # 重试同一步骤
                    retry_counts[step_idx] = retry_counts.get(step_idx, 0) + 1
                    if self.mode == "B1":
                        # Naive retry: 简单延迟
                        time.sleep(0.05)
                    elif self.mode == "B2":
                        # Rule-based: 指数退避
                        backoff = 0.1 * (2 ** (retry_counts[step_idx] - 1))
                        time.sleep(min(backoff, 0.4))
                    continue
                
                elif recovery_action == "rollback":
                    # B2: 回滚后重试
                    world_state.records = checkpoint.records.copy()
                    world_state.inventory = checkpoint.inventory.copy()
                    world_state.audit_log = checkpoint.audit_log.copy()
                    retry_counts[step_idx] = retry_counts.get(step_idx, 0) + 1
                    continue
        
        # 检查成功
        success = check_success(world_state, task)
        return {
            "task_id": task_id,
            "status": "success" if success else "failed",
            "steps_executed": len(steps)
        }
    
    def _execute_step(self, world_state: WorldState, tool_name: str, params: dict, fault_injection):
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
    
    def _get_recovery_action(self, result, step_idx: int, retry_counts: dict, 
                            world_state: WorldState, checkpoint: WorldState) -> str:
        """根据 baseline 模式决定恢复动作"""
        error_type = result.error_type
        current_retries = retry_counts.get(step_idx, 0)
        
        if self.mode == "B0":
            # B0: No Recovery - 任何错误都失败
            return "fail"
        
        elif self.mode == "B1":
            # B1: Naive Retry - 不区分错误类型，统一重试 <=3 次
            if current_retries < 3:
                return "retry"
            else:
                return "fail"
        
        elif self.mode == "B2":
            # B2: Rule-Based Recovery
            if error_type in ["Timeout", "HTTP_500"]:
                # 可重试错误
                if current_retries < 3:
                    return "retry"
                else:
                    return "escalate"
            
            elif error_type == "Conflict":
                # 冲突：回滚后重试
                if current_retries < 3:
                    return "rollback"
                else:
                    return "escalate"
            
            elif error_type in ["PolicyRejected", "AuthDenied"]:
                # 策略/权限问题：立即升级
                return "escalate"
            
            elif error_type in ["BadRequest", "NotFound", "StateCorruption"]:
                # 不可恢复错误
                return "escalate"
            
            else:
                return "escalate"
        
        return "fail"
    
    def _escalate_human(self, task_id: str, step_idx: int, reason: str, 
                       world_state: WorldState, tracker: BudgetTracker):
        mock_api.create_ticket(
            world_state,
            summary=f"[{self.mode}] Task {task_id} escalated at step {step_idx}: {reason}",
            severity="critical"
        )


def run(tasks_path: str, mode: str, seed: int = 42) -> str:
    """运行 baseline 实验"""
    
    # 加载任务
    tasks = []
    with open(tasks_path, 'r') as f:
        for line in f:
            tasks.append(json.loads(line))
    
    print(f"\n{'='*60}")
    print(f"Running Baseline: {mode}")
    print(f"Tasks: {len(tasks)}, Seed: {seed}")
    print(f"{'='*60}\n")
    
    # 设置随机种子确保可复现
    random.seed(seed)
    
    # 运行任务
    runner = BaselineRunner(mode=mode, seed=seed)
    results = []
    
    for i, task in enumerate(tasks):
        result = runner.run_task(task)
        results.append(result)
        
        status_symbol = "✓" if result["status"] == "success" else "✗"
        print(f"[{i+1:2d}/{len(tasks)}] {status_symbol} {result['task_id']}: {result['status']}")
    
    # 保存轨迹
    traces_path = f"traces_{mode}.jsonl"
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
    print(f"{'='*60}\n")
    
    return traces_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="tasks.jsonl")
    parser.add_argument("--mode", choices=["B0", "B1", "B2"], required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    
    traces_path = run(args.tasks, args.mode, args.seed)
    print(f"Traces saved to: {traces_path}")