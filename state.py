from dataclasses import dataclass, field
from typing import Any
import hashlib
import json


@dataclass
class WorldState:
    records: dict
    inventory: dict
    audit_log: list
    fault_log: set[str] = field(default_factory=set)
    fault_plan: dict[str, bool] = field(default_factory=dict)
    fault_state: dict = field(default_factory=dict)  # extra per-fault state (e.g., conflict needs rollback)

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
            audit_log=json.loads(json.dumps(self.audit_log)),
            fault_log=set(self.fault_log),
            fault_plan=dict(self.fault_plan),
            fault_state=json.loads(json.dumps(self.fault_state))
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
    ts_ms: int | None = None
    attempt_idx: int = 0
    event_type: str = "tool_call"
    budget_remaining_tokens: int | None = None
    budget_remaining_tool_calls: int | None = None
    budget_remaining_time_s: float | None = None
    budget_used_tokens: int | None = None
    budget_used_tool_calls: int | None = None
    budget_used_time_s: float | None = None
    final_outcome: str | None = None
    final_reason: str | None = None
    compensation_action: str | None = None
    saga_stack_depth: int = 0
    diagnosis: dict | None = None
    srr_eligible: bool | None = None
    srr_pass: bool | None = None

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
            "recovery_action": self.recovery_action,
            "ts_ms": self.ts_ms,
            "attempt_idx": self.attempt_idx,
            "event_type": self.event_type,
            "budget_remaining_tokens": self.budget_remaining_tokens,
            "budget_remaining_tool_calls": self.budget_remaining_tool_calls,
            "budget_remaining_time_s": self.budget_remaining_time_s,
            "budget_used_tokens": self.budget_used_tokens,
            "budget_used_tool_calls": self.budget_used_tool_calls,
            "budget_used_time_s": self.budget_used_time_s,
            "final_outcome": self.final_outcome,
            "final_reason": self.final_reason,
            "compensation_action": self.compensation_action,
            "saga_stack_depth": self.saga_stack_depth,
            "diagnosis": self.diagnosis,
            "srr_eligible": self.srr_eligible,
            "srr_pass": self.srr_pass,
        }
