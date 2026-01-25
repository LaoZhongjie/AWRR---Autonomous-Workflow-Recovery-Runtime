import json
import argparse
import pandas as pd
from collections import defaultdict
from diagnosis import DiagnosisAgent
from state import StepContext, StepResult, TraceEvent


def evaluate_rca(traces_path: str, evaluation_level: str = "event") -> dict:
    """
    评估 RCA (Root Cause Analysis) 准确率
    
    Args:
        traces_path: 轨迹文件路径
        evaluation_level: "event" (每次错误) 或 "task" (每个任务首次错误)
    
    修复点：
    - 使用 injected_fault.layer_gt 作为真实标签（防泄漏）
    - 使用 DiagnosisAgent(mock) 输出作为预测（与 ground truth 分离）
    - 明确区分 event-level 和 task-level 评估
    """
    
    # 加载轨迹
    events = []
    with open(traces_path, 'r') as f:
        for line in f:
            events.append(json.loads(line))
    
    if not events:
        print("No events found in traces")
        return {}
    
    diagnosis_agent = DiagnosisAgent(mode="mock")

    # 按任务分组，保持顺序
    tasks = defaultdict(list)
    for event in events:
        tasks[event["task_id"]].append(event)

    # 提取有诊断的错误事件
    diagnosed_errors = []
    seen_tasks = set()  # 用于 task-level 去重
    
    for task_id, task_events in tasks.items():
        history_events: list[TraceEvent] = []
        for event in task_events:
            if event["status"] != "error" or not event.get("injected_fault"):
                history_events.append(_event_to_trace_event(event))
                continue

            fault_info = event["injected_fault"]
            layer_gt = fault_info.get("layer_gt")
            error_type = event.get("error_type")
            
            if not layer_gt or not error_type:
                history_events.append(_event_to_trace_event(event))
                continue
            
            # Task-level: 只取每个任务的第一个错误
            if evaluation_level == "task":
                if task_id in seen_tasks:
                    history_events.append(_event_to_trace_event(event))
                    continue
                seen_tasks.add(task_id)
            
            step_context = StepContext(
                task_id=event["task_id"],
                step_idx=event["step_idx"],
                step_name=event.get("step_name", ""),
                tool_name=event.get("tool_name", ""),
                params=event.get("params", {}),
                state_hash=event.get("state_hash", ""),
                budget_remaining=event.get("budget", {})
            )
            step_result = StepResult(
                status=event["status"],
                output=None,
                error_type=event.get("error_type"),
                error_msg=event.get("error_msg"),
                error_trace=event.get("error_trace"),
                latency_ms=event.get("latency_ms", 0),
                injected_fault=event.get("injected_fault")
            )
            diagnosis = diagnosis_agent.diagnose(step_context, step_result, history_events)
            layer_pred = diagnosis.layer
            
            diagnosed_errors.append({
                "task_id": event["task_id"],
                "step_idx": event["step_idx"],
                "error_type": error_type,
                "fault_id": fault_info.get("fault_id", "unknown"),
                "layer_gt": layer_gt,
                "layer_pred": layer_pred,
                "recovery_action": event.get("recovery_action"),
                "matched": layer_gt == layer_pred
            })
            history_events.append(_event_to_trace_event(event))
    
    if not diagnosed_errors:
        print(f"No diagnosed errors found (evaluation_level={evaluation_level})")
        return {}
    
    # 计算准确率
    correct = sum(1 for e in diagnosed_errors if e["matched"])
    total = len(diagnosed_errors)
    accuracy = correct / total if total > 0 else 0
    
    # 构建混淆矩阵
    layers = ["transient", "persistent", "semantic", "cascade"]
    confusion_matrix = []
    
    for pred_layer in layers:
        row = {"predicted": pred_layer}
        for gt_layer in layers:
            count = sum(
                1 for e in diagnosed_errors 
                if e["layer_pred"] == pred_layer and e["layer_gt"] == gt_layer
            )
            row[gt_layer] = count
        confusion_matrix.append(row)
    
    confusion_df = pd.DataFrame(confusion_matrix)
    
    # 统计
    gt_distribution = defaultdict(int)
    pred_distribution = defaultdict(int)
    for e in diagnosed_errors:
        gt_distribution[e["layer_gt"]] += 1
        pred_distribution[e["layer_pred"]] += 1
    
    # Per-layer accuracy
    layer_accuracy = {}
    for layer in layers:
        layer_samples = [e for e in diagnosed_errors if e["layer_gt"] == layer]
        if layer_samples:
            layer_correct = sum(1 for e in layer_samples if e["matched"])
            layer_accuracy[layer] = layer_correct / len(layer_samples)
        else:
            layer_accuracy[layer] = None
    
    results = {
        "evaluation_level": evaluation_level,
        "accuracy": accuracy,
        "correct": correct,
        "total": total,
        "confusion_matrix": confusion_df,
        "gt_distribution": dict(gt_distribution),
        "pred_distribution": dict(pred_distribution),
        "layer_accuracy": layer_accuracy,
        "sample_errors": diagnosed_errors[:10]  # 前10个样本
    }
    
    return results


def _event_to_trace_event(event: dict) -> TraceEvent:
    return TraceEvent(
        task_id=event.get("task_id", ""),
        step_idx=event.get("step_idx", 0),
        step_name=event.get("step_name", ""),
        tool_name=event.get("tool_name", ""),
        params=event.get("params", {}),
        status=event.get("status", ""),
        latency_ms=event.get("latency_ms", 0),
        error_type=event.get("error_type"),
        injected_fault=event.get("injected_fault"),
        state_hash=event.get("state_hash", ""),
        budget=event.get("budget", {}),
        recovery_action=event.get("recovery_action")
    )


def print_rca_results(results: dict):
    """打印 RCA 评估结果"""
    
    if not results:
        print("No RCA results to display")
        return
    
    print("\n" + "="*80)
    print(f"RCA (Root Cause Analysis) EVALUATION [{results['evaluation_level'].upper()}-LEVEL]")
    print("="*80)
    
    print(f"\nOverall Accuracy: {results['accuracy']:.2%} ({results['correct']}/{results['total']})")
    
    print(f"\nPer-Layer Accuracy:")
    for layer, acc in sorted(results['layer_accuracy'].items()):
        if acc is not None:
            print(f"  {layer:12s}: {acc:6.2%}")
        else:
            print(f"  {layer:12s}:   N/A  (no samples)")
    
    print(f"\nGround Truth Distribution:")
    for layer, count in sorted(results['gt_distribution'].items()):
        print(f"  {layer:12s}: {count:3d}")
    
    print(f"\nPredicted Distribution:")
    for layer, count in sorted(results['pred_distribution'].items()):
        print(f"  {layer:12s}: {count:3d}")
    
    print(f"\nConfusion Matrix (rows=predicted, cols=actual):")
    print(results['confusion_matrix'].to_string(index=False))
    
    print(f"\n" + "="*80)
    print(f"SAMPLE DIAGNOSED ERRORS (first {min(10, len(results['sample_errors']))})")
    print("="*80)
    
    for i, error in enumerate(results['sample_errors'], 1):
        match_symbol = "✓" if error['matched'] else "✗"
        print(f"\n[{i:2d}] {match_symbol} {error['task_id']} step {error['step_idx']} (fault_id: {error['fault_id']})")
        print(f"     Error: {error['error_type']:15s}  GT: {error['layer_gt']:10s}  Pred: {error['layer_pred']:10s}")
        print(f"     Action: {error['recovery_action'] or 'N/A'}")
    
    print("\n" + "="*80)
    
    # 评估口径说明
    print(f"\nEvaluation Methodology:")
    print(f"  Level: {results['evaluation_level'].upper()}-level")
    if results['evaluation_level'] == "event":
        print(f"  - Each error event (including retries) is evaluated separately")
        print(f"  - Total: {results['total']} error events across all tasks")
    else:
        print(f"  - Only the first error per task is evaluated")
        print(f"  - Total: {results['total']} tasks with errors")
    print(f"  Ground Truth: injected_fault.layer_gt (from fault injection)")
    print(f"  Prediction: DiagnosisAgent.get_ground_truth_layer(error_type)")
    print("="*80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", required=True, help="Path to traces JSONL file")
    parser.add_argument("--level", choices=["event", "task"], default="event",
                       help="Evaluation level: event (all errors) or task (first error per task)")
    args = parser.parse_args()
    
    results = evaluate_rca(args.traces, args.level)
    print_rca_results(results)
