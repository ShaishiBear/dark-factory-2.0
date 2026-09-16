"""Adaptive actions remain proposals and cannot escape cumulative reservations or scope."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from factory_kernel.exploration_reasoner import ExplorationReasoner
from factory_kernel.frontdoor_intent import IntentRefused
from tests.factory import test_exploration as fixture
from tests.factory.test_exploration import candidate, claim
from tests.factory.test_frontdoor_intent import OWNER


class ReasonerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExplorationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine = self.fixture.engine
        self.provider = Mock(config=None)
        self.reasoner = ExplorationReasoner(self.engine, self.provider)
        self.requests = []
        self.outputs = []
        self.cost = 0.01
        self.provider.run.side_effect = self.respond

    def respond(self, request):
        self.requests.append(request)
        self.assertEqual(request.allowed_tools, ())
        self.assertEqual(request.environment, {})
        self.assertLessEqual(request.max_turns, 5)
        self.assertLessEqual(request.max_budget_usd, 1)
        self.assertLessEqual(request.timeout_seconds, 338)
        self.assertEqual(list(Path(request.cwd).iterdir()), [])
        state = self.fixture.inspect()
        self.assertGreaterEqual(state["budget"]["calls"], len(self.requests))
        self.assertTrue(any(row["status"] == "pending" for row in state["session"]["reservations"].values()))
        prompt = json.loads(request.prompt.split("\n", 1)[1])
        self.assertNotIn("reservations", prompt["context"]["session"], "Prompt snapshots must not embed old prompt snapshots.")
        return SimpleNamespace(structured_output=deepcopy(self.outputs.pop(0)), cost_usd=self.cost, content="")

    def proposal(self, action, request):
        return {"action": action, "request": request, "reason": "This action can resolve the next uncertainty."}

    def command(self):
        return self.fixture.command({"purpose": "Choose the next useful investigation.", "max_usd": 1})

    def test_adaptive_loop_uses_probe_result_then_compiles_a_programme(self):
        self.outputs = [
            self.proposal("add-candidates", {"claims": [claim("scan-assumption"), claim("index-assumption")],
                "candidates": [candidate("scan", "linear", 10, 20), candidate("index", "hash", 40, 60)]}),
            self.proposal("experiment", self.fixture.experiment_request()),
            self.proposal("recommend", {"stop_reason": "sufficient-support", "rationale": "The probe changed preference.",
                "remaining_uncertainty": ["No production latency evidence."], "next_useful_experiment": "Production-shaped workload."}),
            self.proposal("handoff", {"proposal": self.fixture.fixture.request["proposal"]}),
        ]
        result = self.reasoner.run("citations", "lookup", principal=OWNER)
        self.assertEqual(result["status"], "advanced", result)
        self.assertEqual(result["action"], "handoff")
        handoff = result["state"]["sessions"]["lookup"]["handoffs"][-1]
        self.assertEqual(handoff["candidate_id"], "index")
        self.assertEqual(handoff["qualification_status"], "UNPROVEN")
        self.assertEqual([request.role for request in self.requests],
                         ["preflight-proposer", "preflight-challenger", "preflight-challenger", "preflight-proposer"])
        self.assertEqual(self.fixture.inspect()["budget"]["calls"], 4)
        self.assertEqual(self.fixture.inspect()["budget"]["usd"], 4)
        self.assertTrue(all(not Path(request.cwd).exists() for request in self.requests))

    def test_replaying_completed_reasoning_cannot_call_again(self):
        self.outputs = [self.proposal("stop", {"reason": "Need a probe unavailable in this registry."})]
        command = self.command()
        first = self.reasoner.advance("citations", command, principal=OWNER)
        self.assertEqual(first["status"], "needs-evidence-or-owner-tradeoff")
        replay_error = None
        try:
            second = self.reasoner.advance("citations", command, principal=OWNER)
        except IntentRefused as exc:
            replay_error = exc
        self.assertIsNone(replay_error, "A completed replay must return without creating a conflicting result.")
        self.assertEqual(second["status"], "already-recorded")
        self.assertEqual(len(self.requests), 1)

    def test_unknown_or_excess_cost_blocks_further_spend_even_with_new_command_key(self):
        for cost in (None, float("nan"), 2):
            with self.subTest(cost=cost):
                other = ReasonerTests()
                other.setUp()
                try:
                    other.outputs = [other.proposal("stop", {"reason": "Finished."})]
                    other.cost = cost
                    result = other.reasoner.advance("citations", other.command(), principal=OWNER)
                    self.assertEqual(result["status"], "reasoning-failed")
                    with self.assertRaisesRegex(IntentRefused, "spend is uncertain"):
                        other.reasoner.advance("citations", other.command(), principal=OWNER)
                    self.assertEqual(len(other.requests), 1)
                finally:
                    other.doCleanups()

    def test_model_cannot_self_certify_or_change_policy(self):
        self.outputs = [self.proposal("qualify", {"verdict": "passed"})]
        result = self.reasoner.advance("citations", self.command(), principal=OWNER)
        self.assertEqual(result["status"], "reasoning-failed")
        self.assertFalse(result["state"]["sessions"]["lookup"]["handoffs"])

    def test_cumulative_budget_survives_new_session_and_new_command_keys(self):
        for _ in range(4):
            self.outputs = [self.proposal("stop", {"reason": "No available discriminating probe."})]
            self.reasoner.advance("citations", self.command(), principal=OWNER)
        self.fixture.open("child", "lookup")
        command = self.fixture.command({"purpose": "Try again.", "max_usd": 1}, session="child")
        with self.assertRaisesRegex(IntentRefused, "cumulative reasoning budget"):
            self.reasoner.advance("citations", command, principal=OWNER)
        self.assertEqual(len(self.requests), 4)

    def test_provider_retries_and_unbounded_loops_are_refused(self):
        with self.assertRaises(IntentRefused):
            ExplorationReasoner(self.engine, Mock(config=SimpleNamespace(transient_retries=1)))
        for bound in (0, 9, True):
            with self.assertRaises(IntentRefused):
                self.reasoner.run("citations", "lookup", principal=OWNER, max_steps=bound)


if __name__ == "__main__":
    unittest.main()
