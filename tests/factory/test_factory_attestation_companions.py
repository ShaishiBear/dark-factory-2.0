"""Companion attestations at the trusted wrapper boundary (WP05 / R05): built from the closure's
own manifest and from identities the wrapper observed, retained before indexed, verified against
the artifacts on disk, assessed in shadow, and never a substitute for the legacy closure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes, sha256_bytes
from factory_kernel.evidence_closure import (
    ATTESTATION_INDEX_PATH,
    ATTESTATION_STORE_DIR,
    CLOSURE_PROGRAMS,
    JUDGE_CLAIMS,
    LOCK_FILES,
    compile_attestations,
    observe_closure_environment,
    observe_issuer,
)
from factory_kernel.proof_dependencies import load_profiles
from factory_kernel.proof_store import role_of, verify_inventory
from factory_kernel.spine import load_policy
from tests.factory.test_factory_evidence_closure import BASE, HEAD, IMMUNITY_RESULT, EvidenceClosureTests

ROOT = Path(__file__).parents[2]
KERNEL_HEAD = "c" * 40
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, env=GIT_ENV).stdout.strip()


def make_candidate(root: Path, *, with_bun_lock: bool = True) -> tuple[Path, str]:
    repo = root / "candidate"
    (repo / "app" / "backend").mkdir(parents=True)
    (repo / "app" / "frontend").mkdir(parents=True)
    (repo / "app" / "backend" / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    if with_bun_lock:
        (repo / "app" / "frontend" / "bun.lock").write_text("{}\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "candidate")
    return repo, git(repo, "rev-parse", "HEAD")


def synthetic_environment(*, candidate_tree: str = "1" * 40) -> dict:
    policy = load_policy(ROOT / ".factory" / "evidence-spine.json")
    profiles = load_profiles(ROOT / ".factory" / "authority-profiles.json", spine_claim_ids=[r.claim_id for r in policy.requirements])
    return {"policy_sha256": policy.sha256(), "authority_profiles_sha256": profiles.sha256, "profiles": profiles,
            "program_closure_sha256": "d" * 64, "independence_profile_sha256": "e" * 64, "configuration_digest": "f" * 64,
            "toolchain_digest": "1" * 64, "runtime_image_digest": "2" * 64, "candidate_tree": candidate_tree,
            "dependency_lock_digests": {"app/backend/uv.lock": "3" * 64, "app/frontend/bun.lock": "4" * 64}}


HOSTED = {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/dark-factory-trust-root.yml@refs/heads/main",
          "GITHUB_RUN_ID": "35281425505", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "trust-root-authority"}


class CompanionTests(EvidenceClosureTests):
    """Reuses the closure fixture: a complete 21-claim spine, then companions over it."""

    def closed(self, root: Path):
        pack, legacy = self.fixture(root)
        manifest, index = self.close(root, pack, legacy, self.certificates())
        return manifest, index

    def companions(self, root: Path, *, issuer=None, environment=None):
        manifest, index = self.closed(root)
        policy = load_policy(ROOT / ".factory" / "evidence-spine.json")
        return manifest, compile_attestations(
            manifest=manifest, index=index, policy=policy, artifact_root=root, repository_id=1341036238, head_sha=HEAD,
            environment=environment or synthetic_environment(),
            issuer=issuer or observe_issuer(HOSTED, source_revision=KERNEL_HEAD), created_at="2026-09-17T23:30:00+00:00")

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_one_verified_companion_per_spine_claim_all_current_in_shadow_and_none_authorising(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, result = self.companions(root)
            index, summary = result["index"], result["summary"]
            self.assertEqual((summary["count"], summary["verified"], summary["shadow_current"], summary["proof_reuse_allowed"]), (21, 21, 21, False))
            self.assertEqual([a["claim_key"] for a in index["attestations"]],
                             [r.claim_id for r in load_policy(ROOT / ".factory" / "evidence-spine.json").requirements])
            self.assertEqual({a["subject"]["candidate_commit"] for a in index["attestations"]}, {HEAD})
            self.assertEqual({a["subject"]["base_commit"] for a in index["attestations"]}, {BASE})
            self.assertTrue(all(a["outcome"]["verdict"] == "pass" for a in index["attestations"]))
            self.assertTrue(all(row["verified"] for row in index["verification"].values()))
            self.assertTrue(all(row["satisfies_obligation"] for row in index["shadow_currency"].values()))
            self.assertEqual((index["authority"], index["proof_reuse_allowed"]), ("shadow", False))
            written = root / ATTESTATION_INDEX_PATH
            self.assertEqual(sha256_bytes(written.read_bytes()), summary["index_sha256"])
            self.assertEqual(json.loads(written.read_text(encoding="utf-8")), index)
            # The legacy closure outputs are byte-identical to a run without companions.
            self.assertTrue((root / "spine" / "evidence-index.json").is_file())
            self.assertEqual(manifest.sha256(), manifest.__class__.load(root / "spine" / "run-manifest.json").sha256())
            # Evidence objects are retained by role; judge material never shares the public partition.
            store = root / ATTESTATION_STORE_DIR
            self.assertTrue(verify_inventory(store)["consistent"])
            for attestation in index["attestations"]:
                expected = "judge" if attestation["claim_key"] in JUDGE_CLAIMS else "public"
                for row in attestation["evidence"]:
                    self.assertEqual(role_of(store, row["sha256"]), expected, (attestation["claim_key"], row["retained_object_id"]))
            # The dependency index carries the spine's predecessor edges as proof dependence.
            edges = index["dependency_index"]["edges"]
            self.assertTrue(edges and all(edge["kind"] == "depends_on" for edge in edges))
            ids = {a["claim_key"]: a["attestation_id"] for a in index["attestations"]}
            self.assertIn({"source": ids["tickets"], "target": ids["contract"], "kind": "depends_on"}, edges)

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_a_local_issuer_yields_recorded_but_rejected_companions(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, result = self.companions(root, issuer=observe_issuer({}, source_revision=KERNEL_HEAD))
            self.assertEqual((result["summary"]["count"], result["summary"]["verified"], result["summary"]["shadow_current"]), (21, 0, 0))
            statuses = {row["status"] for row in result["index"]["shadow_currency"].values()}
            self.assertEqual(statuses, {"rejected"})
            self.assertTrue(all(row["reason_codes"] == ["issuer_unauthenticated"] for row in result["index"]["verification"].values()))

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_a_tampered_artifact_on_disk_is_caught_by_verification_not_hidden_by_the_manifest(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, index = self.closed(root)
            target = root / "spine" / "builder" / "impact.json"
            target.write_bytes(canonical_bytes({"version": "1.0", "head_sha": HEAD, "verdict": "pass", "note": "edited"}))
            with self.assertRaises(ValueError) as ctx:
                compile_attestations(manifest=manifest, index=index, policy=load_policy(ROOT / ".factory" / "evidence-spine.json"),
                                     artifact_root=root, repository_id=1341036238, head_sha=HEAD, environment=synthetic_environment(),
                                     issuer=observe_issuer(HOSTED, source_revision=KERNEL_HEAD), created_at="2026-09-17T23:30:00+00:00")
            self.assertIn("impact.json", str(ctx.exception))

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_an_incomplete_claim_is_recorded_as_incomplete_and_never_satisfies(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, index = self.closed(root)
            rows = [dict(row) for row in index["claims"]]
            for row in rows:
                if row["claim_id"] == "mutation":
                    row["completion_level"] = 80
                    row["reasons"] = ["independent certification missing"]
            result = compile_attestations(manifest=manifest, index={**index, "claims": rows}, policy=load_policy(ROOT / ".factory" / "evidence-spine.json"),
                                          artifact_root=root, repository_id=1341036238, head_sha=HEAD, environment=synthetic_environment(),
                                          issuer=observe_issuer(HOSTED, source_revision=KERNEL_HEAD), created_at="2026-09-17T23:30:00+00:00")
            mutation = next(a for a in result["index"]["attestations"] if a["claim_key"] == "mutation")
            self.assertEqual(mutation["outcome"], {"verdict": "incomplete", "reason_codes": ["independent certification missing"]})
            shadow = result["index"]["shadow_currency"][mutation["attestation_id"]]
            self.assertEqual((shadow["status"], shadow["satisfies_obligation"]), ("current", False))


class ObservationTests(unittest.TestCase):
    def test_the_environment_is_observed_from_the_kernel_checkout_and_the_exact_candidate_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = make_candidate(Path(tmp))
            environment = observe_closure_environment(kernel_root=ROOT, candidate_root=repo, head_sha=head, environ={"RUNNER_OS": "Linux"})
            self.assertEqual(environment["candidate_tree"], git(repo, "rev-parse", "HEAD^{tree}"))
            self.assertEqual(set(environment["dependency_lock_digests"]), set(LOCK_FILES))
            self.assertEqual(environment["dependency_lock_digests"]["app/backend/uv.lock"], sha256_bytes(b"version = 1\n"))
            self.assertEqual(environment["policy_sha256"], load_policy(ROOT / ".factory" / "evidence-spine.json").sha256())
            again = observe_closure_environment(kernel_root=ROOT, candidate_root=repo, head_sha=head, environ={"RUNNER_OS": "Linux"})
            self.assertEqual(environment["program_closure_sha256"], again["program_closure_sha256"])
            other = observe_closure_environment(kernel_root=ROOT, candidate_root=repo, head_sha=head, environ={"RUNNER_OS": "Windows"})
            self.assertNotEqual(environment["runtime_image_digest"], other["runtime_image_digest"])
            for rel in CLOSURE_PROGRAMS:
                self.assertTrue((ROOT / rel).is_file(), rel)
            with self.assertRaises(ValueError):
                observe_closure_environment(kernel_root=ROOT, candidate_root=repo, head_sha="0" * 40, environ={})

    def test_a_lock_file_absent_from_the_candidate_tree_is_reported_absent_and_makes_shadow_currency_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = make_candidate(Path(tmp), with_bun_lock=False)
            environment = observe_closure_environment(kernel_root=ROOT, candidate_root=repo, head_sha=head, environ={})
            self.assertEqual(set(environment["dependency_lock_digests"]), {"app/backend/uv.lock"})

    def test_the_issuer_is_github_only_when_github_ran_the_program(self):
        hosted = observe_issuer(HOSTED, source_revision=KERNEL_HEAD)
        self.assertEqual((hosted["platform"], hosted["run_id"], hosted["attempt"], hosted["job_id"]), ("github-actions", 35281425505, 1, "trust-root-authority"))
        local = observe_issuer({"GITHUB_RUN_ID": "1"}, source_revision=KERNEL_HEAD)
        self.assertEqual((local["platform"], local["run_id"]), ("local", 0))


class WrapperTests(unittest.TestCase):
    def test_the_wrapper_emits_companions_after_closure_and_fails_loudly_when_it_cannot(self):
        source = (ROOT / "scripts" / "factory_evidence_spine.py").read_text(encoding="utf-8")
        closure = source.index("manifest, index = compile_full_spine(")
        emit = source.index('final["attestations"] = emit_companions(')
        write = source.index("output.write_bytes(canonical_bytes(final))")
        self.assertTrue(closure < emit < write, "companions are emitted after closure and before the bundle is written")
        self.assertIn('fail(f"proof companions could not be emitted: {exc}")', source)
        self.assertIn("EVIDENCE_ATTESTATIONS_OK", source)


if __name__ == "__main__":
    unittest.main()
