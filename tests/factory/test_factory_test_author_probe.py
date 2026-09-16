"""The diagnostic observes the actual role route without product or GitHub authority."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

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


if __name__ == "__main__":
    unittest.main()
