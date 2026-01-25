import json
import random
from constants import SEED


def generate_tasks(n: int = 50, seed: int = SEED) -> list[dict]:
    random.seed(seed)
    tasks = []
    
    fault_types = [
        "Timeout",
        "HTTP_500",
        "Conflict",
        "PolicyRejected",
        "StateCorruption"
    ]

    
    for i in range(n):
        task_id = f"T{i+1:03d}"
        
        # 初始状态
        initial_world_state = {
            "records": {
                f"REC{i+1}": {
                    "status": "pending",
                    "value": random.randint(100, 500),
                    "payment_status": "unpaid"
                }
            },
            "inventory": {
                "widget": random.randint(8, 20)
            },
            "audit_log": []
        }
        
        record_id = f"REC{i+1}"
        
        # 5 步固定流程 (Saga: lock inventory -> process payment)
        item_qty = random.randint(1, 3)
        amount = random.randint(100, 250)
        steps = [
            {
                "step_idx": 0,
                "step_name": "get_record",
                "tool_name": "get_record",
                "params": {"record_id": record_id}
            },
            {
                "step_idx": 1,
                "step_name": "lock_inventory",
                "tool_name": "lock_inventory",
                "params": {"item_id": "widget", "qty": item_qty}
            },
            {
                "step_idx": 2,
                "step_name": "process_payment",
                "tool_name": "process_payment",
                "params": {"order_id": record_id, "amount": amount}
            },
            {
                "step_idx": 3,
                "step_name": "update_record",
                "tool_name": "update_record",
                "params": {
                    "record_id": record_id,
                    "patch": {"status": "approved"}
                }
            },
            {
                "step_idx": 4,
                "step_name": "commit",
                "tool_name": "commit",
                "params": {}
            }
        ]

        # 故障注入策略：至少 50 个任务含库存/支付故障（step 1/2）
        fault_injections = []

        # Phase3 关键：大多数任务必须在 lock_inventory 之后失败，且走 rollback（从而触发 saga 补偿）
        # 做法：80% 的任务固定注入 Conflict（runner 会 rollback），并把故障打在 step 2/3（payment/update），保证 lock 成功发生副作用。
        if i < int(0.8 * n):
            fault_injections.append({
                "step_idx": random.choice([2, 3]),   # 2=process_payment, 3=update_record（按你实际 step 定义）
                "fault_type": "Conflict",            # 触发 rollback -> SRR eligible
                "prob": 1.0,
                "fault_id": f"F{i+1}",
                "scenario": "inventory_payment"
            })
        else:
            # 剩下 20% 保持多样性（但仍然发生在 lock 之后）
            fault_type = fault_types[i % len(fault_types)]
            fault_injections.append({
                "step_idx": random.choice([2, 3]),
                "fault_type": fault_type,
                "prob": 0.9,
                "fault_id": f"F{i+1}",
                "scenario": "inventory_payment"
            })

        
        # 成功条件
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
    
    return tasks


if __name__ == "__main__":
    tasks = generate_tasks(n=100, seed=42)
    
    with open("tasks.jsonl", 'w') as f:
        for task in tasks:
            f.write(json.dumps(task) + '\n')
    
    print(f"Generated {len(tasks)} tasks -> tasks.jsonl")
    
    # 统计故障分布
    fault_dist = {}
    for task in tasks:
        for fi in task.get("fault_injections", []):
            ft = fi["fault_type"]
            fault_dist[ft] = fault_dist.get(ft, 0) + 1
    
    print(f"\nFault distribution:")
    for ft, count in sorted(fault_dist.items()):
        print(f"  {ft}: {count}")
    print(f"  No fault: {sum(1 for t in tasks if not t.get('fault_injections'))}")
