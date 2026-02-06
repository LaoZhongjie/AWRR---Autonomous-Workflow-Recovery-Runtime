import argparse
import json
from typing import List


def _load_events(path: str) -> dict:
    tasks = {}
    with open(path, "r") as f:
        for line in f:
            event = json.loads(line)
            tasks.setdefault(event["task_id"], []).append(event)
    # keep input order; no sorting to preserve traces
    return tasks


def _format_event(event: dict) -> str:
    payload = {
        "event_type": event.get("event_type"),
        "step_idx": event.get("step_idx"),
        "step_name": event.get("step_name"),
        "tool_name": event.get("tool_name"),
        "status": event.get("status"),
        "error_type": event.get("error_type"),
        "recovery_action": event.get("recovery_action"),
        "diagnosis_source": (event.get("diagnosis") or {}).get("source"),
    }
    return json.dumps(payload, ensure_ascii=False)


def extract_cases(traces_path: str, k: int) -> List[str]:
    tasks = _load_events(traces_path)
    outputs = []
    for task_id, events in tasks.items():
        for idx, event in enumerate(events):
            recovery_action = event.get("recovery_action") or ""
            if not recovery_action.startswith("memory:"):
                continue
            diagnosis = event.get("diagnosis") or {}
            signature = diagnosis.get("signature")
            confidence = diagnosis.get("confidence")
            chosen_action = recovery_action.split(":", 1)[1]

            header = (
                f"task_id={task_id} step={event.get('step_name')} "
                f"error_type={event.get('error_type')} action={chosen_action} "
                f"confidence={confidence} signature={signature}"
            )
            context_start = max(0, idx - 3)
            context_end = min(len(events), idx + 4)
            context_lines = [
                _format_event(e) for e in events[context_start:context_end]
            ]
            block = "\n".join([header, *context_lines])
            outputs.append(block)
            if len(outputs) >= k:
                return outputs
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", default="traces_B4.jsonl")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    cases = extract_cases(args.traces, args.k)
    text = "\n\n".join(cases)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"Saved {len(cases)} cases to: {args.out}")
    else:
        print(text)
