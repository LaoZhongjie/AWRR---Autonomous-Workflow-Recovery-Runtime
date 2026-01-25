import json
import argparse
import pandas as pd
from collections import defaultdict


def compute_metrics(traces_path: str, baseline_name: str = None) -> dict:
    """
    从轨迹计算所有指标
    
    修复点：
    - HIR 正确计算为 escalated_tasks / total_tasks
    - MTTR 从实际延迟累加
    - RCO 基于实际 tool_calls
    """
    
    # 加载轨迹
    events = []
    with open(traces_path, 'r') as f:
        for line in f:
            events.append(json.loads(line))
    
    if not events:
        return {}
    
    # 按任务分组
    tasks = defaultdict(list)
    for event in events:
        tasks[event["task_id"]].append(event)
    
    # 指标累积器
    total_tasks = len(tasks)
    completed_tasks = 0
    escalated_tasks = 0
    
    total_errors = 0
    total_recovered = 0
    recovery_times = []  # 恢复耗时（从错误到成功的时间差）
    
    # 成本指标
    baseline_tool_calls = 0
    actual_tool_calls = 0
    
    # UAR
    tasks_with_auth_issues = 0
    
    # 逐任务分析
    for task_id, task_events in tasks.items():
        # 理想情况：5 步完成
        baseline_tool_calls += 5
        actual_tool_calls += len(task_events)
        
        # 检查最终状态
        final_event = task_events[-1]
        
        # WCR: 成功完成的任务
        # 判断标准：最后一个事件是 step_idx=4 且 status=ok
        if final_event["step_idx"] == 4 and final_event["status"] == "ok":
            completed_tasks += 1
        
        # HIR: 升级到人工的任务
        # 判断标准：有 recovery_action="escalate" 的事件
        has_escalation = any(
            e.get("recovery_action") == "escalate" 
            for e in task_events
        )
        if has_escalation:
            escalated_tasks += 1
        
        # RR: Recovery Rate
        error_events = [e for e in task_events if e["status"] == "error"]
        total_errors += len(error_events)
        
        # 追踪每个错误是否成功恢复
        for i, event in enumerate(task_events):
            if event["status"] == "error":
                recovery_action = event.get("recovery_action")
                
                # 检查是否有后续成功
                if recovery_action in ["retry", "rollback"]:
                    # 查找后续同 step 的成功事件
                    for j in range(i+1, len(task_events)):
                        next_event = task_events[j]
                        if (next_event["step_idx"] == event["step_idx"] and 
                            next_event["status"] == "ok"):
                            total_recovered += 1
                            
                            # MTTR: 计算恢复时间（累加中间所有步骤的延迟）
                            recovery_latency = sum(
                                task_events[k]["latency_ms"] 
                                for k in range(i, j+1)
                            )
                            recovery_times.append(recovery_latency)
                            break
        
        # UAR: 权限/策略问题
        auth_errors = [
            e for e in error_events 
            if e.get("error_type") in ["AuthDenied", "PolicyRejected"]
        ]
        if auth_errors:
            tasks_with_auth_issues += 1
    
    # 计算最终指标
    wcr = completed_tasks / total_tasks if total_tasks > 0 else 0
    rr = total_recovered / total_errors if total_errors > 0 else 0.0
    mttr = sum(recovery_times) / len(recovery_times) if recovery_times else 0.0
    
    # RCO: Recovery Cost Overhead
    extra_tool_calls = actual_tool_calls - baseline_tool_calls
    rco = extra_tool_calls / baseline_tool_calls if baseline_tool_calls > 0 else 0
    
    # HIR: Human Intervention Rate
    hir = escalated_tasks / total_tasks if total_tasks > 0 else 0
    
    # UAR: Unauthorized Action Rate
    uar = tasks_with_auth_issues / total_tasks if total_tasks > 0 else 0
    
    metrics = {
        "baseline": baseline_name or "unknown",
        "wcr": wcr,
        "rr": rr,
        "mttr": mttr,
        "rco": rco,
        "hir": hir,
        "uar": uar,
        "total_tasks": total_tasks,
        "completed": completed_tasks,
        "escalated": escalated_tasks,
        "total_errors": total_errors,
        "recovered": total_recovered,
        "baseline_calls": baseline_tool_calls,
        "actual_calls": actual_tool_calls,
        "recovery_count": len(recovery_times)
    }
    
    return metrics


def print_metrics(metrics: dict):
    """打印指标"""
    print(f"\n{'='*70}")
    print(f"Metrics for {metrics['baseline']}")
    print(f"{'='*70}")
    print(f"WCR (Workflow Completion Rate):  {metrics['wcr']:6.2%}  ({metrics['completed']}/{metrics['total_tasks']})")
    print(f"RR  (Recovery Rate):              {metrics['rr']:6.2%}  ({metrics['recovered']}/{metrics['total_errors']})")
    print(f"MTTR (Mean Time To Recovery):     {metrics['mttr']:8.1f} ms")
    print(f"RCO (Recovery Cost Overhead):     {metrics['rco']:6.2%}  (+{metrics['actual_calls']-metrics['baseline_calls']} calls)")
    print(f"HIR (Human Intervention Rate):    {metrics['hir']:6.2%}  ({metrics['escalated']}/{metrics['total_tasks']})")
    print(f"UAR (Unauthorized Action Rate):   {metrics['uar']:6.2%}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", required=True)
    parser.add_argument("--baseline", default="unknown")
    args = parser.parse_args()
    
    metrics = compute_metrics(args.traces, args.baseline)
    print_metrics(metrics)