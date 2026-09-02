import unittest

from factory_kernel.state import FactoryState, Outcome, Stage, retry, transition


class FactoryStateTests(unittest.TestCase):
    def test_happy_path_advances_deterministically(self):
        state = FactoryState(issue=42, stage=Stage.SPEC)
        self.assertEqual(transition(state, Outcome.PASS).stage, Stage.TICKETS)

    def test_only_frontier_can_wait(self):
        waiting = transition(FactoryState(issue=42, stage=Stage.FRONTIER), Outcome.WAIT)
        self.assertEqual(waiting.stage, Stage.WAITING)
        with self.assertRaisesRegex(ValueError, "wait is not legal"):
            transition(FactoryState(issue=42, stage=Stage.GREEN), Outcome.WAIT)

    def test_decompose_is_limited_to_structural_decision_points(self):
        result = transition(FactoryState(issue=42, stage=Stage.ARCHITECTURE), Outcome.DECOMPOSE)
        self.assertEqual(result.stage, Stage.DECOMPOSED)
        with self.assertRaisesRegex(ValueError, "decompose is not legal"):
            transition(FactoryState(issue=42, stage=Stage.IMPLEMENT), Outcome.DECOMPOSE)

    def test_retry_is_explicit_and_preserves_stage(self):
        first = FactoryState(issue=42, stage=Stage.HOLDOUT)
        second = retry(first)
        self.assertEqual(second.stage, Stage.HOLDOUT)
        self.assertEqual(second.attempt, 2)

    def test_terminal_states_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "terminal"):
            transition(FactoryState(issue=42, stage=Stage.NEEDS_HUMAN), Outcome.PASS)


if __name__ == "__main__":
    unittest.main()
