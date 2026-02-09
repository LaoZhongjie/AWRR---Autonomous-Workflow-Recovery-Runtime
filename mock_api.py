import hashlib
import json
import random
import time
from state import WorldState, StepResult
from typing import Optional
from constants import SEED


# 故障类型常量
FAULT_TYPES = [
    "Timeout",
    "HTTP_500",
    "BadRequest",
    "AuthDenied",
    "NotFound",
    "Conflict",
    "PolicyRejected",
    "StateCorruption"
]


# 在 mock_api.py 中更新 FaultInjector 类

class FaultInjector:
    """无状态故障注入器"""
    
    # Ground truth mapping: fault_type -> layer
    FAULT_TYPE_TO_LAYER = {
        "Timeout": "transient",
        "HTTP_500": "transient",
        "Conflict": "cascade",
        "StateCorruption": "cascade",
        "AuthDenied": "persistent",
        "PolicyRejected": "semantic",
        "BadRequest": "semantic",
        "NotFound": "persistent"
    }
    
    @staticmethod
    def should_inject(
        fault_config: Optional[dict],
        step_idx: int,
        task_id: str,
        world_state: WorldState,
        attempt_idx: int = 0
    ) -> Optional[dict]:
        """检查是否应该注入故障"""
        if not fault_config:
            return None

        if fault_config.get("step_idx") != step_idx:
            return None

        fault_id = fault_config.get("fault_id", "unknown")
        mode = fault_config.get("mode", "once")  # once | per_attempt | persistent | stateful_conflict

        # persistent: 一旦计划为真，每次都会注入（不检查 fault_log）
        # per_attempt: 每次尝试按概率独立抽签，可能多次注入
        # once(default): 旧行为，抽签一次，最多注入一次

        rng = _seeded_random(SEED, task_id, fault_id, step_idx, attempt_idx)

        if mode == "once":
            if fault_id in world_state.fault_log:
                return None
            if fault_id not in world_state.fault_plan:
                prob = fault_config.get("prob", 0.0)
                world_state.fault_plan[fault_id] = rng.random() < prob
            if world_state.fault_plan.get(fault_id, False):
                fault_type = fault_config["fault_type"]
                world_state.fault_log.add(fault_id)
                return {
                    "fault_type": fault_type,
                    "fault_id": fault_id,
                    "layer_gt": fault_config.get("layer_override") or FaultInjector.FAULT_TYPE_TO_LAYER.get(fault_type, "persistent"),
                    "task_id": task_id,
                    "scenario": fault_config.get("scenario")
                }
            return None

        if mode == "per_attempt":
            prob = fault_config.get("prob", 0.0)
            if rng.random() < prob:
                fault_type = fault_config["fault_type"]
                return {
                    "fault_type": fault_type,
                    "fault_id": fault_id,
                    "layer_gt": fault_config.get("layer_override") or FaultInjector.FAULT_TYPE_TO_LAYER.get(fault_type, "persistent"),
                    "task_id": task_id,
                    "scenario": fault_config.get("scenario")
                }
            return None

        if mode == "persistent":
            if fault_id not in world_state.fault_plan:
                prob = fault_config.get("prob", 0.0)
                # Sample once per task+fault; if active, it persists across retries.
                world_state.fault_plan[fault_id] = rng.random() < prob
            if world_state.fault_plan.get(fault_id, False):
                fault_type = fault_config["fault_type"]
                return {
                    "fault_type": fault_type,
                    "fault_id": fault_id,
                    "layer_gt": fault_config.get("layer_override") or FaultInjector.FAULT_TYPE_TO_LAYER.get(fault_type, "persistent"),
                    "task_id": task_id,
                    "scenario": fault_config.get("scenario")
                }
            return None

        if mode == "stateful_conflict":
            # Stateful conflict semantics:
            # - Sample whether this fault is "planned" for the task once (or force on first attempt).
            # - If planned, it stays active across retries until a rollback is observed.
            # - After rollback, it is considered resolved and will not reactivate.
            state = world_state.fault_state.setdefault(
                fault_id,
                {"planned": None, "active": False, "resolved": False, "rollback_seen": 0},
            )
            # Count rollbacks so far
            rollback_count = sum(1 for a in world_state.audit_log if a.get("action") == "rollback")
            # If active but a rollback has happened since activation -> clear
            if state["active"] and rollback_count > state.get("rollback_seen", 0):
                state["active"] = False
                state["resolved"] = True
                state["rollback_seen"] = rollback_count

            if state.get("planned") is None:
                if fault_config.get("force_first_attempt") and attempt_idx == 0:
                    state["planned"] = True
                else:
                    prob = fault_config.get("prob", 0.0)
                    state["planned"] = rng.random() < prob

            # Activate only if planned and not yet resolved.
            if (not state["active"]) and state.get("planned") and not state.get("resolved"):
                state["active"] = True
                state["rollback_seen"] = rollback_count

            if state["active"]:
                fault_type = fault_config["fault_type"]
                return {
                    "fault_type": fault_type,
                    "fault_id": fault_id,
                    "layer_gt": fault_config.get("layer_override") or FaultInjector.FAULT_TYPE_TO_LAYER.get(fault_type, "cascade"),
                    "task_id": task_id,
                    "scenario": fault_config.get("scenario")
                }
            return None

        return None


def _seeded_random(*parts) -> random.Random:
    seed_payload = ":".join(str(p) for p in parts)
    seed_hash = hashlib.md5(seed_payload.encode()).hexdigest()
    seed_int = int(seed_hash, 16) % (2**32)
    return random.Random(seed_int)


def _fault_latency_ms(fault_type: str, fault_seed: str) -> int:
    latency_ranges = {
        "Timeout": (120, 400),
        "HTTP_500": (80, 250),
        "BadRequest": (20, 80),
        "AuthDenied": (20, 80),
        "NotFound": (20, 80),
        "Conflict": (60, 220),
        "PolicyRejected": (20, 80),
        "StateCorruption": (80, 260)
    }
    low, high = latency_ranges.get(fault_type, (30, 90))
    rng = _seeded_random(SEED, fault_seed, fault_type)
    return rng.randint(low, high)


def _execute_tool(
    world_state: WorldState,
    tool_func,
    fault_injection: Optional[dict],
    *args,
    **kwargs
) -> StepResult:
    """通用工具执行包装器"""
    start_ns = time.perf_counter_ns()
    
    # 注入故障
    if fault_injection:
        fault_type = fault_injection["fault_type"]
        fault_seed = f"{fault_injection.get('task_id', 'unknown')}:{fault_injection.get('fault_id', 'unknown')}"
        simulated_latency_ms = _fault_latency_ms(fault_type, fault_seed)
        time.sleep(simulated_latency_ms / 1000.0)
        latency_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        
        error_messages = {
            "Timeout": "Request timeout after 30s",
            "HTTP_500": "Internal server error",
            "BadRequest": "Invalid request parameters",
            "AuthDenied": "Authentication denied",
            "NotFound": "Resource not found",
            "Conflict": "Resource conflict detected",
            "PolicyRejected": "Policy violation detected",
            "StateCorruption": "State corruption detected"
        }
        
        return StepResult(
            status="error",
            output=None,
            error_type=fault_type,
            error_msg=error_messages.get(fault_type, "Unknown error"),
            error_trace=f"Injected fault: {fault_type}",
            latency_ms=latency_ms,
            injected_fault=fault_injection
        )
    
    # 正常执行
    try:
        result = tool_func(world_state, *args, **kwargs)
        # 添加基础执行延迟，避免过于理想
        base_sleep_ms = _seeded_random(SEED, tool_func.__name__, args, kwargs).randint(5, 40)
        time.sleep(base_sleep_ms / 1000.0)
        latency_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        return StepResult(
            status="ok",
            output=result,
            error_type=None,
            error_msg=None,
            error_trace=None,
            latency_ms=latency_ms,
            injected_fault=None
        )
    except Exception as e:
        latency_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        return StepResult(
            status="error",
            output=None,
            error_type="RuntimeError",
            error_msg=str(e),
            error_trace=str(e),
            latency_ms=latency_ms,
            injected_fault=None
        )


# 工具函数实现

def get_record(world_state: WorldState, record_id: str, fault_injection: Optional[dict] = None) -> StepResult:
    def _get(ws: WorldState, rid: str):
        if rid not in ws.records:
            raise ValueError(f"Record {rid} not found")
        return {"record": ws.records[rid]}
    
    return _execute_tool(world_state, _get, fault_injection, record_id)


def policy_check(world_state: WorldState, action: str, context: dict, fault_injection: Optional[dict] = None) -> StepResult:
    def _check(ws: WorldState, act: str, ctx: dict):
        # 简单策略: 检查库存
        required_inventory = ctx.get("required_inventory", {})
        for item, qty in required_inventory.items():
            if ws.inventory.get(item, 0) < qty:
                raise ValueError(f"Insufficient inventory: {item}")
        return {"allowed": True, "action": act}
    
    return _execute_tool(world_state, _check, fault_injection, action, context)


def auth_check(world_state: WorldState, record_id: str, fault_injection: Optional[dict] = None) -> StepResult:
    def _auth(ws: WorldState, rid: str):
        if rid not in ws.records:
            raise ValueError("Record not found")
        return {"authorized": True, "record_id": rid}
    return _execute_tool(world_state, _auth, fault_injection, record_id)


def update_record(world_state: WorldState, record_id: str, patch: dict, fault_injection: Optional[dict] = None) -> StepResult:
    def _update(ws: WorldState, rid: str, p: dict):
        if rid not in ws.records:
            raise ValueError(f"Record {rid} not found")
        ws.records[rid].update(p)
        ws.audit_log.append({
            "action": "update_record",
            "record_id": rid,
            "patch": p,
            "timestamp": int(time.time())
        })
        return {"record_id": rid, "updated": True}
    
    return _execute_tool(world_state, _update, fault_injection, record_id, patch)


def notify_user(world_state: WorldState, record_id: str, fault_injection: Optional[dict] = None) -> StepResult:
    def _notify(ws: WorldState, rid: str):
        ws.audit_log.append({
            "action": "notify_user",
            "record_id": rid,
            "timestamp": int(time.time())
        })
        return {"record_id": rid, "notified": True}
    return _execute_tool(world_state, _notify, fault_injection, record_id)


def send_message(world_state: WorldState, user_id: str, text: str, fault_injection: Optional[dict] = None) -> StepResult:
    def _send(ws: WorldState, uid: str, txt: str):
        ws.audit_log.append({
            "action": "send_message",
            "user_id": uid,
            "text": txt,
            "timestamp": int(time.time())
        })
        return {"user_id": uid, "sent": True}
    
    return _execute_tool(world_state, _send, fault_injection, user_id, text)


def create_ticket(world_state: WorldState, summary: str, severity: str, fault_injection: Optional[dict] = None) -> StepResult:
    def _create(ws: WorldState, summ: str, sev: str):
        ticket_id = f"TKT-{len(ws.audit_log)}"
        ws.audit_log.append({
            "action": "create_ticket",
            "ticket_id": ticket_id,
            "summary": summ,
            "severity": sev,
            "timestamp": int(time.time())
        })
        return {"ticket_id": ticket_id, "created": True}
    
    return _execute_tool(world_state, _create, fault_injection, summary, severity)


def commit(world_state: WorldState, fault_injection: Optional[dict] = None) -> StepResult:
    def _commit(ws: WorldState):
        ws.audit_log.append({
            "action": "commit",
            "timestamp": int(time.time())
        })
        return {"committed": True}
    
    return _execute_tool(world_state, _commit, fault_injection)


def lock_inventory(world_state: WorldState, item_id: str, qty: int, fault_injection: Optional[dict] = None) -> StepResult:
    def _lock(ws: WorldState, item: str, amount: int):
        available = ws.inventory.get(item, 0)
        if available < amount:
            raise ValueError(f"Insufficient inventory for {item}")
        ws.inventory[item] = available - amount
        ws.audit_log.append({
            "action": "lock_inventory",
            "item_id": item,
            "qty": amount,
            "timestamp": int(time.time())
        })
        return {"item_id": item, "locked": amount}

    return _execute_tool(world_state, _lock, fault_injection, item_id, qty)


def unlock_inventory(world_state: WorldState, item_id: str, qty: int, fault_injection: Optional[dict] = None) -> StepResult:
    def _unlock(ws: WorldState, item: str, amount: int):
        ws.inventory[item] = ws.inventory.get(item, 0) + amount
        ws.audit_log.append({
            "action": "unlock_inventory",
            "item_id": item,
            "qty": amount,
            "timestamp": int(time.time())
        })
        return {"item_id": item, "unlocked": amount}

    return _execute_tool(world_state, _unlock, fault_injection, item_id, qty)


def process_payment(world_state: WorldState, order_id: str, amount: int, fault_injection: Optional[dict] = None) -> StepResult:
    def _process(ws: WorldState, oid: str, amt: int):
        if oid in ws.records:
            ws.records[oid]["payment_status"] = "paid"
            ws.records[oid]["amount"] = amt
        ws.audit_log.append({
            "action": "process_payment",
            "order_id": oid,
            "amount": amt,
            "timestamp": int(time.time())
        })
        return {"order_id": oid, "paid": True}

    return _execute_tool(world_state, _process, fault_injection, order_id, amount)


def refund_payment(world_state: WorldState, order_id: str, amount: int, fault_injection: Optional[dict] = None) -> StepResult:
    def _refund(ws: WorldState, oid: str, amt: int):
        if oid in ws.records:
            ws.records[oid]["payment_status"] = "refunded"
            ws.records[oid]["refund_amount"] = amt
        ws.audit_log.append({
            "action": "refund_payment",
            "order_id": oid,
            "amount": amt,
            "timestamp": int(time.time())
        })
        return {"order_id": oid, "refunded": True}

    return _execute_tool(world_state, _refund, fault_injection, order_id, amount)


def write_audit(world_state: WorldState, record_id: str, fault_injection: Optional[dict] = None) -> StepResult:
    def _write(ws: WorldState, rid: str):
        ws.audit_log.append({
            "action": "write_audit",
            "record_id": rid,
            "timestamp": int(time.time())
        })
        return {"record_id": rid, "written": True}
    return _execute_tool(world_state, _write, fault_injection, record_id)


def rollback(world_state: WorldState, checkpoint: WorldState, fault_injection: Optional[dict] = None) -> StepResult:
    def _rollback(ws: WorldState, cp: WorldState):
        # IMPORTANT: deep copy to avoid sharing references with checkpoint
        ws.records = json.loads(json.dumps(cp.records))
        ws.inventory = json.loads(json.dumps(cp.inventory))
        ws.audit_log = json.loads(json.dumps(cp.audit_log)) + [{
            "action": "rollback",
            "timestamp": int(time.time())
        }]
        return {"rolled_back": True}
    
    return _execute_tool(world_state, _rollback, fault_injection, checkpoint)
