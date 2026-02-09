import argparse
import json
from collections import Counter
from collections import defaultdict
from typing import Tuple

FINAL_OUTCOMES = {"success", "escalated", "failed"}
RECOVERY_ACTIONS = {"retry", "rollback", "rollback_then_retry"}
TOOL_EVENT_TYPES = {"tool_call"}


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

def _normalize_action(action: str | None) -> str | None:
    if not action:
        return None
    if ":" in action:
        return action.split(":", 1)[1]
    return action

def _task_call_counts(task_events) -> Tuple[int, int]:
    actual_calls = 0
    seen_steps = set()
    base_calls = 0
    for e in task_events:
        if e.get("event_type", "tool_call") != "tool_call":
            continue
        actual_calls += 1
        step_idx = e.get("step_idx", 0)
        if step_idx not in seen_steps:
            seen_steps.add(step_idx)
            base_calls += 1
    return base_calls, actual_calls


def _mttr_delta_ms(task_events, err_i: int, ok_i: int) -> float:
    err_ts = task_events[err_i].get("ts_ms")
    ok_ts = task_events[ok_i].get("ts_ms")
    if err_ts is not None and ok_ts is not None and ok_ts >= err_ts:
        return float(ok_ts - err_ts)

    total = 0.0
    for e in task_events[err_i: ok_i + 1]:
        total += float(e.get("latency_ms", 0) or 0)
    return total

def _is_tool_error_event(e: dict) -> bool:
    return (
        e.get("status") == "error"
        and e.get("event_type", "tool_call") in TOOL_EVENT_TYPES
    )

def _first_tool_error_event(task_events: list[dict]) -> dict | None:
    for e in task_events:
        if _is_tool_error_event(e):
            return e
    return None

def _scalar_summary(metrics: dict) -> dict:
    summary = {}
    for k, v in metrics.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            summary[k] = v
    return summary

def compute_metrics(traces_path: str, baseline_name: str | None = None) -> dict:
    """
    从轨迹计算所有指标

    指标定义：
    - WCR: final_outcome == success
    - HIR: final_outcome == escalated 的任务比例
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
    srr_eligible_tasks = 0
    srr_pass_tasks = 0

    tool_calls_total = sum(
        1 for event in events if event.get("event_type", "tool_call") == "tool_call"
    )
    llm_calls = sum(
        1
        for event in events
        if (event.get("diagnosis") or {}).get("source") in ["diagnosis", "llm"]
    )

    rco_base_calls_total = 0
    rco_overhead_calls_total = 0

    tasks_with_auth_issues = 0
    final_reason_counts: Counter[str] = Counter()
    recovery_action_counts: Counter[str] = Counter()

    # Per-task: classify by the first error type observed.
    first_error_type_task_counts: Counter[str] = Counter()
    first_error_type_outcomes: dict[str, Counter[str]] = defaultdict(Counter)

    # Per-event: error-type level recovery breakdown.
    error_type_event_counts: Counter[str] = Counter()
    recovered_error_type_event_counts: Counter[str] = Counter()
    mttr_event_times_by_error_type: dict[str, list[float]] = defaultdict(list)

    for task_events in tasks.values():
        task_events = sorted(task_events, key=lambda e: e.get("ts_ms", 0))
        last_step_idx = max(e.get("step_idx", 0) for e in task_events)
        final_outcome = _infer_final_outcome(task_events, last_step_idx)

        base_calls_task, actual_calls_task = _task_call_counts(task_events)
        rco_base_calls_total += base_calls_task
        rco_overhead_calls_total += max(actual_calls_task - base_calls_task, 0)

        if final_outcome == "success":
            completed_tasks += 1

        if final_outcome == "escalated":
            escalated_tasks += 1

        final_event = next(
            (e for e in reversed(task_events) if e.get("event_type") == "final"),
            None
        )
        if final_event and final_event.get("srr_eligible") is True:
            srr_eligible_tasks += 1
            if final_event.get("srr_pass") is True:
                srr_pass_tasks += 1
        if final_outcome == "escalated":
            final_reason_counts[str(final_event.get("final_reason") or "unknown")] += 1

        error_events = [e for e in task_events if _is_tool_error_event(e)]
        if error_events:
            error_tasks += 1
            if final_outcome == "success":
                recovered_tasks += 1

        first_error = _first_tool_error_event(task_events)
        if first_error:
            et = str(first_error.get("error_type") or "Unknown")
            first_error_type_task_counts[et] += 1
            first_error_type_outcomes[et][final_outcome] += 1

        total_error_events += len(error_events)
        for e in error_events:
            error_type_event_counts[str(e.get("error_type") or "Unknown")] += 1

        for idx, error_event in enumerate(task_events):
            if error_event.get("status") != "error":
                continue
            recovery_action = _normalize_action(error_event.get("recovery_action"))
            if recovery_action:
                recovery_action_counts[recovery_action] += 1
            if recovery_action not in RECOVERY_ACTIONS:
                continue
            if error_event.get("event_type", "tool_call") not in TOOL_EVENT_TYPES:
                continue
            error_step = error_event.get("step_idx", 0)
            error_ts = error_event.get("ts_ms")
            error_type = str(error_event.get("error_type") or "Unknown")
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
                recovered_error_type_event_counts[error_type] += 1
                if error_ts is not None and recovered_event.get("ts_ms") is not None:
                    ok_i = task_events.index(recovered_event)
                    dt = _mttr_delta_ms(task_events, idx, ok_i)
                    mttr_event_times.append(dt)
                    mttr_event_times_by_error_type[error_type].append(dt)


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

    rco = (rco_overhead_calls_total / rco_base_calls_total) if rco_base_calls_total else 0.0

    cpt = tool_calls_total / total_tasks if total_tasks else 0.0
    cps = tool_calls_total / max(completed_tasks, 1)

    hir = escalated_tasks / total_tasks if total_tasks else 0.0
    uar = tasks_with_auth_issues / total_tasks if total_tasks else 0.0
    srr = srr_pass_tasks / srr_eligible_tasks if srr_eligible_tasks else 0.0

    # Build breakdown dicts for inspection / leaderboard details.
    by_first_error_type = {}
    for et, count in first_error_type_task_counts.items():
        outs = first_error_type_outcomes.get(et, Counter())
        succ = outs.get("success", 0)
        esc = outs.get("escalated", 0)
        fail = outs.get("failed", 0)
        by_first_error_type[et] = {
            "tasks": int(count),
            "success": int(succ),
            "escalated": int(esc),
            "failed": int(fail),
            "rr_task": (succ / count) if count else 0.0,
            "hir": (esc / count) if count else 0.0,
        }

    by_error_type_event = {}
    for et, cnt in error_type_event_counts.items():
        rec = recovered_error_type_event_counts.get(et, 0)
        mttrs = mttr_event_times_by_error_type.get(et, [])
        by_error_type_event[et] = {
            "error_events": int(cnt),
            "recovered_events": int(rec),
            "rr_event": (rec / cnt) if cnt else 0.0,
            "mttr_event": (sum(mttrs) / len(mttrs)) if mttrs else 0.0,
        }

    full = {
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
        "srr": srr,
        "srr_eligible": srr_eligible_tasks,
        "srr_pass": srr_pass_tasks,
        "total_tasks": total_tasks,
        "completed": completed_tasks,
        "escalated": escalated_tasks,
        "tool_calls_total": tool_calls_total,
        "baseline_calls": rco_base_calls_total,
        "actual_calls": tool_calls_total,
        "llm_calls": llm_calls,
        "error_tasks": error_tasks,
        "recovered_tasks": recovered_tasks,
        "total_error_events": total_error_events,
        "recovered_error_events": recovered_error_events,
        # Extra breakdowns (non-scalar; used for deeper comparisons)
        "final_reason_counts": dict(final_reason_counts),
        "recovery_action_counts": dict(recovery_action_counts),
        "by_first_error_type": by_first_error_type,
        "by_error_type_event": by_error_type_event,
    }
    # Keep a scalar-only view handy for CSV/table exports.
    full["summary"] = _scalar_summary(full)
    return full


def print_metrics(metrics: dict, details: bool = False, topk: int = 5):
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
    if metrics.get("srr_eligible", 0):
        print(f"SRR (Safe Rollback Rate):          {metrics['srr']:6.2%}")
    print(
        "HIR (Human Intervention Rate):"
        f"    {metrics['hir']:6.2%}  ({metrics['escalated']}/{metrics['total_tasks']})"
    )
    print(f"UAR (Unauthorized Action Rate):    {metrics['uar']:6.2%}")
    if details:
        reasons = metrics.get("final_reason_counts") or {}
        if reasons:
            top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:topk]
            top_str = ", ".join(f"{k}={v}" for k, v in top)
            print(f"Top Escalate Reasons:              {top_str}")
        actions = metrics.get("recovery_action_counts") or {}
        if actions:
            top = sorted(actions.items(), key=lambda kv: (-kv[1], kv[0]))[:topk]
            top_str = ", ".join(f"{k}={v}" for k, v in top)
            print(f"Top Recovery Actions:              {top_str}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", required=True)
    parser.add_argument("--baseline", default="unknown")
    parser.add_argument("--details", action="store_true", help="Print extra breakdowns")
    args = parser.parse_args()
    
    metrics = compute_metrics(args.traces, args.baseline)
    print_metrics(metrics, details=args.details)
