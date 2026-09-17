"""Every diagnostic launch leaves a typed record that says what it knows and what it does not.

A refusal before launch, a process the interpreter could not create, a timeout after start and
an envelope the provider returned malformed are four different facts. Until WP00 they all
reached the workflow log as `stop_reason=unreadable` plus a traceback cut off before its
exception class. These tests pin the record's strict shape, the phase each failure is
attributed to, the three-valued `provider_started`, the absence of secrets, atomic retention
outside any run directory, and the CLI wrapper's `finally` path (WP00, C01, C06).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel import execution_probe
from factory_kernel.execution_exchange import ROLE_BOUNDS
from factory_kernel.execution_probe import DIAGNOSTICS_DIR_ENV, ProbeRunner, diagnostic, main, write_diagnostic
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.refusal import (
    DIAGNOSTIC_REASON_CODES, DIAGNOSTIC_ROLES, PROVIDER_START, classify_diagnostic, diagnostic_summary,
    validate_diagnostic,
)
from factory_kernel import publication_policy as policy
from tests.factory import test_execution_exchange as exchange_tests

ARGV = ["claude", "--bare", "--max-budget-usd", "1", "--output-format", "json"]
RECEIPT = json.dumps({"type": "result", "total_cost_usd": 0.01})
SECRET = "sk-ant-" + "a" * 40


def completed(rc=0, stdout=RECEIPT, stderr=""):
    return subprocess.CompletedProcess(ARGV, rc, stdout, stderr)


class RecordShapeTests(unittest.TestCase):
    def valid(self, **overrides):
        record = diagnostic(invocation_id="0" * 32, role="diagnostic-route", phase="returned", status="returned",
                            reason_code="returned", provider_started="started", metering="metered",
                            reservation_id="1" * 32, exit_code=0, output=RECEIPT, receipts=[json.loads(RECEIPT)])
        record.update(overrides)
        return record

    def test_a_built_record_validates_and_reports_integer_microusd(self):
        record = self.valid()
        self.assertEqual(validate_diagnostic(record), record)
        self.assertEqual((record["reported_microusd"], record["cost_reason"], record["uncertainty"]), (10000, "reported", []))
        self.assertEqual(record["output_bytes"], len(RECEIPT))

    def test_strict_shape_refuses_unknown_keys_bad_enums_and_booleans_as_integers(self):
        cases = (
            {"extra": 1}, {"phase": "flight"}, {"status": "ok"}, {"reason_code": "provider_unreachable"},
            {"provider_started": True}, {"provider_started": "no"}, {"metering": "free"}, {"exit_code": True},
            {"exit_code": "0"}, {"output_bytes": -1}, {"invocation_id": "xyz"}, {"reservation_id": "short"},
            {"uncertainty": ["provider_start_unknown", "cost_unknown"]}, {"uncertainty": ["novel"]},
            {"uncertainty": "cost_unknown"}, {"schema_version": "2.0"}, {"error_message": "x" * 2001},
            {"traceback": "t" * 8001}, {"output_sha256": None}, {"reported_microusd": 1.5},
            {"status": "refused"}, {"reason_code": "timeout"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_diagnostic(self.valid(**overrides))
        missing = self.valid()
        del missing["traceback"]
        with self.assertRaises(ValueError):
            validate_diagnostic(missing)

    def test_every_enumeration_is_closed_and_the_summary_shows_identity_fields_only(self):
        self.assertEqual(PROVIDER_START, ("not_started", "started", "unknown"))
        self.assertNotIn("provider_unreachable", DIAGNOSTIC_REASON_CODES, "unreadable output is not an outage")
        record = self.valid(error_type="RuntimeError", error_message=SECRET, traceback="private " + SECRET,
                            status="failed", reason_code="unexpected_exception")
        summary = diagnostic_summary(record)
        self.assertTrue(summary.startswith("FACTORY_DIAGNOSTIC role=diagnostic-route phase=returned"))
        self.assertIn("error_type=RuntimeError", summary)
        self.assertNotIn("private", summary)
        self.assertNotIn(SECRET, summary)
        self.assertIn("diagnostic-test-author", DIAGNOSTIC_ROLES)
        self.assertEqual(ROLE_BOUNDS["diagnostic-test-author"], 1)

    def test_classification_reads_classes_and_phase_never_text(self):
        self.assertEqual(classify_diagnostic("admission", IntentRefused("x")), ("refused", "admission_refused"))
        self.assertEqual(classify_diagnostic("reservation", IntentRefused("x")), ("refused", "reservation_refused"))
        self.assertEqual(classify_diagnostic("identity", IntentRefused("x")), ("refused", "identity_refused"))
        self.assertEqual(classify_diagnostic("launch", FileNotFoundError("claude")), ("failed", "launch_failed"))
        self.assertEqual(classify_diagnostic("launch", subprocess.TimeoutExpired(ARGV, 1)), ("failed", "timeout"))
        self.assertEqual(classify_diagnostic("launch", RuntimeError("model unreachable: outage")),
                         ("failed", "unexpected_exception"))
        self.assertEqual(classify_diagnostic("returned", None, result=completed(0), receipts=[{}]), ("returned", "returned"))
        self.assertEqual(classify_diagnostic("returned", None, result=completed(1), receipts=[{}]), ("returned", "provider_error"))
        self.assertEqual(classify_diagnostic("returned", None, result=completed(0), receipts=[]),
                         ("returned", "provider_envelope_malformed"))
        with self.assertRaises(ValueError):
            classify_diagnostic("returned", None)


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.exchange = exchange_tests.ExchangeTests()
        self.exchange.setUp()
        self.addCleanup(self.exchange.doCleanups)
        opener = Mock()
        opener.open.side_effect = self.exchange.transport
        self.client = self.exchange.client(opener)

    def state(self):
        case = self.exchange.case
        return case.budget.snapshot("citations", principal=case.owner)

    def test_admission_refusal_launches_nothing_and_is_typed(self):
        runner = Mock()
        probe = ProbeRunner("diagnostic-route", self.client, runner=runner)
        with self.assertRaises(IntentRefused):
            probe(["claude"])
        record = probe.last_diagnostic
        self.assertEqual((record["phase"], record["status"], record["reason_code"], record["provider_started"]),
                         ("admission", "refused", "admission_refused", "not_started"))
        self.assertEqual((record["cost_reason"], record["reservation_id"], record["uncertainty"]), ("not_launched", None, []))
        runner.assert_not_called()
        self.assertEqual(self.state()["calls"], 0)

    def test_reservation_refusal_differs_from_a_malformed_provider_envelope(self):
        runner = Mock(return_value=completed(0, ""))
        probe = ProbeRunner("diagnostic-route", self.client, runner=runner)
        probe(ARGV)
        malformed = probe.last_diagnostic
        self.assertEqual((malformed["phase"], malformed["status"], malformed["reason_code"], malformed["provider_started"]),
                         ("returned", "returned", "provider_envelope_malformed", "started"))
        self.assertEqual((malformed["cost_reason"], malformed["uncertainty"]), ("no_receipt", ["cost_unknown"]))
        self.assertRegex(malformed["reservation_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(malformed["output_bytes"], 0)
        with self.assertRaises(IntentRefused):
            probe(ARGV)
        refused = probe.last_diagnostic
        self.assertEqual((refused["phase"], refused["status"], refused["reason_code"], refused["provider_started"]),
                         ("reservation", "refused", "reservation_refused", "not_started"))
        self.assertEqual(refused["error_type"], "IntentRefused")
        self.assertNotEqual(refused["invocation_id"], malformed["invocation_id"])
        runner.assert_called_once()

    def test_launch_failure_is_not_started_and_timeout_is_unknown(self):
        probe = ProbeRunner("diagnostic-route", self.client, runner=Mock(side_effect=FileNotFoundError("claude")))
        with self.assertRaises(FileNotFoundError):
            probe(ARGV)
        record = probe.last_diagnostic
        self.assertEqual((record["phase"], record["status"], record["reason_code"], record["provider_started"]),
                         ("launch", "failed", "launch_failed", "not_started"))
        self.assertEqual(record["uncertainty"], [])
        fixture = exchange_tests.ExchangeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        opener = Mock()
        opener.open.side_effect = fixture.transport
        probe = ProbeRunner("diagnostic-route", fixture.client(opener), runner=Mock(side_effect=subprocess.TimeoutExpired(ARGV, 1)))
        with self.assertRaises(subprocess.TimeoutExpired):
            probe(ARGV)
        record = probe.last_diagnostic
        self.assertEqual((record["phase"], record["status"], record["reason_code"], record["provider_started"]),
                         ("launch", "failed", "timeout", "unknown"))
        self.assertEqual(record["uncertainty"], ["cost_unknown", "provider_start_unknown"])
        self.assertEqual(fixture.case.budget.snapshot("citations", principal=fixture.case.owner)["status"], "unresolved-attempt")

    def test_unexpected_exception_is_retained_with_its_class_and_never_recast(self):
        probe = ProbeRunner("diagnostic-route", self.client, runner=Mock(side_effect=RuntimeError("boom " + SECRET)))
        with self.assertRaises(RuntimeError):
            probe(ARGV)
        record = probe.last_diagnostic
        self.assertEqual((record["status"], record["reason_code"], record["error_type"]), ("failed", "unexpected_exception", "RuntimeError"))
        self.assertIn("boom", record["traceback"])
        self.assertIn("RuntimeError", record["traceback"])
        self.assertNotIn(SECRET, json.dumps(record))
        self.assertIn("[REDACTED]", record["error_message"])

    def test_returned_receipt_binds_the_reservation_and_reports_microusd(self):
        probe = ProbeRunner("diagnostic-effort", self.client, runner=Mock(return_value=completed()))
        result = probe(ARGV)
        self.assertEqual(result.stdout, RECEIPT)
        record = probe.last_diagnostic
        self.assertEqual((record["phase"], record["status"], record["reason_code"], record["provider_started"], record["metering"]),
                         ("returned", "returned", "returned", "started", "metered"))
        self.assertEqual((record["reported_microusd"], record["cost_reason"], record["exit_code"]), (10000, "reported", 0))
        self.assertEqual(record["reservation_id"], self.client.last_call_id)
        self.assertEqual([key for key in self.state()["reservations"] if key.endswith(record["reservation_id"])],
                         ["worker-" + record["reservation_id"]])
        nonzero = ProbeRunner("diagnostic-scope", self.client, runner=Mock(return_value=completed(2)))
        nonzero(ARGV)
        self.assertEqual((nonzero.last_diagnostic["reason_code"], nonzero.last_diagnostic["cost_reason"]),
                         ("provider_error", "nonzero_exit"))


class RetentionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)

    def test_record_is_written_atomically_to_a_path_that_does_not_exist_yet(self):
        path = self.directory / "no" / "run" / "dir" / "route.json"
        probe = ProbeRunner("diagnostic-route", None, runner=Mock(return_value=completed()), result_path=path)
        probe(ARGV)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(validate_diagnostic(record), probe.last_diagnostic)
        self.assertEqual(record["metering"], "unknown")
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["route.json"], "no temporary file survives")
        with self.assertRaises(ValueError):
            write_diagnostic(path, {**record, "extra": 1})

    def test_environment_directory_names_one_file_per_launch_and_holds_no_secret(self):
        env = {"PATH": "bin", "ANTHROPIC_AUTH_TOKEN": SECRET, "GH_TOKEN": "ghp_" + "b" * 36,
               "FRONTDOOR_AGE_IDENTITY": "host", DIAGNOSTICS_DIR_ENV: str(self.directory / "diag")}
        runner = Mock(side_effect=[completed(), RuntimeError("leak " + env["GH_TOKEN"] + " " + SECRET)])
        with patch.dict(os.environ, env, clear=True):
            probe = ProbeRunner.from_environment("diagnostic-thinking")
            self.assertEqual(probe.metering, "unmetered-local")
            probe.runner = runner
            probe(ARGV)
            with self.assertRaises(RuntimeError):
                probe(ARGV)
        files = sorted((self.directory / "diag").iterdir())
        self.assertEqual(len(files), 2)
        for file in files:
            self.assertRegex(file.name, r"^diagnostic-thinking-[0-9a-f]{32}\.json$")
            text = file.read_text(encoding="utf-8")
            for secret in (SECRET, env["GH_TOKEN"], "host"):
                self.assertNotIn(secret, text)
            validate_diagnostic(json.loads(text))
        maintenance = policy.REPOSITORY + "/.github/workflows/dark-factory-main-regression.yml@refs/heads/main"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": maintenance}, clear=True):
            self.assertEqual(ProbeRunner.from_environment("diagnostic-route").metering, "unmetered-maintenance-scope")


class MainTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "diagnostics" / "route.json"

    def run_main(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except BaseException as exc:  # noqa: BLE001 - the wrapper re-raises on purpose
                return exc, out.getvalue(), err.getvalue()
        return code, out.getvalue(), err.getvalue()

    def test_identity_refusal_under_the_workers_conditions_is_retained_and_summarised_first(self):
        env = {"PATH": os.environ["PATH"], "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": policy.REPOSITORY,
               "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "1", "GITHUB_SHA": "a" * 40,
               "GITHUB_WORKFLOW_REF": policy.REPOSITORY + "/.github/workflows/dark-factory-worker.yml@refs/heads/main"}
        outcome, stdout, stderr = self.run_main(["--result-path", str(self.path), "--", "timeout", "180", "claude", *ARGV[1:]], env)
        self.assertIsInstance(outcome, IntentRefused)
        self.assertEqual(stdout, "")
        first = stderr.splitlines()[0]
        self.assertTrue(first.startswith("FACTORY_DIAGNOSTIC role=diagnostic-route phase=identity status=refused "
                                         "reason_code=identity_refused provider_started=not_started"), first)
        self.assertLess(len(first.encode()), 400, "the head the workflow keeps already names the phase and class")
        record = validate_diagnostic(json.loads(self.path.read_text(encoding="utf-8")))
        self.assertEqual((record["phase"], record["error_type"], record["metering"]), ("identity", "IntentRefused", "unknown"))
        self.assertIn("IntentRefused", record["traceback"])

    def test_legacy_delimiter_forwards_output_and_exit_code_and_records_the_launch(self):
        runner = Mock(return_value=subprocess.CompletedProcess(["x"], 3, "out " + RECEIPT, "err"))
        with patch.object(ProbeRunner, "from_environment",
                          side_effect=lambda role, result_path=None: ProbeRunner(role, None, runner=runner, result_path=result_path)):
            code, stdout, stderr = self.run_main(["--", "timeout", "180", "claude"], {"PATH": "bin"})
            self.assertEqual((code, stdout), (3, "out " + RECEIPT))
            self.assertTrue(stderr.startswith("FACTORY_DIAGNOSTIC "))
            self.assertTrue(stderr.endswith("err"))
            self.assertFalse(self.path.exists())
            code, _stdout, _stderr = self.run_main(["--result-path", str(self.path), "--", "timeout", "180", "claude"], {"PATH": "bin"})
            self.assertEqual(code, 3)
            self.assertEqual(json.loads(self.path.read_text())["reason_code"], "provider_error")
        self.assertEqual(runner.call_args.args[0], ["timeout", "180", "claude"])
        self.assertEqual(runner.call_args.kwargs["timeout"], 190)

    def test_malformed_invocations_refuse_before_any_runner_exists(self):
        with patch.object(ProbeRunner, "from_environment", side_effect=AssertionError("must not construct")):
            for argv in ([], ["timeout", "180", "claude"], ["--", "x"], ["--result-path"], ["--result-path", "--", "a", "b", "c"],
                         ["--result-path", "", "--", "a", "b", "c"]):
                with self.subTest(argv=argv):
                    outcome, _o, _e = self.run_main(argv, {"PATH": "bin"})
                    self.assertIsInstance(outcome, IntentRefused)
        self.assertFalse(self.path.exists())

    def test_forwarded_streams_are_scrubbed(self):
        runner = Mock(return_value=subprocess.CompletedProcess(["x"], 0, RECEIPT, "token " + SECRET))
        with patch.object(ProbeRunner, "from_environment",
                          side_effect=lambda role, result_path=None: ProbeRunner(role, None, runner=runner)):
            _code, _stdout, stderr = self.run_main(["--", "timeout", "180", "claude"], {"PATH": "bin"})
        self.assertNotIn(SECRET, stderr)
        self.assertIn("[REDACTED]", stderr)
        self.assertIs(execution_probe.subprocess.run, subprocess.run)


if __name__ == "__main__":
    unittest.main()
