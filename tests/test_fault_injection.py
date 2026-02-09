import unittest

from mock_api import FaultInjector
from state import WorldState


class FaultInjectionTests(unittest.TestCase):
    def test_fault_id_injected_once_per_task(self):
        world_state = WorldState(records={}, inventory={}, audit_log=[])
        fault_config = {
            "step_idx": 1,
            "fault_type": "Timeout",
            "fault_id": "F-001",
            "prob": 1.0
        }

        first = FaultInjector.should_inject(
            fault_config, step_idx=1, task_id="T-001", world_state=world_state
        )
        second = FaultInjector.should_inject(
            fault_config, step_idx=1, task_id="T-001", world_state=world_state
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIn("F-001", world_state.fault_log)

    def test_stateful_conflict_clears_after_rollback(self):
        world_state = WorldState(records={}, inventory={}, audit_log=[])
        fault_config = {
            "step_idx": 2,
            "fault_type": "Conflict",
            "fault_id": "F-C1",
            "prob": 1.0,
            "mode": "stateful_conflict",
        }

        first = FaultInjector.should_inject(
            fault_config,
            step_idx=2,
            task_id="T-002",
            world_state=world_state,
            attempt_idx=0,
        )
        self.assertIsNotNone(first)

        # Simulate a rollback being observed by the system.
        world_state.audit_log.append({"action": "rollback"})

        second = FaultInjector.should_inject(
            fault_config,
            step_idx=2,
            task_id="T-002",
            world_state=world_state,
            attempt_idx=1,
        )
        self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
