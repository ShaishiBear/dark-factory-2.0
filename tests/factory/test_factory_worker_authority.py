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


class WorkerPolicyTests(unittest.TestCase):
    def test_no_model_role_receives_shell_or_git_authority(self):
        for role, tools in ROLE_TOOLS.items():
            self.assertFalse(any(tool == "Bash" or tool.startswith("Bash(") for tool in tools), role)

    def test_only_explicit_code_roles_may_dirty_checkout(self):
        self.assertTrue(may_change_repo("test_author"))
        self.assertTrue(may_change_repo("implement"))
        self.assertTrue(may_change_repo("repair"))
        self.assertFalse(may_change_repo("architecture"))
        self.assertFalse(may_change_repo("review"))
        self.assertEqual(allowed_tools("architecture-holdout"), ())

    def test_cli_uses_controlled_worker_runtime(self):
        rt = cli_runtime(ROOT / ".factory/kernel.json")
        self.assertIsInstance(rt, WorkerControlledRuntime)


class ProviderBoundaryTests(unittest.TestCase):
    @patch("factory_kernel.providers.subprocess.run")
    def test_headless_worker_has_fixed_tool_surface_and_no_github_secrets(self, run):
        run.return_value = Mock(returncode=0, stdout="done\n", stderr="")
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
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Edit,Write")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Edit,Write")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--disable-slash-commands", argv)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "model-auth")
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertEqual(env["FACTORY_REPO"], "owner/repo")


class GitAuthorityTests(unittest.TestCase):
    def test_test_author_commit_is_exact_declared_test_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            (root / "tests/test_value.py").write_text("def test_value():\n    assert False\n", encoding="utf-8")
            spec = root / "spec.json"
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
            commit_acceptance_tests(root, spec)
            self.assertEqual(git(root, "status", "--porcelain"), "")
            self.assertEqual(git(root, "diff", "--name-only", "HEAD^", "HEAD"), "tests/test_value.py")

    def test_test_author_cannot_smuggle_product_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            (root / "tests/test_value.py").write_text("def test_value():\n    assert False\n", encoding="utf-8")
            (root / "app/backend/value.py").write_text("VALUE = 999\n", encoding="utf-8")
            spec = root / "spec.json"
            spec.write_text(
                json.dumps({"version": "2.0", "checkpoints": [{"files": ["tests/test_value.py"]}]}),
                encoding="utf-8",
            )
            with self.assertRaises(GitAuthorityError):
                commit_acceptance_tests(root, spec)

    def test_implementation_commit_is_limited_to_compiled_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            (root / "app/backend/value.py").write_text("VALUE = 2\n", encoding="utf-8")
            design = root / "design.json"
            design.write_text(
                json.dumps({"planned_files": ["app/backend/value.py"], "allowed_new_files": []}),
                encoding="utf-8",
            )
            proof = root / "red.json"
            proof.write_text(
                json.dumps({"files": {"tests/test_value.py": "f" * 64}}), encoding="utf-8"
            )
            commit_planned_changes(
                root,
                design_path=design,
                red_proof_path=proof,
                subject="fix(factory): satisfy issue #7",
                issue_number=7,
            )
            self.assertEqual(git(root, "status", "--porcelain"), "")
            self.assertEqual(git(root, "diff", "--name-only", "HEAD^", "HEAD"), "app/backend/value.py")

    def test_implementation_cannot_edit_outside_design_or_immutable_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            design = root / "design.json"
            design.write_text(
                json.dumps({"planned_files": ["app/backend/value.py"], "allowed_new_files": []}),
                encoding="utf-8",
            )
            proof = root / "red.json"
            proof.write_text(
                json.dumps({"files": {"tests/test_value.py": "f" * 64}}), encoding="utf-8"
            )
            (root / "README.md").write_text("smuggled\n", encoding="utf-8")
            with self.assertRaises(GitAuthorityError):
                commit_planned_changes(
                    root,
                    design_path=design,
                    red_proof_path=proof,
                    subject="fix(factory): satisfy issue #7",
                    issue_number=7,
                )
            (root / "README.md").unlink()
            (root / "tests/test_value.py").write_text("def test_value():\n    assert False\n", encoding="utf-8")
            with self.assertRaises(GitAuthorityError):
                commit_planned_changes(
                    root,
                    design_path=design,
                    red_proof_path=proof,
                    subject="fix(factory): satisfy issue #7",
                    issue_number=7,
                )


if __name__ == "__main__":
    unittest.main()
