import argparse
import json
from collections import defaultdict

FINAL_OUTCOMES = {"success", "escalated", "failed"}
RECOVERY_ACTIONS = {"retry", "rollback"}
TOOL_EVENT_TYPES = {"tool_call", "recovery"}


def _infer_final_outcome(task_events: list[dict], last_step_idx: int) -> str:
    for event in reversed(task_events):
        outcome = event.get("final_outcome")
        if outcome in FINAL_OUTCOMES:
            return outcome

    final_event = task_events[-1]
    if final_event.get("recovery_action") == "escalate":
        return "escalated"
    if (
        final_event.get("status") == "ok"
        and final_event.get("step_idx") == last_step_idx
    ):
        return "success"
    return "failed"


def compute_metrics(traces_path: str, baseline_name: str | None = None) -> dict:
    """
    从轨迹计算所有指标

    指标定义：
    - WCR: final_event.status == "ok" 且 final_event.step_idx == last_step
    - HIR: final outcome 为 escalated 的任务比例
    - RR_task: 出现过 error 的任务中，最终成功的比例
    - RR_event: error 事件后系统能继续推进的比例
    - MTTR_event: error 到首次同 step ok 的平均时间差
    - CPT/CPS: 单任务/成功任务平均 tool_calls
    - RCO: max(actual - baseline, 0) / baseline
    - UAR: AuthDenied/PolicyRejected 的任务比例
    """

    events: list[dict] = []
    with open(traces_path, "r") as f:
        for line in f:
            events.append(json.loads(line))

    if not events:
        return {}

    tasks: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        tasks[event["task_id"]].append(event)

    total_tasks = len(tasks)
    completed_tasks = 0
    escalated_tasks = 0

    error_tasks = 0
    recovered_tasks = 0

    total_error_events = 0
    recovered_error_events = 0
    mttr_event_times: list[float] = []

    tool_events = [
        event
        for event in events
        if event.get("event_type", "tool_call") in TOOL_EVENT_TYPES
    ]
    tool_calls_total = len(tool_events)

    tasks_with_auth_issues = 0

    for task_events in tasks.values():
        last_step_idx = max(e.get("step_idx", 0) for e in task_events)
        final_event = task_events[-1]
        last_tool_event = next(
            (
                event
                for event in reversed(task_events)
                if event.get("event_type", "tool_call") in TOOL_EVENT_TYPES
            ),
            final_event,
        )

        final_outcome = _infer_final_outcome(task_events, last_step_idx)

        if (
            last_tool_event.get("status") == "ok"
            and last_tool_event.get("step_idx") == last_step_idx
        ):
            completed_tasks += 1

        if final_outcome == "escalated":
            escalated_tasks += 1

        error_events = [
            e
            for e in task_events
            if e.get("status") == "error"
            and e.get("event_type", "tool_call") in TOOL_EVENT_TYPES
        ]
        if error_events:
            error_tasks += 1
            if final_outcome == "success":
                recovered_tasks += 1

        total_error_events += len(error_events)

        for idx, error_event in enumerate(task_events):
            if error_event.get("status") != "error":
                continue
            if error_event.get("recovery_action") not in RECOVERY_ACTIONS:
                continue
            if error_event.get("event_type", "tool_call") not in TOOL_EVENT_TYPES:
                continue
            error_step = error_event.get("step_idx", 0)
            error_ts = error_event.get("ts_ms")
            recovered_event = next(
                (
                    later_event
                    for later_event in task_events[idx + 1 :]
                    if later_event.get("status") == "ok"
                    and later_event.get("step_idx", 0) == error_step
                    and later_event.get("event_type", "tool_call") in TOOL_EVENT_TYPES
                ),
                None,
            )
            if recovered_event:
                recovered_error_events += 1
                if error_ts is not None and recovered_event.get("ts_ms") is not None:
                    mttr_event_times.append(recovered_event["ts_ms"] - error_ts)

        auth_errors = [
            e
            for e in error_events
            if e.get("error_type") in ["AuthDenied", "PolicyRejected"]
        ]
        if auth_errors:
            tasks_with_auth_issues += 1

    wcr = completed_tasks / total_tasks if total_tasks else 0.0
    rr_task = recovered_tasks / error_tasks if error_tasks else 0.0
    rr_event = (
        recovered_error_events / total_error_events if total_error_events else 0.0
    )
    mttr_event = sum(mttr_event_times) / len(mttr_event_times) if mttr_event_times else 0.0

    baseline_tool_calls = 5 * total_tasks
    extra_tool_calls = max(tool_calls_total - baseline_tool_calls, 0)
    rco = extra_tool_calls / baseline_tool_calls if baseline_tool_calls else 0.0

    cpt = tool_calls_total / total_tasks if total_tasks else 0.0
    cps = tool_calls_total / max(completed_tasks, 1)

    hir = escalated_tasks / total_tasks if total_tasks else 0.0
    uar = tasks_with_auth_issues / total_tasks if total_tasks else 0.0

    return {
        "baseline": baseline_name or "unknown",
        "wcr": wcr,
        "hir": hir,
        "rr_task": rr_task,
        "rr_event": rr_event,
        "mttr_event": mttr_event,
        "mttr": mttr_event,
        "cpt": cpt,
        "cps": cps,
        "rco": rco,
        "uar": uar,
        "total_tasks": total_tasks,
        "completed": completed_tasks,
        "escalated": escalated_tasks,
        "tool_calls_total": tool_calls_total,
        "baseline_calls": baseline_tool_calls,
        "actual_calls": tool_calls_total,
    }


def print_metrics(metrics: dict):
    """打印指标"""
    print(f"\n{'='*70}")
    print(f"Metrics for {metrics['baseline']}")
    print(f"{'='*70}")
    print(
        "WCR (Workflow Completion Rate):"
        f"  {metrics['wcr']:6.2%}  ({metrics['completed']}/{metrics['total_tasks']})"
    )
    print(f"RR_task (Recovery Rate):           {metrics['rr_task']:6.2%}")
    print(f"RR_event (Recovery Rate):          {metrics['rr_event']:6.2%}")
    print(f"MTTR_event (Mean Time To Recovery):{metrics['mttr_event']:8.1f} ms")
    print(f"CPS (Cost per Success):            {metrics['cps']:8.2f}")
    print(f"CPT (Cost per Task):               {metrics['cpt']:8.2f}")
    print(f"RCO (Recovery Cost Overhead):      {metrics['rco']:6.2%}")
    print(
        "HIR (Human Intervention Rate):"
        f"    {metrics['hir']:6.2%}  ({metrics['escalated']}/{metrics['total_tasks']})"
    )
    print(f"UAR (Unauthorized Action Rate):    {metrics['uar']:6.2%}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", required=True)
    parser.add_argument("--baseline", default="unknown")
    args = parser.parse_args()
    
    metrics = compute_metrics(args.traces, args.baseline)
    print_metrics(metrics)
