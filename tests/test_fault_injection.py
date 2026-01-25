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


if __name__ == "__main__":
    unittest.main()
