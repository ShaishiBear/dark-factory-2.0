"""A dependency is declared in the contract, rendered by the kernel, locked by the kernel.

The security guard refuses a manifest change unless the PR body carries `## Dependency
justification` naming each package, and the kernel writes every autonomous PR body. These tests
prove the chain end to end with the real programs: the contract compiler validates the
declaration fail-closed, the kernel renders it in the guard's own terms, the guard accepts the
rendered body, and the kernel refreshes exactly the planned lockfile.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.git_authority import (  # noqa: E402
    GitAuthorityError,
    commit_planned_changes,
    refresh_lockfiles,
)
from factory_kernel.pr_body import DEPENDENCY_HEADING, render_pr_body  # noqa: E402


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


protocol = load("factory_protocol")
security = load("factory_security")

RESEND = {
    "ecosystem": "python", "name": "resend",
    "purpose": "Sends transactional email through the Resend API.",
    "why_existing_insufficient": "No installed dependency speaks SMTP or an email API.",
    "maintenance_evidence": "Weekly releases and an active issue tracker as of 2026-09.",
}
ZOD = {
    "ecosystem": "javascript", "name": "zod",
    "purpose": "Validates API payloads at the fetch boundary with typed schemas.",
    "why_existing_insufficient": "The frontend has no runtime validator; TypeScript types are erased.",
    "maintenance_evidence": "Very widely used and released monthly as of 2026-09.",
}


def contract(**overrides) -> dict:
    value = {
        "version": "2.0",
        "issue": {"number": 42, "title": "Send a welcome email"},
        "summary": "Send a welcome email after signup using a transactional provider.",
        "behaviors": [{"id": "AC-1", "given": "a new signup", "when": "it completes",
                       "then": "one email is sent", "seam": "send_welcome_email()"}],
        "invariants": [], "out_of_scope": [], "risks": [], "ambiguities": [],
    }
    value.update(overrides)
    return value


class ContractDependencySchemaTests(unittest.TestCase):
    def test_contract_without_dependencies_is_unchanged(self):
        self.assertEqual(len(protocol.validate_contract(contract())), 64)
        self.assertEqual(len(protocol.validate_contract(contract(dependencies=[]))), 64)

    def test_complete_declarations_are_accepted_and_hashed(self):
        digest = protocol.validate_contract(contract(dependencies=[RESEND, ZOD]))
        self.assertNotEqual(digest, protocol.validate_contract(contract()))

    def test_thin_or_malformed_declarations_fail_closed(self):
        cases = {
            "not a list": "resend",
            "not an object": ["resend"],
            "bad ecosystem": [dict(RESEND, ecosystem="rust")],
            "missing purpose": [{k: v for k, v in RESEND.items() if k != "purpose"}],
            "thin purpose": [dict(RESEND, purpose="email")],
            "empty name": [dict(RESEND, name="  ")],
            "url name": [dict(RESEND, name="https://example.com/x.whl")],
            "duplicate": [RESEND, dict(RESEND, purpose="Sends email a second way for no reason.")],
        }
        for label, deps in cases.items():
            with self.subTest(label), self.assertRaises(SystemExit):
                protocol.validate_contract(contract(dependencies=deps))


class RenderingTests(unittest.TestCase):
    def test_no_dependencies_means_no_heading(self):
        body = render_pr_body(42, 1, contract())
        self.assertTrue(body.startswith("Fixes #42\n"))
        self.assertIn("<!-- dark-factory-attempt:1 -->", body)
        self.assertNotIn("Dependency justification", body)
        self.assertEqual(body, render_pr_body(42, 1, contract(dependencies=[])))

    def test_declared_dependencies_render_under_the_exact_heading(self):
        body = render_pr_body(42, 2, contract(dependencies=[RESEND, ZOD]))
        self.assertTrue(body.startswith("Fixes #42\n"))
        self.assertIn("<!-- dark-factory-attempt:2 -->", body)
        self.assertIn(f"\n{DEPENDENCY_HEADING}\n", body)
        self.assertEqual(DEPENDENCY_HEADING, "## Dependency justification")
        section = security.dependency_justification(body)
        for dep in (RESEND, ZOD):
            self.assertIn(dep["name"], section)
            self.assertIn(dep["purpose"], section)
            self.assertIn(dep["why_existing_insufficient"], section)
            self.assertIn(dep["maintenance_evidence"], section)


class GuardEndToEndTests(unittest.TestCase):
    """The rendered body is judged by the real guard on a real manifest diff."""

    @staticmethod
    def backend(deps):
        q = ", ".join(json.dumps(x) for x in deps)
        return f"[project]\nname='x'\nversion='0.1'\ndependencies=[{q}]\n"

    @staticmethod
    def frontend(deps):
        return json.dumps({"dependencies": deps, "devDependencies": {}})

    def evaluate(self, body, *, py=None, js=None):
        changed = ["app/backend/routes/channels.py"]
        head_backend = self.backend(["fastapi"])
        head_frontend = self.frontend({"react": "^18.3.1"})
        if py:
            changed += [security.BACKEND_MANIFEST, security.BACKEND_LOCK]
            head_backend = self.backend(["fastapi", py])
        if js:
            changed += [security.FRONTEND_MANIFEST, security.FRONTEND_LOCK]
            head_frontend = self.frontend({"react": "^18.3.1", js: "^1.0.0"})
        return security.evaluate(
            changed_files=sorted(changed),
            base_backend=self.backend(["fastapi"]), head_backend=head_backend,
            base_frontend=self.frontend({"react": "^18.3.1"}), head_frontend=head_frontend,
            diff="diff --git a/x b/x\n+++ b/app/backend/routes/channels.py\n+safe = True\n",
            body=body,
            author={"login": "github-actions[bot]", "type": "Bot", "association": "CONTRIBUTOR"},
            commits=[{"sha": "a" * 40, "author": {"login": "github-actions[bot]", "type": "Bot"},
                      "committer": {"login": "github-actions[bot]", "type": "Bot"}}],
        )

    def test_declared_python_and_javascript_packages_pass_the_guard(self):
        body = render_pr_body(42, 1, contract(dependencies=[RESEND, ZOD]))
        result = self.evaluate(body, py="resend", js="zod")
        self.assertEqual(result["verdict"], "pass", result["findings"])
        self.assertEqual(len(result["dependency_changes"]), 2)

    def test_an_undeclared_package_still_fails_the_guard(self):
        body = render_pr_body(42, 1, contract(dependencies=[RESEND]))
        result = self.evaluate(body, py="resend", js="zod")
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "dependency_justification" and "zod" in x["detail"]
                            for x in result["findings"]))

    def test_no_declaration_and_a_manifest_change_fails_the_guard(self):
        result = self.evaluate(render_pr_body(42, 1, contract()), py="resend")
        self.assertEqual(result["verdict"], "fail")


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


class LockfileRefreshTests(unittest.TestCase):
    MANIFEST = "app/backend/pyproject.toml"
    LOCK = "app/backend/uv.lock"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-lock-")
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "core.autocrlf", "false")
        for rel, text in {
            self.MANIFEST: "[project]\nname='x'\ndependencies=['fastapi']\n",
            self.LOCK: "version = 1\n",
            "app/backend/routes/channels.py": "safe = True\n",
            "tests/test_x.py": "pass\n",
        }.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.calls: list[tuple[Path, list[str]]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def runner_writing(self, *extra: str):
        def run(cwd: Path, argv: list[str]) -> None:
            self.calls.append((cwd, argv))
            (self.repo / self.LOCK).write_text("version = 2\nresend\n", encoding="utf-8", newline="\n")
            for rel in extra:
                (self.repo / rel).write_text("side effect\n", encoding="utf-8", newline="\n")
        return run

    def edit_manifest(self) -> None:
        (self.repo / self.MANIFEST).write_text(
            "[project]\nname='x'\ndependencies=['fastapi', 'resend']\n", encoding="utf-8", newline="\n"
        )

    def test_changed_manifest_refreshes_exactly_its_planned_lockfile(self):
        self.edit_manifest()
        refreshed = refresh_lockfiles(self.repo, [self.MANIFEST, self.LOCK], runner=self.runner_writing())
        self.assertEqual(refreshed, [self.LOCK])
        self.assertEqual(self.calls, [(self.repo / "app/backend", ["uv", "lock"])])
        self.assertIn(self.LOCK, git(self.repo, "status", "--porcelain"))

    def test_unchanged_manifest_runs_nothing(self):
        (self.repo / "app/backend/routes/channels.py").write_text("safe = False\n", encoding="utf-8")
        self.assertEqual(refresh_lockfiles(self.repo, [self.MANIFEST, self.LOCK], runner=self.runner_writing()), [])
        self.assertEqual(self.calls, [])

    def test_unplanned_lockfile_is_refused_before_any_command_runs(self):
        self.edit_manifest()
        with self.assertRaises(GitAuthorityError) as ctx:
            refresh_lockfiles(self.repo, [self.MANIFEST], runner=self.runner_writing())
        self.assertIn("not in the compiled design", str(ctx.exception))
        self.assertEqual(self.calls, [])

    def test_a_refresh_that_touches_anything_else_is_refused(self):
        self.edit_manifest()
        with self.assertRaises(GitAuthorityError) as ctx:
            refresh_lockfiles(self.repo, [self.MANIFEST, self.LOCK], runner=self.runner_writing("app/backend/.python-version"))
        self.assertIn("beyond", str(ctx.exception))

    def test_planned_changes_commit_includes_the_refreshed_lockfile(self):
        self.edit_manifest()
        artifacts = Path(self.tmp.name) / "artifacts"
        artifacts.mkdir()
        design = artifacts / "design.json"
        design.write_text(json.dumps({"planned_files": [self.MANIFEST, self.LOCK], "allowed_new_files": []}), encoding="utf-8")
        proof = artifacts / "red.json"
        proof.write_text(json.dumps({"files": {"tests/test_x.py": "f" * 64}}), encoding="utf-8")
        commit_planned_changes(
            self.repo, design_path=design, red_proof_path=proof,
            subject="fix(factory): satisfy issue #42", issue_number=42, lock_runner=self.runner_writing(),
        )
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(sorted(git(self.repo, "diff", "--name-only", "HEAD^", "HEAD").split()), [self.MANIFEST, self.LOCK])

    def test_planned_changes_commit_refuses_an_unplanned_lockfile(self):
        self.edit_manifest()
        artifacts = Path(self.tmp.name) / "artifacts"
        artifacts.mkdir()
        design = artifacts / "design.json"
        design.write_text(json.dumps({"planned_files": [self.MANIFEST], "allowed_new_files": []}), encoding="utf-8")
        proof = artifacts / "red.json"
        proof.write_text(json.dumps({"files": {}}), encoding="utf-8")
        with self.assertRaises(GitAuthorityError):
            commit_planned_changes(
                self.repo, design_path=design, red_proof_path=proof,
                subject="fix(factory): satisfy issue #42", issue_number=42, lock_runner=self.runner_writing(),
            )
        self.assertEqual(self.calls, [])


class KernelWiringTests(unittest.TestCase):
    def test_build_issue_renders_the_pr_body_from_the_compiled_contract(self):
        tree = ast.parse((ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8"))
        build = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_issue")
        calls = [n for n in ast.walk(build)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "render_pr_body"]
        self.assertEqual(len(calls), 1)
        source_arg = calls[0].args[2]
        self.assertIsInstance(source_arg, ast.Call, "contract must be read from the compiled artifact")
        self.assertIn("task-contract.json", ast.unparse(source_arg))


if __name__ == "__main__":
    unittest.main()
