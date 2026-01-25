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


def check_consistency(world_state: WorldState, initial_state: dict) -> tuple[bool, str]:
    """一致性检查：库存恢复 + 无孤儿记录"""
    inventory_ok = world_state.inventory == initial_state.get("inventory", {})

    orphaned = False
    for record in world_state.records.values():
        status = record.get("status")
        payment_status = record.get("payment_status")
        if payment_status == "paid" and status != "approved":
            orphaned = True
            break
        if status == "approved" and payment_status not in (None, "paid"):
            orphaned = True
            break

    if not inventory_ok:
        return False, "inventory_mismatch"
    if orphaned:
        return False, "orphaned_records"
    return True, "ok"
