from dataclasses import replace
import json
import unittest
from unittest.mock import Mock, patch

from factory_kernel.feedback_lab import Limits, compare, prompt_for, run_arm, run_experiment, validate_tasks
from factory_kernel.feedback_sandbox import CandidateError, SandboxError
from factory_kernel.feedback_lab_cli import LiveWorker, RecordedWorker


TASK = {"id": "identity", "group": "identity", "split": "development", "instruction": "Return the input.",
        "public": [{"input": 2, "expected": 2}], "acceptance": [{"input": 5, "expected": 5}]}


class FunctionSandbox:
    """Only these fixed reviewed strings are recognized; test never executes candidate code."""
    def __init__(self):
        self.calls = []

    def evaluate(self, code, inputs, **kwargs):
        self.calls.append((code, inputs))
        if code == "broken":
            return [None for _ in inputs]
        if code == "crashes":
            raise CandidateError("SyntaxError")
        if code == "infrastructure":
            raise SandboxError("daemon unavailable")
        return list(inputs)


class FeedbackTests(unittest.TestCase):
    def worker(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return {"code": "fixed" if "public_feedback" in prompt else "broken", "cost_usd": None}

    def setUp(self):
        self.calls, self.events = [], []
        self.sandbox = FunctionSandbox()

    def test_feedback_repairs_but_single_keeps_failure(self):
        report = run_experiment([TASK], self.worker, self.sandbox, Limits(), emit=self.events.append)
        self.assertEqual(report["arms"]["single"]["accepted"], 0)
        self.assertEqual(report["arms"]["feedback"]["accepted"], 1)
        self.assertEqual(report["promotion"], "not-authorized")
        self.assertFalse(report["qualification_authority"])

    def test_same_total_turn_and_reservation_caps(self):
        for arm in ("single", "feedback"):
            self.calls.clear()
            row = run_arm(TASK, arm, self.worker, self.sandbox, Limits(), emit=self.events.append)
            self.assertAlmostEqual(sum(call[1]["dollars"] for call in self.calls), .1)
            self.assertEqual(sum(call[1]["turns"] for call in self.calls), 4)
            self.assertIsNone(row["reported_cost_usd"])

    def test_reserves_before_failed_call_and_keeps_denominator(self):
        def fail(prompt, **caps):
            self.assertEqual(self.events[-1]["kind"], "worker_reserved")
            raise RuntimeError("transport failed")
        report = run_experiment([TASK], fail, self.sandbox, Limits(), emit=self.events.append)
        self.assertEqual(len(report["attempts"]), 2)
        self.assertEqual(report["arms"]["feedback"]["errors"], 1)
        self.assertGreater(report["attempts"][1]["reserved_usd"], 0)
        self.assertIsNone(report["attempts"][1]["reported_cost_usd"])

    def test_acceptance_labels_never_reach_worker(self):
        task = {**TASK, "acceptance": [{"input": "SECRET-INPUT", "expected": "SECRET-ANSWER"}]}
        prompt = prompt_for(task, None, None)
        self.assertNotIn("SECRET", prompt)
        self.assertNotIn("acceptance", prompt)
        self.assertNotIn("group", prompt)

    def test_final_reruns_exact_last_draft(self):
        row = run_arm(TASK, "feedback", self.worker, self.sandbox, Limits(), emit=self.events.append)
        self.assertTrue(row["accepted"])
        self.assertEqual(self.sandbox.calls[-1], ("fixed", [5]))
        self.assertEqual(row["rounds"][-1]["code"], "fixed")

    def test_candidate_exception_can_be_repaired(self):
        def worker(prompt, **caps):
            return {"code": "fixed" if "public_feedback" in prompt else "crashes"}
        row = run_arm(TASK, "feedback", worker, self.sandbox, Limits(), emit=self.events.append)
        self.assertTrue(row["accepted"])
        self.assertIn("execution_error", row["rounds"][0]["public"])

    def test_infrastructure_failure_is_not_correct_refusal_or_repair(self):
        row = run_arm(TASK, "feedback", lambda *a, **k: {"code": "infrastructure"}, self.sandbox,
                      Limits(), emit=self.events.append)
        self.assertFalse(row["accepted"])
        self.assertEqual(row["status"], "error")
        self.assertEqual(len(row["rounds"]), 1)

    def test_cancellation_assigns_failures_without_spending(self):
        report = run_experiment([TASK], self.worker, self.sandbox, Limits(), emit=self.events.append,
                                cancelled=lambda: True)
        self.assertEqual(len(report["attempts"]), 2)
        self.assertEqual(len(self.calls), 0)
        self.assertTrue(all(row["reserved_usd"] == 0 for row in report["attempts"]))

    def test_task_budget_expiry_refuses_final_checks(self):
        ticks = iter([0, 0, 121, 122])
        row = run_arm(TASK, "single", self.worker, self.sandbox, Limits(), emit=self.events.append,
                      clock=lambda: next(ticks))
        self.assertEqual(row["status"], "error")
        self.assertEqual(len(self.sandbox.calls), 0)

    def test_public_success_stops_without_extra_spend(self):
        row = run_arm(TASK, "feedback", lambda *a, **k: {"code": "fixed", "cost_usd": 0}, self.sandbox,
                      Limits(), emit=self.events.append)
        self.assertTrue(row["accepted"])
        self.assertEqual(len(row["rounds"]), 1)
        self.assertAlmostEqual(row["reserved_usd"], .05)

    def test_bad_limits_and_split_leak_refused(self):
        for limits in (Limits(rounds=True), Limits(dollars=float("nan")), Limits(turns=1)):
            with self.assertRaises(ValueError):
                limits.validate()
        with self.assertRaises(ValueError):
            validate_tasks([TASK, {**TASK, "id": "other", "split": "confirmation"}])

    def test_missing_or_boolean_result_does_not_pass_numeric_check(self):
        with self.assertRaises(ValueError):
            compare(TASK["public"], [])
        self.assertFalse(compare([{"input": 0, "expected": 1}], [True])["passed"])
        self.assertTrue(compare([{"input": 0, "expected": [2.0]}], [[2]])["passed"])
        self.assertFalse(compare([{"input": 0, "expected": {"a": [1]}}], [{"a": [True]}])["passed"])

    def test_recorded_worker_only_uses_public_examples(self):
        worker = RecordedWorker()
        initial = worker(prompt_for(TASK, None, None))
        repaired = worker(prompt_for(TASK, initial["code"], {"passed": False}))
        self.assertIn("return None", initial["code"])
        self.assertNotIn("5", repaired["code"])

    def test_overreported_spend_stops_current_arm(self):
        row = run_arm(TASK, "feedback", lambda *a, **k: {"code": "fixed", "cost_usd": 9}, self.sandbox,
                      Limits(), emit=self.events.append)
        self.assertEqual(row["status"], "error")
        self.assertEqual(len(self.sandbox.calls), 0)

    def test_overreported_spend_prevents_all_subsequent_calls(self):
        worker = Mock(return_value={"code": "fixed", "cost_usd": 9})
        report = run_experiment([TASK], worker, self.sandbox, Limits(), emit=self.events.append)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(len(report["attempts"]), 2)

    def test_public_failure_cannot_be_saved_by_final_success(self):
        class DifferentResults:
            def evaluate(self, code, inputs, **caps):
                return [None] if inputs == [2] else [5]
        row = run_arm(TASK, "feedback", self.worker, DifferentResults(), Limits(), emit=self.events.append)
        self.assertTrue(row["acceptance"]["passed"])
        self.assertFalse(row["accepted"])

    def test_unknown_costs_remain_unknown_in_summary(self):
        report = run_experiment([TASK], self.worker, self.sandbox, Limits(), emit=self.events.append)
        self.assertIsNone(report["arms"]["feedback"]["total_reported_cost_usd"])
        self.assertEqual(report["arms"]["feedback"]["unknown_cost_arms"], 1)
        self.assertEqual(report["paired_outcomes"], [{"id": "identity", "single": False, "feedback": True}])

    def test_live_adapter_has_no_tools_or_automatic_retries(self):
        from factory_kernel.config import ProviderConfig
        configured = ProviderConfig("claude-cli", "claude", "fixture-model", 2700, transient_retries=2)
        with patch("harness.feedback.provider.load_config", return_value=Mock(provider=configured)):
            worker = LiveWorker("fixture-config")
        self.assertEqual(worker.provider.config.transient_retries, 0)
        with patch.object(worker.provider, "run", return_value=Mock(
                structured_output={"code": "def solve(x): return x"}, cost_usd=.01)) as run:
            with patch("harness.feedback.provider.tempfile.TemporaryDirectory") as directory:
                directory.return_value.__enter__.return_value = "."
                worker("instruction", turns=2, dollars=.05, seconds=30)
        request = run.call_args.args[0]
        self.assertEqual(request.allowed_tools, ())
        self.assertEqual(request.environment, {})
        self.assertEqual(request.max_turns, 2)
        self.assertEqual(request.max_budget_usd, .05)
        self.assertEqual(request.timeout_seconds, 30)
        for kwargs in ({"turns": True, "dollars": .05, "seconds": 30},
                       {"turns": 2, "dollars": float("nan"), "seconds": 30},
                       {"turns": 2, "dollars": .05, "seconds": 0}):
            with self.assertRaises(ValueError):
                worker("instruction", **kwargs)


if __name__ == "__main__":
    unittest.main()
