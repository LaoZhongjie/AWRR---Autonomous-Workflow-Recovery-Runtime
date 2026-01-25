from state import WorldState


def check_success(world_state: WorldState, task: dict) -> bool:
    """检查任务是否成功"""
    success_condition = task.get("success_condition", {})
    
    if success_condition.get("type") == "record_status":
        record_id = success_condition["record_id"]
        expected_status = success_condition["expected_status"]
        
        if record_id in world_state.records:
            actual_status = world_state.records[record_id].get("status")
            return actual_status == expected_status
    
    return False