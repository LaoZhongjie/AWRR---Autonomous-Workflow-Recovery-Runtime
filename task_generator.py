import json
import random
from constants import SEED


def generate_tasks(n: int = 50, seed: int = SEED) -> list[dict]:
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
        
        # 故障注入策略：
        # - 前 8 个任务：每种故障类型各一个
        # - 中间 32 个任务：随机故障，概率 0.7-0.9
        # - 最后 10 个任务：无故障（对照组）
        fault_injections = []
        
        if i < len(fault_types):
            # 前 8 个任务：覆盖所有故障类型
            fault_type = fault_types[i]
            fault_injections.append({
                "step_idx": random.randint(0, 4),
                "fault_type": fault_type,
                "prob": 1.0,
                "fault_id": f"F{i+1}"
            })
        elif i < 40:
            # 中间 32 个任务：随机故障
            fault_type = random.choice(fault_types)
            fault_injections.append({
                "step_idx": random.randint(0, 4),
                "fault_type": fault_type,
                "prob": random.uniform(0.7, 0.9),
                "fault_id": f"F{i+1}"
            })
        # 最后 10 个任务无故障
        
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
    tasks = generate_tasks(n=50, seed=42)
    
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
