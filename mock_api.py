import random
import time
from state import WorldState, StepResult
from typing import Optional


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


class FaultInjector:
    """无状态故障注入器"""
    
    @staticmethod
    def should_inject(fault_config: Optional[dict], step_idx: int) -> Optional[dict]:
        """检查是否应该注入故障"""
        if not fault_config:
            return None
        
        if fault_config.get("step_idx") == step_idx:
            prob = fault_config.get("prob", 0.0)
            if random.random() < prob:
                return {
                    "fault_type": fault_config["fault_type"],
                    "fault_id": fault_config.get("fault_id", "unknown")
                }
        return None


def _execute_tool(world_state: WorldState, tool_func, fault_injection: Optional[dict], *args, **kwargs) -> StepResult:
    """通用工具执行包装器"""
    start_ms = int(time.time() * 1000)
    
    # 注入故障
    if fault_injection:
        fault_type = fault_injection["fault_type"]
        latency_ms = int(time.time() * 1000) - start_ms
        
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
        latency_ms = int(time.time() * 1000) - start_ms
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
        latency_ms = int(time.time() * 1000) - start_ms
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
        ws.records = cp.records
        ws.inventory = cp.inventory
        ws.audit_log = cp.audit_log + [{
            "action": "rollback",
            "timestamp": int(time.time())
        }]
        return {"rolled_back": True}
    
    return _execute_tool(world_state, _rollback, fault_injection, checkpoint)