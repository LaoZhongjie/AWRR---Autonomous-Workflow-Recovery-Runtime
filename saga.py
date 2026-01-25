from dataclasses import dataclass, field
from typing import Callable, Any
import time
from state import TraceEvent, WorldState
import mock_api


@dataclass
class CompensationAction:
    name: str
    compensate_fn: Callable[..., Any]
    args: tuple
    kwargs: dict


@dataclass
class TransactionStack:
    actions: list[CompensationAction] = field(default_factory=list)

    def push(self, compensate_fn: Callable[..., Any], args: tuple, kwargs: dict | None = None):
        if kwargs is None:
            kwargs = {}
        action = CompensationAction(
            name=getattr(compensate_fn, "__name__", "compensate"),
            compensate_fn=compensate_fn,
            args=tuple(args),
            kwargs=dict(kwargs),
        )
        self.actions.append(action)

    def pop(self) -> CompensationAction | None:
        if not self.actions:
            return None
        return self.actions.pop()

    def depth(self) -> int:
        return len(self.actions)

    def clear(self):
        self.actions.clear()


@dataclass
class SagaRollbackResult:
    status: str
    reason: str | None = None


class SagaManager:
    def __init__(self, logger, stack: TransactionStack | None = None):
        self.logger = logger
        self.stack = stack or TransactionStack()

    def rollback_saga(
        self,
        world_state: WorldState,
        task_id: str,
        step_idx: int,
        tracker,
        token_estimator,
    ) -> SagaRollbackResult:
        while self.stack.actions:
            if tracker.is_exhausted():
                return SagaRollbackResult(status="error", reason="budget_exhausted")

            action = self.stack.pop()
            result = action.compensate_fn(
                world_state, *action.args, **action.kwargs, fault_injection=None
            )
            budget_snapshot = tracker.snapshot()
            event = TraceEvent(
                task_id=task_id,
                step_idx=step_idx,
                step_name="compensate",
                tool_name=action.name,
                params={"args": list(action.args), "kwargs": action.kwargs},
                status=result.status,
                latency_ms=result.latency_ms,
                error_type=result.error_type,
                injected_fault=result.injected_fault,
                state_hash=world_state.compute_hash(),
                budget=budget_snapshot["remaining"],
                recovery_action="rollback",
                ts_ms=int(time.time() * 1000),
                attempt_idx=0,
                event_type="compensation",
                budget_remaining_tokens=budget_snapshot["remaining"]["tokens"],
                budget_remaining_tool_calls=budget_snapshot["remaining"]["tool_calls"],
                budget_remaining_time_s=budget_snapshot["remaining"]["time"],
                budget_used_tokens=budget_snapshot["used"]["tokens"],
                budget_used_tool_calls=budget_snapshot["used"]["tool_calls"],
                budget_used_time_s=budget_snapshot["used"]["time"],
                compensation_action="saga_rollback",
                saga_stack_depth=self.stack.depth(),
                diagnosis=None
            )
            self.logger.append(event)
            tracker.consume(tokens=token_estimator(event.params), tool_calls=1)

            if result.status != "ok":
                self._record_critical_failure(world_state, task_id, step_idx, tracker)
                return SagaRollbackResult(status="error", reason="compensation_failed")

        return SagaRollbackResult(status="ok", reason=None)

    def _record_critical_failure(
        self,
        world_state: WorldState,
        task_id: str,
        step_idx: int,
        tracker,
    ):
        ticket_result = mock_api.create_ticket(
            world_state,
            summary=f"Critical: compensation failed for task {task_id} at step {step_idx}",
            severity="critical",
            fault_injection=None,
        )
        budget_snapshot = tracker.snapshot()
        event = TraceEvent(
            task_id=task_id,
            step_idx=step_idx,
            step_name="compensate",
            tool_name="create_ticket",
            params={"summary": f"compensation_failed:{task_id}", "severity": "critical"},
            status=ticket_result.status,
            latency_ms=ticket_result.latency_ms,
            error_type=ticket_result.error_type,
            injected_fault=ticket_result.injected_fault,
            state_hash=world_state.compute_hash(),
            budget=budget_snapshot["remaining"],
            recovery_action="escalate",
            ts_ms=int(time.time() * 1000),
            attempt_idx=0,
            event_type="compensation",
            budget_remaining_tokens=budget_snapshot["remaining"]["tokens"],
            budget_remaining_tool_calls=budget_snapshot["remaining"]["tool_calls"],
            budget_remaining_time_s=budget_snapshot["remaining"]["time"],
            budget_used_tokens=budget_snapshot["used"]["tokens"],
            budget_used_tool_calls=budget_snapshot["used"]["tool_calls"],
            budget_used_time_s=budget_snapshot["used"]["time"],
            compensation_action="create_ticket",
            saga_stack_depth=self.stack.depth(),
            diagnosis=None
        )
        self.logger.append(event)
        tracker.consume(tokens=0, tool_calls=1)
