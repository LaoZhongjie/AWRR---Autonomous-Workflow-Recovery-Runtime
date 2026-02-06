import argparse
import json
import os
from typing import List, Tuple

from baselines import BaselineRunner


def _load_tasks(path: str) -> List[dict]:
    tasks = []
    with open(path, "r") as f:
        for line in f:
            tasks.append(json.loads(line))
    return tasks


def _infer_final_outcome(task_events: List[dict], last_step_idx: int) -> str:
    for event in reversed(task_events):
        outcome = event.get("final_outcome")
        if outcome in {"success", "failed", "escalated"}:
            return outcome
    final_event = task_events[-1]
    if final_event.get("recovery_action") == "escalate":
        return "escalated"
    if final_event.get("status") == "ok" and final_event.get("step_idx") == last_step_idx:
        return "success"
    return "failed"


def _batch_error_counts(batch_events: List[dict]) -> Tuple[int, int]:
    tasks = {}
    for event in batch_events:
        tasks.setdefault(event["task_id"], []).append(event)

    error_tasks = 0
    recovered_tasks = 0
    for task_events in tasks.values():
        task_events = sorted(task_events, key=lambda e: e.get("ts_ms", 0))
        last_step_idx = max(e.get("step_idx", 0) for e in task_events)
        final_outcome = _infer_final_outcome(task_events, last_step_idx)
        error_events = [
            e
            for e in task_events
            if e.get("status") == "error" and e.get("event_type", "tool_call") == "tool_call"
        ]
        if error_events:
            error_tasks += 1
            if final_outcome == "success":
                recovered_tasks += 1
    return error_tasks, recovered_tasks


def run_learning_eval(
    tasks_path: str,
    seed: int,
    batch_size: int,
    diagnosis_mode: str,
    memory_path: str,
    out_history: str,
    out_traces: str,
):
    tasks = _load_tasks(tasks_path)
    if not tasks:
        raise ValueError("No tasks loaded")

    if memory_path and os.path.exists(memory_path):
        os.remove(memory_path)

    runner = BaselineRunner(
        mode="B4",
        seed=seed,
        diagnosis_mode=diagnosis_mode,
        memory_path=memory_path,
    )

    total_error_tasks = 0
    total_recovered_tasks = 0
    learning_curve = []

    total_batches = (len(tasks) + batch_size - 1) // batch_size
    for batch_idx in range(total_batches):
        start_event_idx = len(runner.logger.events)
        start = batch_idx * batch_size
        end = min(start + batch_size, len(tasks))
        for task in tasks[start:end]:
            runner.run_task(task)

        end_event_idx = len(runner.logger.events)
        batch_events = [e.to_dict() for e in runner.logger.events[start_event_idx:end_event_idx]]
        batch_error_tasks, batch_recovered_tasks = _batch_error_counts(batch_events)
        total_error_tasks += batch_error_tasks
        total_recovered_tasks += batch_recovered_tasks

        rr_batch = batch_recovered_tasks / batch_error_tasks if batch_error_tasks else 0.0
        rr_cumulative = (
            total_recovered_tasks / total_error_tasks if total_error_tasks else 0.0
        )
        learning_curve.append(
            {
                "episode": batch_idx + 1,
                "rr_batch": rr_batch,
                "rr_cumulative": rr_cumulative,
                "tasks_seen": end,
            }
        )

        print(
            f"[Batch {batch_idx + 1}/{total_batches}] RR_batch={rr_batch:.2%} "
            f"RR_cumulative={rr_cumulative:.2%} tasks_seen={end}"
        )

    earliest = None
    for item in learning_curve:
        if item["rr_cumulative"] >= 0.8:
            earliest = item["episode"]
            break

    with open(out_history, "w") as f:
        json.dump(learning_curve, f, indent=2)

    runner.logger.flush_jsonl(out_traces)

    print("\nLearning efficiency summary:")
    if earliest is None:
        print("  RR>=0.8 not reached")
    else:
        print(f"  Earliest batch with RR>=0.8: {earliest}")
    print(f"  Learning curve saved to: {out_history}")
    print(f"  Traces saved to: {out_traces}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="tasks.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--diagnosis-mode", choices=["mock", "llm"], default="mock")
    parser.add_argument("--memory", default="memory_bank.json")
    parser.add_argument("--out-history", default="learning_curve.json")
    parser.add_argument("--out-traces", default="traces_B4_learning.jsonl")
    args = parser.parse_args()

    run_learning_eval(
        tasks_path=args.tasks,
        seed=args.seed,
        batch_size=args.batch_size,
        diagnosis_mode=args.diagnosis_mode,
        memory_path=args.memory,
        out_history=args.out_history,
        out_traces=args.out_traces,
    )
