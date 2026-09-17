"""The diagnostic observes the actual role route without product or GitHub authority."""
import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from factory_test_author_probe import diagnostic, verify_dispatch  # noqa: E402
from tests.factory.test_factory_effort_and_stream_logs import _stream_with_thinking  # noqa: E402


class TestAuthorRouteProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.policy = Path(tmp.name) / "kernel.json"
        self.policy.write_text(json.dumps({"provider": {"model": "default/route",
                                                      "model_overrides": {"test_author": "actual/author"}}}))
        self.source = {"PATH": os.environ["PATH"], "HOME": "/private-owner-home", "GH_TOKEN": "fixture-github",
                       "ANTHROPIC_AUTH_TOKEN": "fixture-api", "ANTHROPIC_BASE_URL": "https://elsewhere.example"}
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        env = kwargs["env"]
        self.assertEqual(argv[argv.index("--model") + 1], "actual/author")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "1")
        self.assertIn("--bare", argv)
        self.assertEqual(kwargs["timeout"], 180)
        self.assertEqual(set(env) - {"MAX_THINKING_TOKENS"},
                         {"PATH", "HOME", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
                          "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"})
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://openrouter.ai/api")
        self.assertNotEqual(env["HOME"], self.source["HOME"])
        self.assertEqual(Path.cwd(), Path(env["HOME"]))
        self.assertEqual(list(Path.cwd().iterdir()), [])
        thinking = {None: 3000, "1024": 1000, "0": 0}[env.get("MAX_THINKING_TOKENS")]
        return SimpleNamespace(returncode=0, stdout=_stream_with_thinking(thinking))

    def test_actual_role_route_three_fixed_calls_with_no_inherited_credentials_or_scope(self):
        original = Path.cwd()
        record = diagnostic(self.policy, source=self.source, runner=self.runner)
        self.assertEqual(Path.cwd(), original)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(record["model"], "actual/author")
        self.assertEqual(record["calls"], 3)
        self.assertTrue(record["cap1024_honoured"])
        self.assertTrue(record["cap1024_exercised"])
        self.assertTrue(record["cap0_honoured"])
        self.assertFalse(record["qualifies_work"])
        self.assertFalse(record["changes_policy"])
        self.assertIsNone(record["actual_cost_usd"])
        self.assertNotIn("fixture-api", json.dumps(record))
        self.assertTrue(all(not Path(kwargs["env"]["HOME"]).exists() for _, kwargs in self.calls))

    def test_failed_measurements_do_not_prove_a_cap_or_retry(self):
        def timeout(argv, **kwargs):
            self.runner(argv, **kwargs)
            raise subprocess.TimeoutExpired(argv, 180, output="")
        record = diagnostic(self.policy, source=self.source, runner=timeout)
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(record["cap1024_honoured"])
        self.assertFalse(record["cap0_honoured"])
        self.assertTrue(all(not row["returned"] for row in record["measurements"]))
        self.assertFalse(record["cap1024_exercised"])

    def test_observed_small_samples_do_not_demonstrate_enforcement(self):
        def observed(argv, **kwargs):
            self.runner(argv, **kwargs)
            thinking = {None: 352, "1024": 200, "0": 382}[kwargs["env"].get("MAX_THINKING_TOKENS")]
            return SimpleNamespace(returncode=0, stdout=_stream_with_thinking(thinking))
        record = diagnostic(self.policy, source=self.source, runner=observed)
        self.assertFalse(record["cap1024_exercised"])
        self.assertFalse(record["cap1024_honoured"])
        self.assertFalse(record["cap0_honoured"])
        self.assertEqual(len(self.calls), 3)

    def test_missing_api_credential_refuses_before_any_model_call(self):
        self.source.pop("ANTHROPIC_AUTH_TOKEN")
        with self.assertRaisesRegex(ValueError, "explicit API"):
            diagnostic(self.policy, source=self.source, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_owner_source_and_first_attempt_are_required(self):
        source = {"GITHUB_REPOSITORY": "ShaishiBear/dark-factory-2.0", "GITHUB_REPOSITORY_OWNER": "ShaishiBear",
                  "GITHUB_ACTOR": "ShaishiBear", "GITHUB_TRIGGERING_ACTOR": "ShaishiBear",
                  "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
                  "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_REF":
                  "ShaishiBear/dark-factory-2.0/.github/workflows/dark-factory-test-author-probe.yml@refs/heads/main"}
        verify_dispatch(source)
        for key in source:
            wrong = deepcopy(source)
            wrong[key] = "other"
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_dispatch(wrong)

    def test_workflow_is_manual_read_only_and_binds_its_protected_source(self):
        workflow = (ROOT / ".github/workflows/dark-factory-test-author-probe.yml").read_text()
        for required in ("workflow_dispatch:", "contents: read", "ref: ${{ github.sha }}",
                         "github.triggering_actor == github.repository_owner", "github.run_attempt == 1",
                         "persist-credentials: false", "timeout 600 python scripts/factory_test_author_probe.py"):
            self.assertIn(required, workflow)
        for forbidden in ("contents: write", "issues: write", "pull-requests: write", "schedule:",
                          "create-github-app-token", "workflow run", "factory:accepted", "continue-on-error:"):
            self.assertNotIn(forbidden, workflow)

    def test_the_lane_is_metered_or_disabled_never_an_unmetered_paid_path(self):
        """WP00/C06: three paid calls ran here through a raw subprocess with the model credential
        handed to them by the workflow. Now the script launches only through the metered
        runner, and the workflow withholds the credential until an authenticated scope exists."""
        from factory_kernel.execution_probe import ProbeRunner
        from factory_kernel.frontdoor_intent import IntentRefused
        import factory_test_author_probe as script

        source = (ROOT / "scripts/factory_test_author_probe.py").read_text()
        self.assertIn('runner = ProbeRunner.from_environment(DIAGNOSTIC_ROLE)', source)
        self.assertIn("source=os.environ, runner=runner)", source)
        self.assertEqual(script.DIAGNOSTIC_ROLE, "diagnostic-test-author")
        workflow = (ROOT / ".github/workflows/dark-factory-test-author-probe.yml").read_text()
        for forbidden in ("secrets.OPENROUTER_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FRONTDOOR_AGE_IDENTITY"):
            self.assertNotIn(forbidden, workflow)
        self.assertIn("FACTORY_DIAGNOSTICS_DIR", workflow)
        ref = "ShaishiBear/dark-factory-2.0/.github/workflows/dark-factory-test-author-probe.yml@refs/heads/main"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": ref,
                                     "GITHUB_REPOSITORY": "ShaishiBear/dark-factory-2.0",
                                     "GITHUB_RUN_ATTEMPT": "1"}, clear=True):
            with self.assertRaises(IntentRefused):
                ProbeRunner.from_environment(script.DIAGNOSTIC_ROLE)
        # The metered runner is threaded through every nested call, not only the first.
        with patch.object(ProbeRunner, "from_environment", return_value=self.runner) as connect, \
                patch.object(script, "verify_dispatch"), patch.object(script.subprocess, "check_output", return_value="a" * 40), \
                patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "7", "RUNNER_TEMP": str(self.policy.parent),
                                        "PATH": os.environ["PATH"], "ANTHROPIC_AUTH_TOKEN": "fixture-api"}), \
                patch.object(script, "ROOT", self.policy.parent), contextlib.redirect_stdout(io.StringIO()):
            (self.policy.parent / ".factory").mkdir(exist_ok=True)
            (self.policy.parent / ".factory" / "kernel.json").write_text(self.policy.read_text())
            script.main()
        connect.assert_called_once_with("diagnostic-test-author")
        self.assertEqual(len(self.calls), 3)


if __name__ == "__main__":
    unittest.main()
