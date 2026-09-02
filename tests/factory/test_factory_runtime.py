import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel.agents import AgentRequest
from factory_kernel.config import ProviderConfig, load_config
from factory_kernel.providers import ClaudeCliProvider
from factory_kernel.runtime import FactoryStopped, KernelRuntime


ROOT = Path(__file__).parents[2]


class ConfigTests(unittest.TestCase):
    def test_checked_in_kernel_config_is_valid(self):
        config = load_config(ROOT / ".factory/kernel.json")
        self.assertEqual(config.repository, "ShaishiBear/dark-factory-2.0")
        self.assertEqual(config.provider.provider_id, "claude-cli")
        self.assertEqual(config.provider.model, "z-ai/glm-5.3-flash")
        self.assertEqual(config.provider.architecture_model, "deepseek/deepseek-v4-pro-0813")
        self.assertNotEqual(config.provider.model, config.provider.architecture_model)
        self.assertEqual(config.validation.full_command, ("python", "harness/ci.py"))
        for role in config.prompts:
            self.assertTrue(config.prompt_path(role, ROOT).is_file())

    def test_prompt_escape_is_rejected(self):
        raw = json.loads((ROOT / ".factory/kernel.json").read_text(encoding="utf-8"))
        raw["prompts"]["plan"] = "../outside.md"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kernel.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "safe repo-relative"):
                load_config(path)


class ProviderTests(unittest.TestCase):
    @patch("factory_kernel.providers.subprocess.run")
    def test_claude_cli_is_a_worker_not_an_authority(self, run):
        run.return_value = Mock(returncode=0, stdout="done\n", stderr="")
        provider = ClaudeCliProvider(
            ProviderConfig(
                provider_id="claude-cli", binary="claude", model="sonnet", timeout_seconds=60
            )
        )
        result = provider.run(
            AgentRequest(role="implement", prompt="do the task", cwd="/tmp", environment={"X": "1"})
        )
        self.assertEqual(result.provider_id, "claude-cli")
        self.assertEqual(result.content, "done")
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "claude")
        self.assertIn("--bare", argv)
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "do the task")
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")

    @patch("factory_kernel.providers.subprocess.run")
    def test_architecture_holdout_routes_to_independent_model(self, run):
        run.return_value = Mock(returncode=0, stdout='{"version":"1.0"}\n', stderr="")
        provider = ClaudeCliProvider(
            ProviderConfig(
                provider_id="claude-cli",
                binary="claude",
                model="sonnet",
                architecture_model="opus",
                timeout_seconds=60,
            )
        )
        result = provider.run(
            AgentRequest(
                role="architecture-holdout",
                prompt="judge architecture",
                cwd="/tmp",
                model="sonnet",
                structured_schema={"type": "object"},
            )
        )
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(result.model, "opus")

    @patch("factory_kernel.providers.subprocess.run")
    def test_worker_failure_fails_closed(self, run):
        run.return_value = Mock(returncode=7, stdout="", stderr="bad")
        provider = ClaudeCliProvider(
            ProviderConfig(
                provider_id="claude-cli", binary="claude", model="sonnet", timeout_seconds=60
            )
        )
        with self.assertRaisesRegex(RuntimeError, "agent worker failed"):
            provider.run(AgentRequest(role="review", prompt="review", cwd="/tmp"))


class FakeGitHub:
    def __init__(self, prs=None, issues=None):
        self.prs = prs or []
        self.issues = issues or []
        self.calls = []

    def list_prs(self, label):
        self.calls.append(("prs", label))
        return self.prs

    def list_issues(self, label):
        self.calls.append(("issues", label))
        return self.issues

    @staticmethod
    def labels(value):
        return {item["name"] for item in value.get("labels", [])}


class DispatchTests(unittest.TestCase):
    def runtime(self, github):
        rt = KernelRuntime(repo_root=ROOT, config=load_config(ROOT / ".factory/kernel.json"))
        rt.github = github
        return rt

    def test_dispatch_checks_stop_then_reaps_before_selecting_work(self):
        github = FakeGitHub(prs=[{"number": 8, "updatedAt": "2026-01-01", "labels": []}])
        rt = self.runtime(github)
        order = []
        rt.check_stop = lambda: order.append("stop")
        rt.reap_stale_claims = lambda: order.append("reap")
        decision = rt.choose_dispatch()
        self.assertEqual(order, ["stop", "reap"])
        self.assertEqual(decision.kind, "validate-pr")
        self.assertEqual(decision.number, 8)
        self.assertEqual(github.calls[0][0], "prs")

    def test_pr_review_has_priority_over_accepted_issue(self):
        github = FakeGitHub(
            prs=[{"number": 12, "updatedAt": "2026-01-02", "labels": []}],
            issues=[{"number": 42, "updatedAt": "2026-01-01", "labels": [{"name": "factory:accepted"}]}],
        )
        rt = self.runtime(github)
        rt.check_stop = Mock()
        rt.reap_stale_claims = Mock()
        decision = rt.choose_dispatch()
        self.assertEqual((decision.kind, decision.number), ("validate-pr", 12))
        self.assertFalse(any(call[0] == "issues" for call in github.calls))

    def test_in_progress_accepted_issue_is_not_double_dispatched(self):
        github = FakeGitHub(
            issues=[{
                "number": 42,
                "updatedAt": "2026-01-01",
                "labels": [{"name": "factory:accepted"}, {"name": "factory:in-progress"}],
            }]
        )
        rt = self.runtime(github)
        rt.check_stop = Mock()
        rt.reap_stale_claims = Mock()
        decision = rt.choose_dispatch()
        self.assertEqual(decision.kind, "idle")

    @patch("factory_kernel.runtime.subprocess.run")
    def test_unreadable_stop_state_fails_closed(self, run):
        run.return_value = Mock(returncode=1, stdout="STOPPED: cannot read stop state", stderr="")
        rt = self.runtime(FakeGitHub())
        with self.assertRaisesRegex(FactoryStopped, "cannot read stop state"):
            rt.check_stop()


class AttachedEvidenceTests(unittest.TestCase):
    def test_contract_and_proof_blocks_parse_without_discussion_context(self):
        body = (
            "Fixes #4\n"
            "<!-- factory-contract:start -->\n```factory-contract\n{\"version\":\"2.0\"}\n```\n"
            "contract-sha256: " + "a" * 64 + "\n<!-- factory-contract:end -->\n"
            "<!-- factory-proof:start -->\n```factory-proof\n{\"version\":\"2.0\"}\n```\n"
            "proof-sha256: " + "b" * 64 + "\n<!-- factory-proof:end -->\n"
        )
        contract, proof = KernelRuntime._extract_attached(body)
        self.assertEqual(contract["version"], "2.0")
        self.assertEqual(proof["version"], "2.0")


if __name__ == "__main__":
    unittest.main()
