"""Adversarial coverage for the one-time genesis authority.

The PR that rewrites the factory trust root cannot be certified by that trust root: running the
candidate's own harness from the candidate's own tree proves self-consistency, not trustworthiness.
These tests pin the properties that make the genesis verifier a real authority rather than another
component of the thing it is judging.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
VERIFIER = ROOT / "harness" / "bootstrap_verify.py"
MANIFEST_REL = ".factory/bootstrap/genesis.json"

PREFIXES = ["factory_kernel/", "harness/", "tests/factory/"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=120
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


class GenesisRepo:
    """A miniature repository shaped like the real one, so mutations stay off the live tree."""

    def __init__(self, tmp: Path) -> None:
        self.path = tmp / "repo"
        (self.path / "harness").mkdir(parents=True)
        (self.path / "factory_kernel").mkdir(parents=True)
        (self.path / "tests" / "factory").mkdir(parents=True)
        (self.path / ".factory" / "bootstrap").mkdir(parents=True)
        shutil.copy2(VERIFIER, self.path / "harness" / "bootstrap_verify.py")
        (self.path / "factory_kernel" / "spine.py").write_text("POLICY = 1\n", encoding="utf-8")
        (self.path / "factory_kernel" / "independence.py").write_text("REG = ()\n", encoding="utf-8")
        (self.path / "tests" / "factory" / "test_x.py").write_text("assert True\n", encoding="utf-8")
        git(self.path, "init", "-q", "-b", "main")
        git(self.path, "config", "user.email", "genesis@example.invalid")
        git(self.path, "config", "user.name", "Genesis")
        git(self.path, "add", "-A")
        git(self.path, "commit", "-qm", "base")
        self.base = git(self.path, "rev-parse", "HEAD").strip()

    def verifier_sha(self) -> str:
        return sha256((self.path / "harness" / "bootstrap_verify.py").read_bytes())

    def inventory(self) -> dict[str, str]:
        head = git(self.path, "rev-parse", "HEAD").strip()
        listing = git(self.path, "ls-tree", "-r", "--name-only", head).splitlines()
        return {
            p: sha256(
                subprocess.run(
                    ["git", "cat-file", "blob", f"{head}:{p}"],
                    cwd=self.path, capture_output=True, timeout=60,
                ).stdout
            )
            for p in listing
            if any(p.startswith(x) for x in PREFIXES)
        }

    def manifest(self, **overrides) -> dict:
        files = self.inventory()
        value = {
            "version": "1.0",
            "base_sha": self.base,
            "verifier_sha256": self.verifier_sha(),
            "trust_root_prefixes": PREFIXES,
            "trust_root": files,
            "policy_sha256": {"factory_kernel/spine.py": files["factory_kernel/spine.py"]},
            "observed": {
                "focused_tests": 289,
                "unit_tests": 766,
                "static_checks": 5,
                "factory_mutations": {"total": 81, "caught": 81, "not_injected": 0},
                "application_mutations": {"total": 9, "caught": 9, "not_injected": 0},
            },
            "authorization": {
                "one_time": True,
                "approved_by": "repository owner",
                "reason": "genesis: this PR replaces the machinery that governs future PRs",
            },
        }
        value.update(overrides)
        return value

    def write_manifest(self, manifest: dict) -> str:
        raw = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
        (self.path / MANIFEST_REL).write_bytes(raw)
        git(self.path, "add", "-A")
        git(self.path, "commit", "-qm", "genesis manifest")
        return sha256(raw)

    def run(self, *, verifier=None, manifest=None, candidate=None):
        head = git(self.path, "rev-parse", "HEAD").strip()
        raw = subprocess.run(
            ["git", "cat-file", "blob", f"{head}:{MANIFEST_REL}"],
            cwd=self.path, capture_output=True, timeout=60,
        ).stdout
        return subprocess.run(
            [
                sys.executable, "harness/bootstrap_verify.py",
                "--expect-verifier", verifier or self.verifier_sha(),
                "--expect-manifest", manifest or sha256(raw),
                "--expect-candidate", candidate or head,
            ],
            cwd=self.path, capture_output=True, text=True, timeout=180,
        )


class BootstrapAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = GenesisRepo(Path(self.tmp.name))

    def test_valid_genesis_is_authorized(self):
        self.repo.write_manifest(self.repo.manifest())
        proc = self.repo.run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("BOOTSTRAP_OK", proc.stdout)

    def test_verifier_imports_nothing_from_the_trust_root_it_certifies(self):
        """A verifier that imported the machinery could be steered by it."""
        source = VERIFIER.read_text(encoding="utf-8")
        for banned in ("factory_kernel", "factory_security", "harness.", "from scripts"):
            self.assertNotIn(f"import {banned}", source)
            self.assertNotIn(f"from {banned}", source)

    def test_modified_verifier_is_refused(self):
        """The candidate must not be able to silently edit its own certifier."""
        self.repo.write_manifest(self.repo.manifest())
        target = self.repo.path / "harness" / "bootstrap_verify.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
        proc = self.repo.run(verifier=self.repo.verifier_sha())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("BOOTSTRAP_REFUSED", proc.stderr)

    def test_manifest_that_was_not_authorized_is_refused(self):
        self.repo.write_manifest(self.repo.manifest())
        proc = self.repo.run(manifest="0" * 64)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the one that was authorized", proc.stderr)

    def test_authorization_does_not_carry_to_another_commit(self):
        """The one-time exception is scoped to exactly one commit."""
        self.repo.write_manifest(self.repo.manifest())
        first = git(self.repo.path, "rev-parse", "HEAD").strip()
        (self.repo.path / "README.md").write_text("later\n", encoding="utf-8")
        git(self.repo.path, "add", "-A")
        git(self.repo.path, "commit", "-qm", "a later commit")
        proc = self.repo.run(candidate=first)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the authorized candidate", proc.stderr)

    def test_unlisted_trust_root_file_is_refused(self):
        """A new authority file smuggled in outside the manifest must fail closed."""
        manifest = self.repo.manifest()
        self.repo.write_manifest(manifest)
        (self.repo.path / "factory_kernel" / "sneaky.py").write_text("BYPASS = True\n", encoding="utf-8")
        git(self.repo.path, "add", "-A")
        git(self.repo.path, "commit", "-qm", "smuggle")
        raw = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
        proc = self.repo.run(manifest=sha256(raw))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unlisted", proc.stderr)

    def test_trust_root_file_content_change_is_refused(self):
        manifest = self.repo.manifest()
        manifest["trust_root"]["factory_kernel/spine.py"] = "1" * 64
        self.repo.write_manifest(manifest)
        proc = self.repo.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not match", proc.stderr)

    def test_manifest_naming_a_different_verifier_is_refused(self):
        self.repo.write_manifest(self.repo.manifest(verifier_sha256="2" * 64))
        proc = self.repo.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not name this verifier", proc.stderr)

    def test_pinned_policy_outside_the_trust_root_is_refused(self):
        manifest = self.repo.manifest()
        manifest["policy_sha256"] = {"README.md": "3" * 64}
        self.repo.write_manifest(manifest)
        proc = self.repo.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("outside the trust root", proc.stderr)

    def test_escaped_or_uninjected_mutation_evidence_is_refused(self):
        for family, block, expected in (
            ("factory_mutations", {"total": 81, "caught": 80, "not_injected": 0}, "escape"),
            ("factory_mutations", {"total": 81, "caught": 81, "not_injected": 2}, "inject"),
            ("application_mutations", {"total": 0, "caught": 0, "not_injected": 0}, "ran nothing"),
        ):
            with self.subTest(family=family, block=block):
                repo = GenesisRepo(Path(tempfile.mkdtemp(dir=self.tmp.name)))
                manifest = repo.manifest()
                manifest["observed"][family] = block
                repo.write_manifest(manifest)
                proc = repo.run()
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(expected, proc.stderr)

    def test_authorization_must_be_one_time_and_attributed(self):
        for override, expected in (
            ({"one_time": False, "approved_by": "x", "reason": "y"}, "not marked one-time"),
            ({"one_time": True, "approved_by": "", "reason": "y"}, "names no approver"),
            ({"one_time": True, "approved_by": "x", "reason": ""}, "states no reason"),
        ):
            with self.subTest(override=override):
                repo = GenesisRepo(Path(tempfile.mkdtemp(dir=self.tmp.name)))
                repo.write_manifest(repo.manifest(authorization=override))
                proc = repo.run()
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(expected, proc.stderr)

    def test_all_three_human_values_are_required(self):
        self.repo.write_manifest(self.repo.manifest())
        head = git(self.repo.path, "rev-parse", "HEAD").strip()
        proc = subprocess.run(
            [sys.executable, "harness/bootstrap_verify.py", "--expect-candidate", head],
            cwd=self.repo.path, capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("supplied by a human", proc.stderr)


class BootstrapIsNotMergeAuthorityTests(unittest.TestCase):
    """The exception must not become a route around the ordinary guards."""

    def test_no_merge_path_consumes_the_genesis_manifest(self):
        for rel in (
            "harness/merge_verify.py",
            "factory_kernel/evidence_closure.py",
            "factory_kernel/spine.py",
            "factory_kernel/runtime.py",
            "factory_kernel/worker_runtime.py",
            "scripts/factory_evidence.py",
            "scripts/factory_evidence_spine.py",
        ):
            source = (ROOT / rel).read_text(encoding="utf-8")
            with self.subTest(path=rel):
                self.assertNotIn("bootstrap", source.lower())

    def test_ordinary_trust_root_change_still_fails_closed(self):
        """Genesis authorizes one commit; it grants nothing to the next trust-root change."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("fs", ROOT / "scripts/factory_security.py")
        fs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fs)
        for path in ("factory_kernel/independence.py", "harness/merge_verify.py"):
            self.assertTrue(fs.protected_path(path), path)


if __name__ == "__main__":
    unittest.main()
