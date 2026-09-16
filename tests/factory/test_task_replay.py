"""Replay must execute observations, retain failures, and never acquire authority."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel import task_replay as replay
from factory_kernel import task_replay_adapters as adapters
from factory_kernel.benchmark import load_suite

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "harness/replay/inputs.json"
LABELS = ROOT / "harness/replay/labels.json"
IDENTITY = {"factory_sha": "a" * 40, "source_sha256": "b" * 64}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cases, _ = replay.load_cases(INPUTS)

    def write_inputs(self, value):
        path = self.home / "inputs.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def one_case(self):
        value = json.loads(INPUTS.read_text(encoding="utf-8"))
        value["cases"] = value["cases"][:1]
        return self.write_inputs(value)

    def test_no_expected_labels_identity_or_incident_in_worker_request(self):
        request = self.cases[0].request()
        self.assertEqual(set(request), {"adapter", "inputs"})
        self.assertNotIn("expected", json.dumps(request))
        first = request["inputs"]["contract"]["summary"]
        request["inputs"]["contract"]["summary"] = "modified"
        self.assertEqual(self.cases[0].request()["inputs"]["contract"]["summary"], first)

    def test_duplicate_json_and_nonfinite_values_refused(self):
        for raw in (b'{"a":1,"a":2}', b'{"a": NaN}', b'[]', b' ' * 256_001):
            with self.subTest(raw=raw[:25]), self.assertRaises(ValueError):
                replay.strict_json(raw)

    def test_labels_commands_and_duplicate_cases_refused_at_input_boundary(self):
        original = json.loads(INPUTS.read_text(encoding="utf-8"))
        for key in ("expected", "command", "module"):
            value = copy.deepcopy(original)
            value["cases"][0][key] = "merge"
            with self.subTest(key=key), self.assertRaises(ValueError):
                replay.load_cases(self.write_inputs(value))
        original["cases"].append(original["cases"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            replay.load_cases(self.write_inputs(original))

    def test_unregistered_adapter_and_path_shaped_id_refused(self):
        for key, bad in (("adapter", "os.system"), ("id", "../manifest")):
            value = json.loads(INPUTS.read_text(encoding="utf-8"))
            value["cases"][0][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                replay.load_cases(self.write_inputs(value))

    def test_retries_of_same_incident_cannot_cross_splits(self):
        value = json.loads(INPUTS.read_text(encoding="utf-8"))
        value["cases"][1]["split"] = "confirmation"
        with self.assertRaisesRegex(ValueError, "leaks across"):
            replay.load_cases(self.write_inputs(value))

    def test_no_credentials_or_startup_configuration_in_child_environment(self):
        with patch.dict(os.environ, {"GH_TOKEN": "synthetic", "OPENROUTER_API_KEY": "synthetic",
                                    "PYTHONPATH": "untrusted", "PATH": "untrusted", "HOME": "private"}):
            env = replay.child_environment(self.home)
        self.assertEqual(env["HOME"], str(self.home))
        self.assertFalse({"GH_TOKEN", "OPENROUTER_API_KEY", "PYTHONPATH", "PATH"} & env.keys())

    def test_contract_and_repro_and_lease_execute_production_rules(self):
        expected = {case.case_id: case.expected for case in load_suite(LABELS).cases}
        for case in self.cases[:6]:
            with self.subTest(case=case.id):
                observed = adapters.observe(**case.request())
                self.assertEqual(observed["outcome"], expected[case.id])
                self.assertTrue(observed["evidence"])

    def test_unexpected_validation_exception_is_not_a_correct_rejection(self):
        from harness.rehearsal import Trace
        trace = Trace(outcome="NameError", error="undefined variable")
        with patch("harness.rehearsal.rehearse", return_value=trace), self.assertRaises(RuntimeError):
            adapters.observe("validation", {"reject": "holdout"})

    def test_rehearsal_cannot_accept_file_writes_from_fixture(self):
        with self.assertRaises(ValueError):
            adapters.observe("validation", {"worktree_files": {"../../escape": "payload"}})

    def test_process_failure_and_timeout_are_not_observations(self):
        with patch.object(replay.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            row = replay.execute(self.cases[0], timeout=1)
        self.assertEqual(row["status"], "error")
        self.assertNotIn("outcome", row)
        with patch.object(replay.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 1)):
            row = replay.execute(self.cases[0], timeout=1)
        self.assertEqual(row["status"], "timeout")

    def test_zero_exit_with_forged_or_missing_result_is_invalid(self):
        def launch(*args, **kwargs):
            kwargs["stdout"].write(b'{"outcome":"merge"}')
            return subprocess.CompletedProcess([], 0)
        with patch.object(replay.subprocess, "run", side_effect=launch):
            self.assertEqual(replay.execute(self.cases[0], timeout=1)["status"], "invalid-output")

    def test_child_invocation_is_fixed_and_gets_only_data(self):
        def launch(argv, **kwargs):
            self.assertEqual(argv[1:4], ["-I", "-S", "-c"])
            self.assertEqual(argv[4], replay.BOOTSTRAP)
            self.assertEqual(set(json.loads(kwargs["input"])), {"adapter", "inputs"})
            self.assertEqual(kwargs["timeout"], 3)
            kwargs["stdout"].write(b'{"outcome":"wait","level":"production-rule","evidence":{"checked":true}}')
            return subprocess.CompletedProcess([], 0)
        with patch.object(replay.subprocess, "run", side_effect=launch):
            self.assertEqual(replay.execute(self.cases[0], timeout=3)["status"], "observed")

    def test_missing_execution_never_scores_as_expected_refusal(self):
        suite = load_suite(LABELS)
        rows = [{"id": case.case_id, "repeat": 1, "status": "observed", "outcome": case.expected}
                for case in suite.cases]
        rows[1] = {"id": rows[1]["id"], "repeat": 1, "status": "error"}
        result = replay.evaluate(self.cases, rows, [suite], IDENTITY["factory_sha"])
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["repeats"][0]["status"], "incomplete")
        self.assertEqual(result["assigned"], 10)

    def test_changed_label_changes_score_not_execution(self):
        suite = load_suite(LABELS)
        rows = [{"id": case.case_id, "repeat": 1, "status": "observed", "outcome": case.expected}
                for case in suite.cases]
        self.assertEqual(replay.evaluate(self.cases, rows, [suite], IDENTITY["factory_sha"])["status"], "pass")
        labels = json.loads(LABELS.read_text(encoding="utf-8"))
        labels["cases"][0]["expected"] = "merge"
        path = self.home / "labels.json"
        path.write_text(json.dumps(labels), encoding="utf-8")
        self.assertEqual(replay.evaluate(self.cases, rows, [load_suite(path)], IDENTITY["factory_sha"])["status"], "fail")

    def test_label_coverage_mismatch_refused(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            replay.evaluate(self.cases[:1], [], [load_suite(LABELS)], IDENTITY["factory_sha"])

    def test_source_bytes_not_only_head_are_bound(self):
        root = self.home / "repo"
        (root / "factory_kernel").mkdir(parents=True)
        file = root / "factory_kernel/test.py"
        file.write_text("first", encoding="utf-8")
        with patch.object(replay, "git", side_effect=["a" * 40, "factory_kernel/test.py\0"] * 2):
            first = replay.source_identity(root)
            file.write_text("second", encoding="utf-8")
            second = replay.source_identity(root)
        self.assertEqual(first["factory_sha"], second["factory_sha"])
        self.assertNotEqual(first["source_sha256"], second["source_sha256"])

    def test_partial_run_persists_and_never_overwrites_a_previous_run(self):
        inputs, output = self.one_case(), self.home / "run"
        with patch.object(replay, "source_identity", return_value=IDENTITY), \
                patch.object(replay, "execute", side_effect=KeyboardInterrupt):
            report = replay.run(inputs, output, labels=[], repeats=2)
        self.assertEqual([row["status"] for row in report["attempts"]], ["interrupted", "not-run"])
        self.assertTrue((output / "report.json").exists())
        events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), 4)
        with patch.object(replay, "source_identity", return_value=IDENTITY), self.assertRaises(ValueError):
            replay.run(inputs, output, labels=[])

    def test_total_budget_retains_unexecuted_assignment(self):
        with patch.object(replay, "source_identity", return_value=IDENTITY), \
                patch.object(replay.time, "monotonic", side_effect=[0, 5]), \
                patch.object(replay, "execute") as execute:
            report = replay.run(self.one_case(), self.home / "run", labels=[], total_seconds=1)
        execute.assert_not_called()
        self.assertEqual(report["attempts"][0]["reason"], "total-budget")

    def test_source_drift_invalidates_otherwise_observed_run(self):
        changed = {**IDENTITY, "source_sha256": "c" * 64}
        with patch.object(replay, "source_identity", side_effect=[IDENTITY, changed]), \
                patch.object(replay, "execute", return_value={"status": "observed", "outcome": "wait"}):
            report = replay.run(self.one_case(), self.home / "run", labels=[])
        self.assertFalse(report["source_unchanged"])
        self.assertFalse(report["qualification_authority"])
        self.assertIsNone(report["live_verified_completions"])

    def test_invalid_labels_still_leave_execution_report(self):
        labels = self.home / "labels.json"
        labels.write_text('{"version":"bad"}', encoding="utf-8")
        with patch.object(replay, "source_identity", return_value=IDENTITY), \
                patch.object(replay, "execute", return_value={"status": "timeout"}):
            report = replay.run(self.one_case(), self.home / "run", labels=[labels])
        self.assertEqual(report["evaluation"]["status"], "invalid-labels")
        self.assertTrue((self.home / "run/report.json").is_file())

    def test_budget_validation_rejects_runaway_and_nonfinite_values(self):
        for kwargs in ({"repeats": True}, {"repeats": 11}, {"per_case_seconds": float("nan")},
                       {"total_seconds": 0}, {"total_seconds": 3601}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                replay.run(INPUTS, self.home / "run", labels=[], **kwargs)

    @unittest.skipIf(os.name == "nt", "Factory rehearsal uses the Linux runtime configuration")
    def test_actual_isolated_children_exercise_all_ten_cases(self):
        expected = {case.case_id: case.expected for case in load_suite(LABELS).cases}
        for case in self.cases:
            with self.subTest(case=case.id):
                row = replay.execute(case, timeout=30)
                self.assertEqual(row["status"], "observed", row)
                self.assertEqual(row["outcome"], expected[case.id])
                if row["outcome"] == "merge":
                    self.assertEqual(row["level"], "simulated-orchestration")
                    self.assertTrue(row["evidence"]["merge_called"])
                    self.assertEqual(row["evidence"]["terminal"], "returned")


if __name__ == "__main__":
    unittest.main()
