import json
import argparse
import pandas as pd
from collections import defaultdict


def compute_metrics(traces_path: str, baseline_name: str = None) -> dict:
    """从轨迹计算所有指标"""
    
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
    total_errors = 0
    total_recovered = 0
    recovery_times = []
    
    # 成本指标
    baseline_tool_calls = 0  # 无错误情况下的理想 tool_calls
    actual_tool_calls = 0
    
    # HIR: Human Intervention Rate
    tasks_with_tickets = 0
    
    # UAR: Unauthorized Action Rate
    tasks_with_auth_issues = 0
    
    # 逐任务分析
    for task_id, task_events in tasks.items():
        # 理想情况：5 步完成（无错误）
        baseline_tool_calls += 5
        actual_tool_calls += len(task_events)
        
        # WCR: 是否完成
        final_event = task_events[-1]
        if final_event.get("recovery_action") != "escalate":
            # 检查是否所有步骤都成功
            if final_event["status"] == "ok" and final_event["step_idx"] >= 4:
                completed_tasks += 1
        
        # RR: Recovery Rate
        error_events = [e for e in task_events if e["status"] == "error"]
        total_errors += len(error_events)
        
        recovered_events = [e for e in error_events 
                          if e.get("recovery_action") in ["retry", "rollback"]]
        
        # 检查恢复是否成功（后续有 ok 事件）
        for i, event in enumerate(task_events):
            if event["status"] == "error" and event.get("recovery_action") in ["retry", "rollback"]:
                # 查找后续同 step_idx 的成功事件
                for j in range(i+1, len(task_events)):
                    if (task_events[j]["step_idx"] == event["step_idx"] and 
                        task_events[j]["status"] == "ok"):
                        total_recovered += 1
                        recovery_times.append(task_events[j]["latency_ms"])
                        break
        
        # HIR: 是否创建了 ticket
        ticket_events = [e for e in task_events if e["tool_name"] == "create_ticket"]
        if ticket_events:
            tasks_with_tickets += 1
        
        # UAR: 是否有权限/策略问题
        auth_errors = [e for e in error_events 
                      if e.get("error_type") in ["AuthDenied", "PolicyRejected"]]
        if auth_errors:
            tasks_with_auth_issues += 1
    
    # 计算最终指标
    wcr = completed_tasks / total_tasks if total_tasks > 0 else 0
    rr = total_recovered / total_errors if total_errors > 0 else 1.0
    mttr = sum(recovery_times) / len(recovery_times) if recovery_times else 0
    
    # RCO: Recovery Cost Overhead (额外成本比例)
    extra_tool_calls = actual_tool_calls - baseline_tool_calls
    rco = extra_tool_calls / baseline_tool_calls if baseline_tool_calls > 0 else 0
    
    # HIR: Human Intervention Rate
    hir = tasks_with_tickets / total_tasks if total_tasks > 0 else 0
    
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
        "total_errors": total_errors,
        "recovered": total_recovered,
        "baseline_calls": baseline_tool_calls,
        "actual_calls": actual_tool_calls
    }
    
    return metrics


def print_metrics(metrics: dict):
    """打印指标"""
    print(f"\n{'='*60}")
    print(f"Metrics for {metrics['baseline']}")
    print(f"{'='*60}")
    print(f"WCR (Workflow Completion Rate):  {metrics['wcr']:6.2%}")
    print(f"RR  (Recovery Rate):              {metrics['rr']:6.2%}")
    print(f"MTTR (Mean Time To Recovery):     {metrics['mttr']:6.1f} ms")
    print(f"RCO (Recovery Cost Overhead):     {metrics['rco']:6.2%}")
    print(f"HIR (Human Intervention Rate):    {metrics['hir']:6.2%}")
    print(f"UAR (Unauthorized Action Rate):   {metrics['uar']:6.2%}")
    print(f"{'='*60}")
    print(f"Tasks: {metrics['completed']}/{metrics['total_tasks']} completed")
    print(f"Errors: {metrics['recovered']}/{metrics['total_errors']} recovered")
    print(f"Tool calls: {metrics['actual_calls']} (baseline: {metrics['baseline_calls']})")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", required=True)
    parser.add_argument("--baseline", default="unknown")
    args = parser.parse_args()
    
    metrics = compute_metrics(args.traces, args.baseline)
    print_metrics(metrics)