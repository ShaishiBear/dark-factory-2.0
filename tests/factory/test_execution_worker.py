"""Paid worker and diagnostic entry points require durable, single-use permission."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel.config import load_config
from factory_kernel.execution_probe import ProbeRunner
from factory_kernel.execution_worker import ExecutionWorker
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.runtime import KernelRuntime
from factory_kernel import publication_policy as policy
from tests.factory import test_execution_exchange as exchange_tests

ROOT = Path(__file__).resolve().parents[2]
ARGV = ["claude", "--bare", "--max-budget-usd", "1", "--output-format", "json"]


class WorkerExecutionTests(unittest.TestCase):
    def setUp(self):
        self.exchange = exchange_tests.ExchangeTests()
        self.exchange.setUp()
        self.addCleanup(self.exchange.doCleanups)
        self.opener = Mock()
        self.opener.open.side_effect = self.exchange.transport
        self.client = self.exchange.client(self.opener)

    def state(self):
        case = self.exchange.case
        return case.budget.snapshot("citations", principal=case.owner)

    def effect(self, *_args, **_kwargs):
        rows = list(self.state()["reservations"].values())
        self.assertIsNotNone(rows[-1]["started"])
        return SimpleNamespace(cost_usd=0.1)

    def test_runtime_wraps_real_provider_and_keeps_read_only_construction_free(self):
        with patch("factory_kernel.execution_worker.ExecutionClient.from_environment") as connect, patch.dict(
                os.environ, {"FACTORY_WORKDIR": str(self.exchange.case.directory)}):
            runtime = KernelRuntime(repo_root=ROOT, config=load_config(ROOT / ".factory/kernel.json"))
            connect.assert_not_called()
        self.assertIsInstance(runtime.provider, ExecutionWorker)
        self.assertEqual(runtime.provider.provider_id, "claude-cli")

    def test_worker_roles_share_one_project_allowance_and_stop_before_fourth_call(self):
        provider = Mock()
        provider.run.side_effect = self.effect
        worker = ExecutionWorker(provider, Mock())
        with patch("factory_kernel.execution_worker.ExecutionClient.from_environment", return_value=self.client) as connect:
            for role in ("triage", "implement", "holdout"):
                worker.run(replace(self.exchange.agent_request(), role=role))
            with self.assertRaises(IntentRefused):
                worker.run(self.exchange.agent_request())
            connect.assert_called_once()
        self.assertEqual(provider.run.call_count, 3)
        self.assertEqual(self.state()["reserved_microusd"], 3_000_000)

    def test_missing_capability_cannot_fall_back_to_raw_provider(self):
        provider = Mock()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(IntentRefused):
            ExecutionWorker(provider, Mock()).run(self.exchange.agent_request())
        provider.run.assert_not_called()

    def test_each_probe_leg_reserves_before_launch_and_preserves_stream(self):
        raw = json.dumps({"type": "result", "total_cost_usd": 0.01})
        def effect(*args, **kwargs):
            self.effect()
            return subprocess.CompletedProcess(args[0], 0, raw, "diagnostic stderr")
        runner = Mock(side_effect=effect)
        for role in ("diagnostic-effort", "diagnostic-thinking", "diagnostic-scope"):
            result = ProbeRunner(role, self.client, runner=runner)(ARGV, capture_output=True, timeout=180)
            self.assertEqual(result.stdout, raw)
        with self.assertRaises(IntentRefused):
            ProbeRunner("diagnostic-route", self.client, runner=runner)(ARGV)
        self.assertEqual(runner.call_count, 3)

    def test_missing_ambiguous_failed_and_overrun_probe_cost_blocks_following_leg(self):
        # Independent fixtures because an unresolved reservation cannot be reset.
        for stdout, rc in (("", 0), ('{"type":"result"}\n{"type":"result"}', 0),
                           ('{"type":"result","total_cost_usd":0}', 1),
                           ('{"type":"result","total_cost_usd":2}', 0)):
            with self.subTest(stdout=stdout, rc=rc):
                fixture = exchange_tests.ExchangeTests()
                fixture.setUp()
                try:
                    opener = Mock()
                    opener.open.side_effect = fixture.transport
                    runner = Mock(return_value=subprocess.CompletedProcess(ARGV, rc, stdout, ""))
                    probe = ProbeRunner("diagnostic-route", fixture.client(opener), runner=runner)
                    probe(ARGV)
                    with self.assertRaises(IntentRefused):
                        probe(ARGV)
                    runner.assert_called_once()
                finally:
                    fixture.doCleanups()

    def test_probe_timeout_never_retries_and_retains_unknown_charge(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired(ARGV, 1))
        probe = ProbeRunner("diagnostic-route", self.client, runner=runner)
        with self.assertRaises(subprocess.TimeoutExpired):
            probe(ARGV)
        with self.assertRaises(IntentRefused):
            probe(ARGV)
        runner.assert_called_once()
        self.assertEqual(self.state()["status"], "unresolved-attempt")

    def test_probe_secret_filter_applies_to_explicit_environment(self):
        env = {"PATH": "bin", "ANTHROPIC_AUTH_TOKEN": "model", "GH_TOKEN": "platform",
               "FRONTDOOR_AGE_IDENTITY": "host", "OPENROUTER_API_KEY": "validation",
               "MAX_THINKING_TOKENS": "1024", "ARTIFACTS_DIR": "artifacts"}
        runner = Mock(return_value=subprocess.CompletedProcess(ARGV, 0,
            '{"type":"result","total_cost_usd":0}', ""))
        with patch.dict(os.environ, env, clear=True):
            ProbeRunner("diagnostic-thinking", self.client, runner=runner)(ARGV, env=env)
        self.assertEqual(runner.call_args.kwargs["env"], {key: env[key] for key in
            ("PATH", "ANTHROPIC_AUTH_TOKEN", "MAX_THINKING_TOKENS", "ARTIFACTS_DIR")})

    def test_worker_probe_cannot_use_missing_or_foreign_workflow_as_maintenance(self):
        for ref in ("", policy.REPOSITORY + "/.github/workflows/dark-factory-worker.yml@refs/heads/main", "foreign"):
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": ref}, clear=True):
                with self.assertRaises(IntentRefused):
                    ProbeRunner.from_environment("diagnostic-route")
        maintenance = policy.REPOSITORY + "/.github/workflows/dark-factory-main-regression.yml@refs/heads/main"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": maintenance}, clear=True):
            self.assertIsNone(ProbeRunner.from_environment("diagnostic-route").client)

    def test_probe_cannot_charge_a_smaller_bound_than_its_cli(self):
        runner = Mock()
        probe = ProbeRunner("diagnostic-route", self.client, runner=runner)
        for argv in (["claude"], ["claude", "--max-budget-usd"], ["claude", "--max-budget-usd", "2"],
                     ARGV + ["--max-budget-usd", "2"]):
            with self.subTest(argv=argv), self.assertRaises(IntentRefused):
                probe(argv)
        runner.assert_not_called()
        self.assertEqual(self.state()["calls"], 0)

    def test_probe_reservation_roles_have_independent_protected_bounds(self):
        for role in ("diagnostic-route", "diagnostic-effort", "diagnostic-thinking", "diagnostic-scope"):
            self.exchange.call.update(role=role, microusd=1_000_001)
            with self.assertRaises(IntentRefused):
                self.exchange.request()

    def test_workflow_wires_capability_only_to_trusted_paid_entry_points(self):
        text = (ROOT / ".github/workflows/dark-factory-worker.yml").read_text()
        self.assertNotIn('routing_code="$(curl', text)
        self.assertIn("python -m factory_kernel.execution_probe -- timeout 180 claude", text)
        names = []
        for part in text.split("\n      - name: ")[1:]:
            if "FRONTDOOR_AGE_IDENTITY:" in part:
                names.append(part.splitlines()[0])
                self.assertIn("GH_TOKEN: ${{ github.token }}", part)
        self.assertEqual(names, ["Prove the worker's model route with the pinned CLI",
            "Probe whether the route honours an effort level", "Probe whether the route honours a thinking budget",
            "Prove the worker's read scope with the pinned CLI", "Dispatch exactly one factory action"])
        for name, role in (("effort", "effort"), ("thinking_cap", "thinking"), ("read_scope", "scope")):
            script = (ROOT / f"scripts/factory_{name}_probe.py").read_text()
            self.assertIn(f'runner=ProbeRunner.from_environment("diagnostic-{role}")', script)


if __name__ == "__main__":
    unittest.main()
