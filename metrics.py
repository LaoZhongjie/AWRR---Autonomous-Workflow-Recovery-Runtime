import json
import argparse
import pandas as pd
from collections import defaultdict


def compute_metrics(traces_path: str):
    """从轨迹计算指标"""
    
    # 加载轨迹
    events = []
    with open(traces_path, 'r') as f:
        for line in f:
            events.append(json.loads(line))
    
    # 按任务分组
    tasks = defaultdict(list)
    for event in events:
        tasks[event["task_id"]].append(event)
    
    # 计算指标
    metrics = []
    
    for task_id, task_events in tasks.items():
        # WCR: Workflow Completion Rate
        final_event = task_events[-1]
        completed = final_event["recovery_action"] != "escalate" if final_event["recovery_action"] else True
        
        # RR: Recovery Rate (成功恢复的比例)
        error_events = [e for e in task_events if e["status"] == "error"]
        recovered_events = [e for e in error_events if e["recovery_action"] in ["retry", "rollback"]]
        recovery_rate = len(recovered_events) / len(error_events) if error_events else 1.0
        
        # MTTR: Mean Time To Recovery (平均恢复时间, ms)
        recovery_times = []
        for i, event in enumerate(task_events):
            if event["status"] == "error" and event["recovery_action"] in ["retry", "rollback"]:
                # 查找下一个成功事件
                for j in range(i+1, len(task_events)):
                    if task_events[j]["status"] == "ok":
                        recovery_times.append(task_events[j]["latency_ms"])
                        break
        
        mttr = sum(recovery_times) / len(recovery_times) if recovery_times else 0
        
        # 故障类型统计
        fault_types = [e["injected_fault"]["fault_type"] for e in task_events if e["injected_fault"]]
        
        metrics.append({
            "task_id": task_id,
            "completed": completed,
            "steps": len(task_events),
            "errors": len(error_events),
            "recovered": len(recovered_events),
            "recovery_rate": f"{recovery_rate:.2%}",
            "mttr_ms": f"{mttr:.1f}",
            "fault_types": ",".join(fault_types) if fault_types else "none"
        })
    
    # 整体指标
    total_tasks = len(metrics)
    wcr = sum(1 for m in metrics if m["completed"]) / total_tasks
    avg_rr = sum(float(m["recovery_rate"].strip('%')) for m in metrics) / total_tasks / 100
    avg_mttr = sum(float(m["mttr_ms"]) for m in metrics) / total_tasks
    
    print("\n=== Overall Metrics ===")
    print(f"Workflow Completion Rate (WCR): {wcr:.2%}")
    print(f"Average Recovery Rate (RR): {avg_rr:.2%}")
    print(f"Average MTTR: {avg_mttr:.1f} ms")
    
    print("\n=== Per-Task Metrics ===")
    df = pd.DataFrame(metrics)
    print(df.to_string(index=False))
    
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", default="traces.jsonl")
    args = parser.parse_args()
    
    compute_metrics(args.traces)