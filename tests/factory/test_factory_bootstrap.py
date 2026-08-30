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
        # The human's reviewed copy lives outside the repository under test.
        self.external_verifier = tmp / "reviewed_bootstrap_verify.py"
        shutil.copy2(VERIFIER, self.external_verifier)
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
        return sha256(self.external_verifier.read_bytes())

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
            "version": "2.0",
            "base_sha": self.base,
            "verifier_sha256": self.verifier_sha(),
            "trust_root_prefixes": PREFIXES,
            "trust_root": files,
            "policy_sha256": {"factory_kernel/spine.py": files["factory_kernel/spine.py"]},
            "evidence_requirements": {
                "required_markers": ["STATIC_OK", "FACTORY_MUTATIONS_OK"],
                "minimum": {"focused_tests": 100, "unit_tests": 500, "static_checks": 5},
                "mutation_families": ["factory_mutations", "application_mutations"],
            },
        }
        value.update(overrides)
        return value

    def evidence(self, commit: str, **overrides) -> dict:
        value = {
            "candidate_sha": commit,
            "markers": ["STATIC_OK", "UNIT_PASSED", "FACTORY_MUTATIONS_OK"],
            "focused_tests": 311,
            "unit_tests": 766,
            "static_checks": 5,
            "factory_mutations": {"total": 91, "caught": 91, "not_injected": 0},
            "application_mutations": {"total": 9, "caught": 9, "not_injected": 0},
        }
        value.update(overrides)
        return value

    def write_manifest(self, manifest: dict) -> str:
        raw = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
        (self.path / MANIFEST_REL).write_bytes(raw)
        git(self.path, "add", "-A")
        git(self.path, "commit", "-qm", "genesis manifest")
        return sha256(raw)

    def run(self, *, verifier=None, manifest=None, candidate=None, evidence=None, extra=None):
        """Run the ceremony the way a human does: an external copy, an explicit repo and commit."""
        head = git(self.path, "rev-parse", "HEAD").strip()
        commit = candidate or head
        raw = subprocess.run(
            ["git", "cat-file", "blob", f"{head}:{MANIFEST_REL}"],
            cwd=self.path, capture_output=True, timeout=60,
        ).stdout
        evidence_file = self.path.parent / "evidence.json"
        evidence_file.write_text(
            json.dumps(evidence if evidence is not None else self.evidence(commit)),
            encoding="utf-8",
        )
        argv = [
            sys.executable, str(self.external_verifier),
            "--repo", str(self.path),
            "--commit", commit,
            "--expect-verifier", verifier or self.verifier_sha(),
            "--expect-manifest", manifest or sha256(raw),
            "--evidence", str(evidence_file),
            "--evidence-run", "https://example.invalid/run/1",
            "--approver", "repository owner",
            "--reason", "genesis: this PR replaces the machinery governing future PRs",
        ]
        return subprocess.run(
            argv + list(extra or []),
            cwd=self.path.parent, capture_output=True, text=True, timeout=180,
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
        target = self.repo.external_verifier
        target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
        proc = self.repo.run(verifier="4" * 64)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the one that was reviewed", proc.stderr)

    def test_working_tree_tampering_is_invisible(self):
        """Every read comes from the object store, so a dirty checkout cannot smuggle content."""
        self.repo.write_manifest(self.repo.manifest())
        for rel in ("harness/bootstrap_verify.py", "factory_kernel/spine.py", MANIFEST_REL):
            (self.repo.path / rel).write_text("TAMPERED = True\n", encoding="utf-8")
        proc = self.repo.run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("BOOTSTRAP_OK", proc.stdout)

    def test_the_ceremony_runs_the_reviewed_copy_not_the_candidates(self):
        """The human's copy is the authority; the candidate's copy is merely inventoried."""
        self.repo.write_manifest(self.repo.manifest())
        self.assertFalse(
            self.repo.external_verifier.is_relative_to(self.repo.path),
            "the reviewed verifier must live outside the repository under test",
        )
        proc = self.repo.run()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_manifest_that_was_not_authorized_is_refused(self):
        self.repo.write_manifest(self.repo.manifest())
        proc = self.repo.run(manifest="0" * 64)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the one that was reviewed", proc.stderr)

    def test_parent_commit_evidence_cannot_authorize_the_manifest_bearing_child(self):
        """Validation of the parent is not exact-head evidence for the commit that adds genesis."""
        parent = git(self.repo.path, "rev-parse", "HEAD").strip()
        self.repo.write_manifest(self.repo.manifest())
        child = git(self.repo.path, "rev-parse", "HEAD").strip()
        self.assertNotEqual(parent, child)
        proc = self.repo.run(evidence=self.repo.evidence(parent))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("different commit than the one being authorized", proc.stderr)

    def test_evidence_below_the_declared_requirement_is_refused(self):
        self.repo.write_manifest(self.repo.manifest())
        head = git(self.repo.path, "rev-parse", "HEAD").strip()
        proc = self.repo.run(evidence=self.repo.evidence(head, unit_tests=10))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("below the required", proc.stderr)

    def test_evidence_missing_a_required_marker_is_refused(self):
        self.repo.write_manifest(self.repo.manifest())
        head = git(self.repo.path, "rev-parse", "HEAD").strip()
        proc = self.repo.run(evidence=self.repo.evidence(head, markers=["UNIT_PASSED"]))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing required markers", proc.stderr)

    def test_manifest_cannot_assert_its_own_authorization(self):
        """The human act must create the authorization, not confirm a string the candidate wrote."""
        self.repo.write_manifest(
            self.repo.manifest(authorization={"one_time": True, "approved_by": "me"})
        )
        proc = self.repo.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("asserts its own authorization", proc.stderr)

    def test_authorization_artifact_binds_the_human_act(self):
        self.repo.write_manifest(self.repo.manifest())
        proc = self.repo.run(extra=["--output", str(self.repo.path.parent / "auth.json")])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        auth = json.loads((self.repo.path.parent / "auth.json").read_text(encoding="utf-8"))
        head = git(self.repo.path, "rev-parse", "HEAD").strip()
        self.assertEqual(auth["candidate_sha"], head)
        self.assertEqual(auth["scope"], "one-time-genesis")
        for field in (
            "candidate_tree", "verifier_sha256", "manifest_sha256", "approver", "reason",
            "evidence_run", "evidence_sha256", "authorized_at", "base_sha",
        ):
            self.assertTrue(str(auth.get(field) or "").strip(), field)

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
        self.repo.write_manifest(self.repo.manifest())
        head = git(self.repo.path, "rev-parse", "HEAD").strip()
        for family, block, expected in (
            ("factory_mutations", {"total": 91, "caught": 90, "not_injected": 0}, "escape"),
            ("factory_mutations", {"total": 91, "caught": 91, "not_injected": 2}, "inject"),
            ("application_mutations", {"total": 0, "caught": 0, "not_injected": 0}, "ran nothing"),
        ):
            with self.subTest(family=family, block=block):
                proc = self.repo.run(evidence=self.repo.evidence(head, **{family: block}))
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(expected, proc.stderr)

    def test_every_ceremony_value_is_required(self):
        self.repo.write_manifest(self.repo.manifest())
        head = git(self.repo.path, "rev-parse", "HEAD").strip()
        proc = subprocess.run(
            [
                sys.executable, str(self.repo.external_verifier),
                "--repo", str(self.repo.path), "--commit", head,
            ],
            cwd=self.repo.path.parent, capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("supplied by a human at ceremony time", proc.stderr)


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
