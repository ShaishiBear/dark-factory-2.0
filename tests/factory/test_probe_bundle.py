"""Diagnostic launches metered through one validation bundle (WP02, C06): the real ProbeRunner
over a real meter scope, and the test-author probe script opening, threading and closing that
bundle around its three nested calls."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel.execution_probe import ProbeRunner
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.validation_meter import close_validation_scope, open_validation_scope
from tests.factory.test_validation_meter import BINDING, FakeLedger, digest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from tests.factory.test_factory_effort_and_stream_logs import _stream_with_thinking  # noqa: E402

ARGV = ["claude", "-p", "hello", "--bare", "--model", "actual/author", "--tools", "", "--max-turns", "1", "--max-budget-usd", "1"]


def completed(returncode=0, cost=0.0123):
    stream = _stream_with_thinking(100)
    if cost is None:
        stream = stream.replace('"total_cost_usd"', '"cost_omitted"')
    return SimpleNamespace(returncode=returncode, stdout=stream, stderr="")


class BundleLaunchTests(unittest.TestCase):
    def setUp(self):
        self.ledger = FakeLedger()
        self.scope = open_validation_scope(self.ledger, scope_class="diagnostic-probe", binding=BINDING, execution_id="probe-run", attempt=1,
                                           limit_microusd=3_000_000, max_calls=3, request_sha256=digest("bundle"))
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_each_launch_takes_a_one_dollar_ceiling_inside_the_bundle_and_is_observed(self):
        launches = []

        def runner(argv, **kwargs):
            launches.append((list(argv), kwargs))
            return completed()

        probe = ProbeRunner("diagnostic-test-author", None, scope=self.scope, runner=runner, result_dir=self.tmp)
        self.assertEqual(probe.metering, "metered-bundle")
        for cap in ("3000", "1024", "0"):
            probe(ARGV, env={"PATH": os.environ["PATH"], "MAX_THINKING_TOKENS": cap}, timeout=180)
        self.assertEqual(len(launches), 3)
        calls = list(self.scope.calls.values())
        self.assertEqual([c.state for c in calls], ["observed"] * 3)
        self.assertEqual([c.microusd for c in calls], [1_000_000] * 3)
        self.assertEqual([c.outcome for c in calls], ["returned"] * 3)
        self.assertTrue(all(isinstance(c.reported_microusd, int) for c in calls), "the CLI receipt is telemetry on the call")
        self.assertEqual(len({c.request_sha256 for c in calls}), 3, "each cap is a different request identity")
        record = probe.last_diagnostic
        self.assertEqual((record["metering"], record["status"]), ("metered-bundle", "returned"))
        self.assertIn(record["reservation_id"], self.scope.calls, "the launch's reservation identity is its metered call")
        self.assertEqual(len(list(self.tmp.glob("diagnostic-test-author-*.json"))), 3)
        summary = close_validation_scope(self.scope)
        self.assertEqual((summary["calls"], summary["unknown_calls"], summary["outcome"]), (3, 0, "returned"))
        self.assertEqual(self.ledger.observed[-1]["reported_microusd"], sum(c.reported_microusd for c in calls))

    def test_the_fourth_launch_is_refused_by_the_meter_before_any_process_exists(self):
        runner = Mock(return_value=completed())
        probe = ProbeRunner("diagnostic-test-author", None, scope=self.scope, runner=runner)
        for _ in range(3):
            probe(ARGV, env={"PATH": "x"}, timeout=1)
        with self.assertRaises(IntentRefused):
            probe(ARGV, env={"PATH": "x"}, timeout=1)
        self.assertEqual(runner.call_count, 3)
        self.assertEqual((probe.last_diagnostic["status"], probe.last_diagnostic["phase"], probe.last_diagnostic["provider_started"]),
                         ("refused", "reservation", "not_started"))

    def test_a_launch_that_dies_is_observed_as_failed_with_unknown_spend_and_the_bundle_stays_unknown(self):
        probe = ProbeRunner("diagnostic-test-author", None, scope=self.scope, runner=Mock(side_effect=subprocess.TimeoutExpired(ARGV, 1)))
        with self.assertRaises(subprocess.TimeoutExpired):
            probe(ARGV, env={"PATH": "x"}, timeout=1)
        call = next(iter(self.scope.calls.values()))
        self.assertEqual((call.state, call.outcome, call.reported_microusd, call.telemetry["provider_started"]), ("observed", "failed", None, "unknown"))
        summary = close_validation_scope(self.scope)
        self.assertEqual((summary["reported_microusd"], summary["unknown_calls"]), (None, 1))
        nonzero = ProbeRunner("diagnostic-test-author", None, scope=open_validation_scope(
            self.ledger, scope_class="diagnostic-probe", binding=BINDING, execution_id="probe-run-2", attempt=1,
            limit_microusd=1_000_000, max_calls=1, request_sha256=digest("b2")), runner=Mock(return_value=completed(returncode=2)))
        nonzero(ARGV, env={"PATH": "x"}, timeout=1)
        call = next(iter(nonzero.scope.calls.values()))
        self.assertEqual((call.outcome, call.reported_microusd), ("failed", None), "a nonzero exit cannot turn unknown spend into a receipt")

    def test_only_a_diagnostic_scope_and_the_protected_cli_bound_are_accepted(self):
        other = open_validation_scope(self.ledger, scope_class="validation-llm", binding=BINDING, execution_id="x", attempt=1,
                                      limit_microusd=10, max_calls=1, request_sha256=digest("x"))
        with self.assertRaises(IntentRefused):
            ProbeRunner("diagnostic-test-author", None, scope=other)
        probe = ProbeRunner("diagnostic-test-author", None, scope=self.scope, runner=Mock(return_value=completed()))
        with self.assertRaises(IntentRefused):
            probe([*ARGV[:-1], "5"], env={"PATH": "x"}, timeout=1)
        self.assertEqual(self.scope.calls, {}, "an unbounded launch never reaches the meter")


class ScriptWiringTests(unittest.TestCase):
    """main() with the metered runner: one bundle for three calls on the hosted exchange."""

    class Client:
        binding = dict(BINDING)

        def __init__(self):
            self.phases = []
            self.last_call_id = None

        def exchange(self, phase, call, payload):
            self.phases.append((phase, dict(call), dict(payload)))
            return {"reserve": {"status": "reserved", "project_version": 5}, "start": {"status": "start-once"},
                    "observe": {"status": "observed"}}[phase]

    def test_the_three_calls_run_inside_one_reserved_bundle_that_closes_with_the_record(self):
        import factory_test_author_probe as script

        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / ".factory").mkdir()
        (tmp / ".factory" / "kernel.json").write_text(json.dumps({"provider": {"model": "default/route", "model_overrides": {"test_author": "actual/author"}}}))
        client = self.Client()
        launches = []

        def fake_run(argv, **kwargs):
            launches.append(argv)
            return completed()

        runner = ProbeRunner(script.DIAGNOSTIC_ROLE, client, runner=fake_run)
        with patch.object(ProbeRunner, "from_environment", return_value=runner), patch.object(script, "verify_dispatch"), \
                patch.object(script.subprocess, "check_output", return_value="a" * 40), \
\
                patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "7", "RUNNER_TEMP": str(tmp), "PATH": os.environ["PATH"],
                                        "ANTHROPIC_AUTH_TOKEN": "fixture-api", "FACTORY_DIAGNOSTICS_DIR": str(tmp / "diag")}), \
                patch.object(script, "ROOT", tmp), contextlib.redirect_stdout(io.StringIO()) as out:
            script.main()
        record = json.loads((tmp / "test-author-route-diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual(len(launches), 3)
        phases = [row[0] for row in client.phases]
        self.assertEqual(phases, ["reserve", "start", "observe"], "one bundle on the exchange, not three reservations")
        bundle = client.phases[0][1]
        self.assertEqual((bundle["role"], bundle["microusd"], bundle["execution_id"], bundle["attempt"]), ("diagnostic-probe", 3_000_000, "test-author-route-7", 1))
        self.assertEqual(client.phases[2][2]["outcome"], "returned")
        self.assertEqual((record["metering"], record["bundle"]["calls"], record["bundle"]["limit_microusd"], record["bundle"]["unknown_calls"]),
                         ("metered-bundle", 3, 3_000_000, 0))
        self.assertTrue((tmp / "diag").is_dir() and list((tmp / "diag").glob("meter-*.json")), "the meter record is retained beside the diagnostics")
        self.assertIn('"metering": "metered-bundle"', out.getvalue())

    def test_without_an_authenticated_client_the_lane_stays_unmetered_and_says_so(self):
        import factory_test_author_probe as script

        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / ".factory").mkdir()
        (tmp / ".factory" / "kernel.json").write_text(json.dumps({"provider": {"model": "default/route", "model_overrides": {"test_author": "actual/author"}}}))
        runner = ProbeRunner(script.DIAGNOSTIC_ROLE, None, runner=lambda argv, **kwargs: completed(), metering="unmetered-local")
        with patch.object(ProbeRunner, "from_environment", return_value=runner), patch.object(script, "verify_dispatch"), \
                patch.object(script.subprocess, "check_output", return_value="a" * 40), \
                patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "7", "RUNNER_TEMP": str(tmp), "PATH": os.environ["PATH"],
                                        "ANTHROPIC_AUTH_TOKEN": "fixture-api"}), \
                patch.object(script, "ROOT", tmp), contextlib.redirect_stdout(io.StringIO()):
            script.main()
        record = json.loads((tmp / "test-author-route-diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual((record["metering"], record["bundle"]), ("unmetered-local", None))


if __name__ == "__main__":
    unittest.main()
