import json
import random
from state import WorldState


def generate_tasks(n: int = 20, seed: int = 42) -> list[dict]:
    random.seed(seed)
    tasks = []
    
    fault_types = [
        "Timeout", "HTTP_500", "BadRequest", "AuthDenied",
        "NotFound", "Conflict", "PolicyRejected", "StateCorruption"
    ]
    
    for i in range(n):
        task_id = f"T{i+1:03d}"
        
        # 初始状态
        initial_world_state = {
            "records": {
                f"REC{i+1}": {"status": "pending", "value": random.randint(100, 500)}
            },
            "inventory": {
                "credits": random.randint(500, 1000),
                "tokens": random.randint(100, 300)
            },
            "audit_log": []
        }
        
        record_id = f"REC{i+1}"
        user_id = f"USER{i+1}"
        
        # 5 步固定流程
        steps = [
            {
                "step_idx": 0,
                "step_name": "get_record",
                "tool_name": "get_record",
                "params": {"record_id": record_id}
            },
            {
                "step_idx": 1,
                "step_name": "policy_check",
                "tool_name": "policy_check",
                "params": {
                    "action": "approve",
                    "context": {"required_inventory": {"credits": 50}}
                }
            },
            {
                "step_idx": 2,
                "step_name": "update_record",
                "tool_name": "update_record",
                "params": {
                    "record_id": record_id,
                    "patch": {"status": "approved"}
                }
            },
            {
                "step_idx": 3,
                "step_name": "send_message",
                "tool_name": "send_message",
                "params": {
                    "user_id": user_id,
                    "text": "Your request has been approved"
                }
            },
            {
                "step_idx": 4,
                "step_name": "commit",
                "tool_name": "commit",
                "params": {}
            }
        ]
        
        # 故障注入 (均匀分布)
        fault_injections = []
        if i < len(fault_types):
            # 前8个任务,每个覆盖一种故障类型
            fault_type = fault_types[i]
            fault_injections.append({
                "step_idx": random.randint(0, 4),
                "fault_type": fault_type,
                "prob": 1.0,
                "fault_id": f"F{i+1}"
            })
        elif i < 16:
            # 中间8个任务,随机故障
            fault_type = random.choice(fault_types)
            fault_injections.append({
                "step_idx": random.randint(0, 4),
                "fault_type": fault_type,
                "prob": 0.8,
                "fault_id": f"F{i+1}"
            })
        # 最后4个任务无故障
        
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
    tasks = generate_tasks(n=20, seed=42)
    
    with open("tasks.jsonl", 'w') as f:
        for task in tasks:
            f.write(json.dumps(task) + '\n')
    
    print(f"Generated {len(tasks)} tasks -> tasks.jsonl")
    print(f"Sample task: {tasks[0]['task_id']}")
    print(f"  Steps: {len(tasks[0]['steps'])}")
    print(f"  Faults: {len(tasks[0]['fault_injections'])}")