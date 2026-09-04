from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel.agents import AgentRequest
from factory_kernel.cli import runtime as cli_runtime
from factory_kernel.config import ProviderConfig
from factory_kernel.credential_env import scoped_environment
from factory_kernel.github_cli import GitHubClient
from factory_kernel.runtime import KernelRuntime
from factory_kernel.git_authority import (
    GitAuthorityError,
    commit_acceptance_tests,
    commit_planned_changes,
)
from factory_kernel.providers import ClaudeCliProvider
from factory_kernel.worker_policy import ROLE_TOOLS, allowed_tools, may_change_repo
from factory_kernel.worker_runtime import WorkerControlledRuntime

ROOT = Path(__file__).parents[2]


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def init_repo(root: Path) -> None:
    git(root, "init", "-q")
    (root / "app/backend").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app/backend/value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests/test_value.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    git(root, "add", ".")
    git(
        root,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid",
        "commit", "-qm", "initial",
    )


def fixture_layout(tmp: str) -> tuple[Path, Path]:
    base = Path(tmp)
    repo = base / "repo"
    artifacts = base / "artifacts"
    repo.mkdir()
    artifacts.mkdir()
    init_repo(repo)
    return repo, artifacts


class WorkerPolicyTests(unittest.TestCase):
    def test_no_model_role_receives_shell_or_git_authority(self):
        for role, tools in ROLE_TOOLS.items():
            self.assertFalse(any(tool == "Bash" or tool.startswith("Bash(") for tool in tools), role)

    def test_only_explicit_code_roles_may_dirty_checkout(self):
        self.assertTrue(may_change_repo("test_author"))
        self.assertTrue(may_change_repo("implement"))
        self.assertTrue(may_change_repo("repair"))
        self.assertFalse(may_change_repo("architecture"))
        self.assertFalse(may_change_repo("review-spec"))
        self.assertFalse(may_change_repo("review-standards"))
        self.assertEqual(allowed_tools("architecture-holdout"), ())

    def test_cli_uses_controlled_worker_runtime(self):
        rt = cli_runtime(ROOT / ".factory/kernel.json")
        self.assertIsInstance(rt, WorkerControlledRuntime)


class ProviderBoundaryTests(unittest.TestCase):
    @patch("factory_kernel.providers.subprocess.run")
    def test_headless_worker_has_fixed_tool_surface_and_no_github_secrets(self, run):
        run.return_value = Mock(returncode=0, stdout='{"type": "result", "subtype": "success", "is_error": false, "result": "done", "num_turns": 1, "duration_ms": 10, "total_cost_usd": 0.0, "session_id": "s", "usage": {"input_tokens": 1, "output_tokens": 1}}', stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            artifacts.mkdir()
            config = ProviderConfig(
                provider_id="claude-cli",
                binary="claude",
                model="sonnet",
                architecture_model="opus",
                timeout_seconds=60,
            )
            provider = ClaudeCliProvider(config)
            with patch.dict(
                os.environ,
                {
                    "PATH": "/usr/bin",
                    "HOME": "/tmp/home",
                    "ANTHROPIC_API_KEY": "model-auth",
                    "GH_TOKEN": "must-not-leak",
                    "DATABASE_URL": "must-not-leak-either",
                },
                clear=True,
            ):
                provider.run(
                    AgentRequest(
                        role="implement",
                        prompt="edit the planned file",
                        cwd=tmp,
                        allowed_tools=("Read", "Edit", "Write"),
                        environment={
                            "ARTIFACTS_DIR": str(artifacts),
                            "FACTORY_REPO": "owner/repo",
                        },
                    )
                )
        argv = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertIn("--bare", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Edit,Write")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Edit,Write")
        self.assertNotIn("Bash", argv[argv.index("--tools") + 1])
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--disable-slash-commands", argv)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "model-auth")
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertEqual(env["FACTORY_REPO"], "owner/repo")

    @patch("factory_kernel.providers.subprocess.run")
    def test_no_tool_worker_stays_noninteractive_and_tool_empty(self, run):
        run.return_value = Mock(returncode=0, stdout='{"type": "result", "subtype": "success", "is_error": false, "result": "{\\"version\\":\\"1.0\\"}", "num_turns": 1, "duration_ms": 10, "total_cost_usd": 0.0, "session_id": "s", "usage": {"input_tokens": 1, "output_tokens": 1}}', stderr="")
        provider = ClaudeCliProvider(
            ProviderConfig(
                provider_id="claude-cli", binary="claude", model="sonnet", timeout_seconds=60
            )
        )
        provider.run(
            AgentRequest(
                role="architecture-holdout",
                prompt="judge",
                cwd="/tmp",
                structured_schema={"type": "object"},
                allowed_tools=(),
            )
        )
        argv = run.call_args.args[0]
        self.assertIn("--bare", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", argv)


class CredentialScopeTests(unittest.TestCase):
    def source(self):
        return {
            "PATH": "/usr/bin",
            "NORMAL": "safe",
            "GH_TOKEN": "github-write",
            "DATABASE_URL": "validation-db",
            "OPENROUTER_API_KEY": "validation-llm",
            "ANTHROPIC_API_KEY": "model-auth",
        }

    def test_scoped_environment_requires_explicit_capability(self):
        none = scoped_environment(source=self.source())
        self.assertEqual(none["NORMAL"], "safe")
        self.assertNotIn("GH_TOKEN", none)
        self.assertNotIn("DATABASE_URL", none)
        self.assertNotIn("OPENROUTER_API_KEY", none)
        self.assertNotIn("ANTHROPIC_API_KEY", none)

        github = scoped_environment(scope="github", source=self.source())
        self.assertEqual(github["GH_TOKEN"], "github-write")
        self.assertNotIn("DATABASE_URL", github)
        self.assertNotIn("ANTHROPIC_API_KEY", github)

        validation = scoped_environment(scope="validation", source=self.source())
        self.assertEqual(validation["DATABASE_URL"], "validation-db")
        self.assertEqual(validation["OPENROUTER_API_KEY"], "validation-llm")
        self.assertNotIn("GH_TOKEN", validation)
        self.assertNotIn("ANTHROPIC_API_KEY", validation)

        both = scoped_environment(scope="github+validation", source=self.source())
        self.assertEqual(both["GH_TOKEN"], "github-write")
        self.assertEqual(both["DATABASE_URL"], "validation-db")
        self.assertNotIn("ANTHROPIC_API_KEY", both)

        with self.assertRaises(ValueError):
            scoped_environment({"GH_TOKEN": "smuggled"}, source=self.source())

    @patch("factory_kernel.runtime.subprocess.run")
    def test_kernel_exec_defaults_to_zero_credentials(self, run):
        run.return_value = Mock(returncode=0, stdout="ok", stderr="")
        runtime = object.__new__(KernelRuntime)
        with patch.dict(os.environ, self.source(), clear=True):
            runtime._exec(["true"], cwd=Path("/tmp"))
        env = run.call_args.kwargs["env"]
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    @patch("factory_kernel.github_cli.subprocess.run")
    def test_git_push_token_is_ephemeral_child_capability(self, run):
        run.return_value = Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            client = GitHubClient("owner/repo", cwd=tmp)
            with patch.dict(os.environ, self.source(), clear=True):
                client.push_branch("factory/issue-7")
        argv = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertNotIn("github-write", " ".join(argv))
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["FACTORY_GIT_TOKEN"], "github-write")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("GIT_ASKPASS", env)


class GitHubWorkerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/dark-factory-worker.yml").read_text(encoding="utf-8")

    def test_scheduler_is_only_schedule_or_manual_and_never_cancels_active_authority(self):
        self.assertIn("  schedule:\n", self.workflow)
        self.assertIn("  workflow_dispatch:\n", self.workflow)
        self.assertNotIn("pull_request_target:", self.workflow)
        self.assertNotIn("\n  pull_request:\n", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn("timeout-minutes: 300", self.workflow)

    def test_scheduler_does_not_persist_write_token_into_checkout(self):
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertNotIn("persist-credentials: true", self.workflow)

    def test_scheduler_permissions_are_explicit_and_worker_tools_are_pinned(self):
        for permission in ("contents: write", "issues: write", "pull-requests: write"):
            self.assertIn(permission, self.workflow)
        for ref in (
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
            "astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e",
            "oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6",
            "@anthropic-ai/claude-code@2.1.245",
            "agent-browser@0.35.0",
        ):
            self.assertIn(ref, self.workflow)
        self.assertGreaterEqual(self.workflow.count("token: ''"), 3)
        self.assertIn("github-token: ''", self.workflow)

    def test_scheduler_fails_closed_before_exact_one_shot_dispatch(self):
        self.assertIn("FACTORY_PREFLIGHT_REFUSED GitHub Issues are disabled", self.workflow)
        for name in (
            "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "DATABASE_URL", "OPENROUTER_API_KEY",
            "JWT_SECRET", "SUPADATA_API_KEY", "YOUTUBE_CHANNEL_ID", "DARK_FACTORY_E2E_EMAIL",
            "DARK_FACTORY_E2E_PASSWORD",
        ):
            self.assertIn(name, self.workflow)
        # Model calls leave the runner for OpenRouter's Anthropic-compatible endpoint. The SDK
        # appends /v1/messages to the base, so the base is the API root; a versioned base sent
        # every call to /api/v1/v1/messages (D-010). A silently misrouted or unauthorised
        # endpoint would degrade every model judgement in the run instead of stopping it, so the
        # preflight proves that exact route, with the pinned CLI, before any ladder is spent.
        self.assertIn("ANTHROPIC_BASE_URL: https://openrouter.ai/api\n", self.workflow)
        self.assertNotIn("openrouter.ai/api/v1", self.workflow)
        self.assertIn(
            "FACTORY_PREFLIGHT_REFUSED OpenRouter messages endpoint returned", self.workflow
        )
        self.assertIn("FACTORY_PREFLIGHT_REFUSED worker CLI cannot reach model", self.workflow)
        self.assertNotIn("api.anthropic.com", self.workflow)
        self.assertIn("run: python -m factory_kernel dispatch --once", self.workflow)
        self.assertNotIn("dispatch --once --no-merge", self.workflow)



class GitAuthorityTests(unittest.TestCase):
    def test_test_author_commit_is_exact_declared_test_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, artifacts = fixture_layout(tmp)
            (repo / "tests/test_value.py").write_text(
                "def test_value():\n    assert False\n", encoding="utf-8"
            )
            spec = artifacts / "spec.json"
            spec.write_text(
                json.dumps(
                    {
                        "version": "2.0",
                        "checkpoints": [
                            {
                                "acceptance_id": "AC-1",
                                "cwd": ".",
                                "argv": ["python", "-m", "pytest", "tests/test_value.py"],
                                "files": ["tests/test_value.py"],
                                "expected_failure": "assert False",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            commit_acceptance_tests(repo, spec)
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual(
                git(repo, "diff", "--name-only", "HEAD^", "HEAD"), "tests/test_value.py"
            )

    def test_test_author_cannot_smuggle_product_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, artifacts = fixture_layout(tmp)
            (repo / "tests/test_value.py").write_text(
                "def test_value():\n    assert False\n", encoding="utf-8"
            )
            (repo / "app/backend/value.py").write_text("VALUE = 999\n", encoding="utf-8")
            spec = artifacts / "spec.json"
            spec.write_text(
                json.dumps({"version": "2.0", "checkpoints": [{"files": ["tests/test_value.py"]}]}),
                encoding="utf-8",
            )
            with self.assertRaises(GitAuthorityError):
                commit_acceptance_tests(repo, spec)

    def test_implementation_commit_is_limited_to_compiled_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, artifacts = fixture_layout(tmp)
            (repo / "app/backend/value.py").write_text("VALUE = 2\n", encoding="utf-8")
            design = artifacts / "design.json"
            design.write_text(
                json.dumps({"planned_files": ["app/backend/value.py"], "allowed_new_files": []}),
                encoding="utf-8",
            )
            proof = artifacts / "red.json"
            proof.write_text(
                json.dumps({"files": {"tests/test_value.py": "f" * 64}}), encoding="utf-8"
            )
            commit_planned_changes(
                repo,
                design_path=design,
                red_proof_path=proof,
                subject="fix(factory): satisfy issue #7",
                issue_number=7,
            )
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual(
                git(repo, "diff", "--name-only", "HEAD^", "HEAD"), "app/backend/value.py"
            )

    def test_implementation_cannot_edit_outside_design_or_immutable_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, artifacts = fixture_layout(tmp)
            design = artifacts / "design.json"
            design.write_text(
                json.dumps({"planned_files": ["app/backend/value.py"], "allowed_new_files": []}),
                encoding="utf-8",
            )
            proof = artifacts / "red.json"
            proof.write_text(
                json.dumps({"files": {"tests/test_value.py": "f" * 64}}), encoding="utf-8"
            )
            (repo / "README.md").write_text("smuggled\n", encoding="utf-8")
            with self.assertRaises(GitAuthorityError):
                commit_planned_changes(
                    repo,
                    design_path=design,
                    red_proof_path=proof,
                    subject="fix(factory): satisfy issue #7",
                    issue_number=7,
                )
            (repo / "README.md").unlink()
            (repo / "tests/test_value.py").write_text(
                "def test_value():\n    assert False\n", encoding="utf-8"
            )
            with self.assertRaises(GitAuthorityError):
                commit_planned_changes(
                    repo,
                    design_path=design,
                    red_proof_path=proof,
                    subject="fix(factory): satisfy issue #7",
                    issue_number=7,
                )


if __name__ == "__main__":
    unittest.main()
