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
        world_state: WorldState
    ) -> Optional[dict]:
        """检查是否应该注入故障"""
        if not fault_config:
            return None

        if fault_config.get("step_idx") != step_idx:
            return None

        fault_id = fault_config.get("fault_id", "unknown")
        if fault_id in world_state.fault_log:
            return None

        if fault_id not in world_state.fault_plan:
            prob = fault_config.get("prob", 0.0)
            rng = _seeded_random(SEED, task_id, fault_id, step_idx)
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


def _seeded_random(*parts) -> random.Random:
    seed_payload = ":".join(str(p) for p in parts)
    seed_hash = hashlib.md5(seed_payload.encode()).hexdigest()
    seed_int = int(seed_hash, 16) % (2**32)
    return random.Random(seed_int)


def _fault_latency_ms(fault_type: str, fault_seed: str) -> int:
    latency_ranges = {
        "Timeout": (50, 150),
        "HTTP_500": (30, 80),
        "BadRequest": (10, 40),
        "AuthDenied": (10, 40),
        "NotFound": (10, 40),
        "Conflict": (20, 60),
        "PolicyRejected": (10, 40),
        "StateCorruption": (20, 60)
    }
    low, high = latency_ranges.get(fault_type, (10, 40))
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
