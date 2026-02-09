import argparse
import json
import random
from constants import SEED


# Workflow templates (more steps, reuse limited step names for B4 learning)
WORKFLOW_TEMPLATES = [
    {
        "name": "saga_payment",
        "steps": [
            ("get_record", {"record_id": "{rec}"}),
            ("auth_check", {"record_id": "{rec}"}),
            ("lock_inventory", {"item_id": "widget", "qty": "{qty}"}),
            ("process_payment", {"order_id": "{rec}", "amount": "{amt}"}),
            ("update_record", {"record_id": "{rec}", "patch": {"status": "approved"}}),
            ("notify_user", {"record_id": "{rec}"}),
            ("commit", {}),
        ],
    },
    {
        "name": "double_write",
        "steps": [
            ("get_record", {"record_id": "{rec}"}),
            # Keep policy_check in the flow, but avoid generating non-injected hard failures.
            ("policy_check", {"action": "approve", "context": {"required_inventory": {"widget": 1}}}),
            ("update_record", {"record_id": "{rec}", "patch": {"status": "approved"}}),
            ("write_audit", {"record_id": "{rec}"}),
            ("commit", {}),
        ],
    },
    {
        "name": "refund_flow",
        "steps": [
            ("get_record", {"record_id": "{rec}"}),
            ("auth_check", {"record_id": "{rec}"}),
            ("process_payment", {"order_id": "{rec}", "amount": "{amt}"}),
            ("refund_payment", {"order_id": "{rec}", "amount": "{amt}"}),
            ("update_record", {"record_id": "{rec}", "patch": {"status": "approved"}}),
            ("commit", {}),
        ],
    },
]


# Fault templates with effect hints
FAULT_TEMPLATES_BALANCED = [
    # transient: retry likely succeeds
    {"name": "transient_read", "steps": ["get_record"], "types": ["Timeout", "HTTP_500"], "prob": 0.30, "weight": 0.15, "mode": "per_attempt", "scenario": "transient"},
    {"name": "transient_write_safe", "steps": ["lock_inventory", "process_payment", "update_record"], "types": ["Timeout", "HTTP_500"], "prob": 0.22, "weight": 0.10, "mode": "per_attempt", "scenario": "transient_safe"},
    # unsafe writes：重试可能成功，但也可能更糟（交给策略/诊断区分）
    {"name": "transient_write_unsafe", "steps": ["lock_inventory", "process_payment", "update_record"], "types": ["Timeout", "HTTP_500"], "prob": 0.30, "weight": 0.20, "mode": "per_attempt", "scenario": "transient_unsafe"},
    # sticky/incident: repeated failures (dependency down)
    {"name": "sticky_incident", "steps": ["process_payment", "update_record", "commit"], "types": ["HTTP_500", "Timeout"], "prob": 0.45, "weight": 0.08, "mode": "persistent", "scenario": "incident"},
    {"name": "dep_down", "steps": ["get_record", "process_payment", "update_record", "commit"], "types": ["HTTP_500"], "prob": 0.35, "weight": 0.06, "mode": "persistent", "scenario": "dep_down"},
    # semantic/auth: retry无效（持久），另加最终一致性 NotFound（低概率）
    {"name": "semantic_auth", "steps": ["auth_check", "policy_check", "update_record"], "types": ["PolicyRejected", "BadRequest", "AuthDenied", "NotFound"], "prob": 1.0, "weight": 0.12, "mode": "persistent", "scenario": "semantic"},
    {"name": "eventual_notfound", "steps": ["get_record", "update_record"], "types": ["NotFound"], "prob": 0.20, "weight": 0.05, "mode": "per_attempt", "scenario": "eventual_consistency"},
    # cascade/consistency: needs rollback/compensate
    {"name": "conflict_stateful", "steps": ["process_payment", "update_record", "commit"], "types": ["Conflict"], "prob": 0.45, "weight": 0.22, "mode": "stateful_conflict", "scenario": "cascade_conflict"},
    {"name": "state_corruption", "steps": ["update_record", "commit"], "types": ["StateCorruption"], "prob": 0.15, "weight": 0.08, "mode": "persistent", "scenario": "state_corruption"},
]

FAULT_TEMPLATES_SEPARATION = [
    # Goal: amplify separation so that B4/B3 >> B2 >> B1
    # - Conflict that requires rollback (B2/B3/B4 can handle; B1 cannot)
    # - Eventual-consistency NotFound that should be retried (B3/B4 can handle; B2 escalates)
    # - A small amount of easy transient noise to keep MTTR/RCO meaningful
    {
        "name": "conflict_stateful",
        "steps": ["process_payment", "update_record", "commit"],
        "prefer_steps": ["update_record"],
        "types": ["Conflict"],
        "prob": 0.50,
        "weight": 0.55,
        "mode": "stateful_conflict",
        "scenario": "cascade_conflict",
        "force_first_attempt": True,
    },
    {
        "name": "eventual_notfound",
        "steps": ["get_record"],
        "prefer_steps": ["get_record"],
        "types": ["NotFound"],
        "prob": 1.0,
        "weight": 0.35,
        "mode": "once",
        "scenario": "eventual_consistency",
    },
    {
        "name": "transient_read",
        "steps": ["get_record"],
        "prefer_steps": ["get_record"],
        "types": ["Timeout", "HTTP_500"],
        "prob": 1.0,
        "weight": 0.10,
        "mode": "once",
        "scenario": "transient",
    },
]


def _choose_workflow() -> dict:
    return random.choice(WORKFLOW_TEMPLATES)


def _choose_fault_template(fault_templates: list[dict]) -> dict:
    weights = [tpl["weight"] for tpl in fault_templates]
    return random.choices(fault_templates, weights=weights, k=1)[0]


def _get_fault_templates(profile: str) -> list[dict]:
    if profile == "balanced":
        return FAULT_TEMPLATES_BALANCED
    if profile == "separation":
        return FAULT_TEMPLATES_SEPARATION
    raise ValueError(f"Unknown fault profile: {profile}")


def _instantiate_steps(template: dict, rec: str, qty: int, amt: int) -> list[dict]:
    steps = []
    for idx, (name, params) in enumerate(template["steps"]):
        # Replace placeholders
        p = json.loads(json.dumps(params))
        def _replace(obj):
            if isinstance(obj, str):
                if obj == "{rec}":
                    return rec
                if obj == "{qty}":
                    return qty
                if obj == "{amt}":
                    return amt
                # Fallback: do string substitution for mixed strings.
                return obj.replace("{rec}", rec).replace("{qty}", str(qty)).replace("{amt}", str(amt))
            if isinstance(obj, dict):
                return {k: _replace(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_replace(v) for v in obj]
            return obj
        p = _replace(p)
        steps.append({
            "step_idx": idx,
            "step_name": name,
            "tool_name": name,
            "params": p
        })
    return steps


def generate_tasks(
    n: int = 50,
    seed: int = SEED,
    shuffle: bool = True,
    fault_profile: str = "balanced",
) -> list[dict]:
    random.seed(seed)
    tasks = []
    fault_templates = _get_fault_templates(fault_profile)

    for i in range(n):
        task_id = f"T{i+1:03d}"
        record_id = f"REC{i+1}"
        item_qty = random.randint(1, 3)
        amount = random.randint(100, 500)

        initial_world_state = {
            "records": {
                record_id: {
                    "status": "pending",
                    "value": amount,
                    "payment_status": "unpaid"
                }
            },
            "inventory": {"widget": random.randint(8, 20)},
            "audit_log": []
        }

        workflow = _choose_workflow()
        steps = _instantiate_steps(workflow, record_id, item_qty, amount)

        fault_injections = []

        # Always inject one fault (no-fault prob = 0) to stress recovery paths
        tpl = _choose_fault_template(fault_templates)
        candidate_steps = [s for s in steps if s["step_name"] in tpl["steps"]]
        if candidate_steps:
            preferred = tpl.get("prefer_steps")
            if preferred:
                preferred_candidates = [s for s in candidate_steps if s["step_name"] in preferred]
                if preferred_candidates:
                    candidate_steps = preferred_candidates

            step = random.choice(candidate_steps)
            fi = {
                "step_idx": step["step_idx"],
                "fault_type": random.choice(tpl["types"]),
                "prob": tpl["prob"],
                "fault_id": f"F{i+1}",
                "scenario": tpl["scenario"],
                "mode": tpl.get("mode", "once"),
            }
            # Pass through any extra knobs used by the fault injector.
            for k in ["force_first_attempt", "layer_override"]:
                if k in tpl:
                    fi[k] = tpl[k]
            fault_injections.append(fi)

        success_condition = {
            "type": "record_status",
            "record_id": record_id,
            "expected_status": "approved"
        }

        tasks.append({
            "task_id": task_id,
            "initial_world_state": initial_world_state,
            "steps": steps,
            "fault_injections": fault_injections,
            "success_condition": success_condition
        })

    if shuffle:
        random.shuffle(tasks)

    return tasks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", default="tasks.jsonl")
    parser.add_argument(
        "--fault-profile",
        choices=["balanced", "separation"],
        default="balanced",
        help="Fault mix preset. 'separation' is tuned to make B4/B3 >> B2 >> B1.",
    )
    parser.add_argument("--no-shuffle", action="store_false", dest="shuffle", help="Disable shuffling tasks")
    parser.set_defaults(shuffle=True)
    args = parser.parse_args()

    tasks = generate_tasks(
        n=args.n,
        seed=args.seed,
        shuffle=args.shuffle,
        fault_profile=args.fault_profile,
    )

    with open(args.out, 'w') as f:
        for task in tasks:
            f.write(json.dumps(task) + '\n')

    print(f"Generated {len(tasks)} tasks -> {args.out}")

    fault_dist = {}
    for task in tasks:
        for fi in task.get("fault_injections", []):
            ft = fi["fault_type"]
            fault_dist[ft] = fault_dist.get(ft, 0) + 1

    print(f"\nFault distribution (profile={args.fault_profile}):")
    for ft, count in sorted(fault_dist.items()):
        print(f"  {ft}: {count}")
    print(f"  No fault: {sum(1 for t in tasks if not t.get('fault_injections'))}")
