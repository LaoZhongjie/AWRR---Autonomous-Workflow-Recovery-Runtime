from dataclasses import dataclass, field
from typing import Any
import hashlib
import json


@dataclass
class WorldState:
    records: dict
    inventory: dict
    audit_log: list

    def to_dict(self) -> dict:
        return {
            "records": self.records,
            "inventory": self.inventory,
            "audit_log": self.audit_log
        }

    def compute_hash(self) -> str:
        data = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def deep_copy(self) -> 'WorldState':
        return WorldState(
            records=json.loads(json.dumps(self.records)),
            inventory=json.loads(json.dumps(self.inventory)),
            audit_log=json.loads(json.dumps(self.audit_log))
        )


@dataclass
class Budget:
    max_tokens: int
    max_tool_calls: int
    max_time_s: float
    used_tokens: int = 0
    used_tool_calls: int = 0
    start_time: float = 0.0


@dataclass
class StepContext:
    task_id: str
    step_idx: int
    step_name: str
    tool_name: str
    params: dict
    state_hash: str
    budget_remaining: dict  # tokens/tool_calls/time


@dataclass
class StepResult:
    status: str  # "ok" | "error"
    output: dict | None
    error_type: str | None
    error_msg: str | None
    error_trace: str | None
    latency_ms: int
    injected_fault: dict | None


@dataclass
class TraceEvent:
    task_id: str
    step_idx: int
    step_name: str
    tool_name: str
    params: dict
    status: str
    latency_ms: int
    error_type: str | None
    injected_fault: dict | None
    state_hash: str
    budget: dict
    recovery_action: str | None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "step_idx": self.step_idx,
            "step_name": self.step_name,
            "tool_name": self.tool_name,
            "params": self.params,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "injected_fault": self.injected_fault,
            "state_hash": self.state_hash,
            "budget": self.budget,
            "recovery_action": self.recovery_action
        }