"""Companion attestations at the trusted wrapper boundary (WP05 / R05): built from the closure's
own manifest and from identities the wrapper observed, retained before indexed, verified against
the artifacts on disk, assessed in shadow, and never a substitute for the legacy closure."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes, sha256_bytes, sha256_value
from factory_kernel.evidence_closure import (
    ATTESTATION_INDEX_PATH,
    ATTESTATION_STORE_DIR,
    CLOSURE_PROGRAMS,
    JUDGE_CLAIMS,
    LOCK_FILES,
    compile_attestations,
    observe_closure_environment,
    observe_issuer,
    reverify_companions,
)
from factory_kernel.proof_dependencies import load_profiles
from factory_kernel.proof_store import get_by_digest, role_of, verify_inventory
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
    def test_verification_reads_retained_objects_so_an_unretained_or_altered_artifact_is_not_evidence(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, result = self.companions(root)
            issuer = observe_issuer(HOSTED, source_revision=KERNEL_HEAD)
            again = reverify_companions(root, issuer)
            self.assertEqual((len(again["verified"]), again["proof_reuse_allowed"]), (21, False))
            by_claim = {a["claim_key"]: a for a in result["index"]["attestations"]}
            store = root / ATTESTATION_STORE_DIR
            # The store no longer holds the impact artifact: the bytes on disk are unchanged, but
            # unretained evidence is missing evidence.
            impact = by_claim["impact"]
            digest = next(row["sha256"] for row in impact["evidence"] if row["retained_object_id"].endswith("impact.json"))
            (store / "objects" / "public" / digest).unlink()
            self.assertIsNone(get_by_digest(store, digest))
            after = reverify_companions(root, issuer)
            row = after["verification"][impact["attestation_id"]]
            self.assertEqual((row["verified"], row["status"], row["reason_codes"]), (False, "insufficient", ["artifact_missing"]))
            self.assertEqual(len(after["verified"]), 20)
            # A rewritten artifact on disk is bytes the store never vouched for: not retained, so
            # missing, never silently accepted because a file of that name exists.
            design = by_claim["design"]
            (root / "spine" / "builder" / "design.json").write_bytes(b'{"version":"1.0","edited":true}')
            edited = reverify_companions(root, issuer)["verification"][design["attestation_id"]]
            self.assertEqual((edited["status"], edited["reason_codes"]), ("insufficient", ["artifact_missing"]))
            # A re-signed record that names a different digest for a retained object is forged:
            # the object exists, its bytes do not match what the record attests.
            index_path = root / ATTESTATION_INDEX_PATH
            index = json.loads(index_path.read_text(encoding="utf-8"))
            for record in index["attestations"]:
                if record["claim_key"] == "context":
                    record["evidence"][0]["sha256"] = "5" * 64
                    body = {k: v for k, v in record.items() if k != "attestation_id"}
                    record["attestation_id"] = sha256_value(body)
                    forged_id = record["attestation_id"]
            index_path.write_bytes(canonical_bytes(index))
            forged = reverify_companions(root, issuer)["verification"][forged_id]
            self.assertEqual((forged["status"], forged["reason_codes"]), ("rejected", ["artifact_tampered"]))

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_shadow_currency_compares_the_exact_subject(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, result = self.companions(root)
            shadow = result["index"]["shadow_currency"]
            self.assertTrue(all(row["status"] == "current" for row in shadow.values()))
            self.assertTrue(all(a["subject"]["candidate_tree"] == "1" * 40 for a in result["index"]["attestations"]))

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
    """The wrapper's run() with every subprocess and the closure stubbed: what remains is the
    wrapper's own contract, that the bundle it writes carries the companions it emitted, and
    that a failure to emit them is a refusal of this authority."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("spine_wrapper_under_test", ROOT / "scripts" / "factory_evidence_spine.py")
        self.spine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.spine)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.legacy = {"version": "5.0", "pr": 42, "issue": 7, "base_sha": BASE, "head_sha": HEAD, "observed": {}}

    def fake_run(self, argv, **kwargs):
        program = str(argv[1]) if len(argv) > 1 else ""
        if program.endswith("factory_evidence.py"):
            (self.root / "evidence-bundle-core-v5.json").write_bytes(canonical_bytes(self.legacy))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if program.endswith("factory_mutations/run.py") or program.endswith("run.py"):
            return subprocess.CompletedProcess(argv, 0, "FACTORY_MUTATIONS_OK FACTORY_MUTATIONS_TOTAL=3 FACTORY_MUTATIONS_CAUGHT=3 "
                                               "FACTORY_MUTATIONS_NOT_INJECTED=0 IMMUNITY_OK entries=1 assertions=2 sha256=" + "e" * 64, "")
        if program.endswith("factory_provenance.py"):
            (self.root / "spine").mkdir(exist_ok=True)
            (self.root / "spine" / "builder-provenance.json").write_bytes(canonical_bytes({"version": "1.0"}))
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected subprocess: {argv}")

    def run_wrapper(self, emit):
        (self.root / "holdout.json").write_bytes(canonical_bytes({"version": "1.0", "verdict": "pass"}))
        (self.root / "architecture-holdout.json").write_bytes(canonical_bytes({"version": "1.0", "verdict": "pass"}))
        (self.root / "independent").mkdir(exist_ok=True)
        for claim in ("contract", "design", "architecture-governor"):
            (self.root / "independent" / f"{claim}.json").write_bytes(canonical_bytes({"claim_id": claim}))
        manifest = SimpleNamespace(sha256=lambda: "9" * 64, run_id="pr-42-evidence-" + HEAD[:12], base_sha=BASE)
        index = {"version": "1.0", "claims": [], "completion_level": 100, "builder_provenance_sha256": "8" * 64}
        args = SimpleNamespace(pr="42", verdict=str(self.root / "verdict.json"), architecture_verdict=str(self.root / "architecture-holdout.json"),
                               output=str(self.root / "evidence-bundle.json"))
        with patch.object(self.spine.subprocess, "run", self.fake_run), \
                patch.object(self.spine, "verify_pack", lambda *a, **k: None), \
                patch.object(self.spine, "compile_full_spine", lambda **k: (manifest, index)), \
                patch.object(self.spine, "emit_companions", emit), \
                patch.object(self.spine, "ROOT", self.root), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.spine.run(args)
        return json.loads((self.root / "evidence-bundle.json").read_text(encoding="utf-8")), out.getvalue()

    def test_the_bundle_carries_the_companion_summary_the_wrapper_emitted(self):
        calls = []

        def emit(**kwargs):
            calls.append(kwargs)
            return {"index_sha256": "7" * 64, "count": 21, "verified": 21, "shadow_current": 21, "proof_reuse_allowed": False}

        bundle, _ = self.run_wrapper(emit)
        self.assertEqual(bundle["attestations"], {"index_sha256": "7" * 64, "count": 21, "verified": 21, "shadow_current": 21, "proof_reuse_allowed": False})
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["head"], calls[0]["candidate_root"], calls[0]["kernel_root"]), (HEAD, self.root, ROOT))
        self.assertEqual(calls[0]["index"]["completion_level"], 100)
        self.assertEqual(bundle["run_manifest_sha256"], "9" * 64, "legacy fields are untouched")

    def test_a_companion_failure_is_a_refusal_of_this_authority(self):
        def emit(**kwargs):
            raise ValueError("candidate tree cannot be observed")

        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            self.run_wrapper(emit)
        self.assertIn("proof companions could not be emitted: candidate tree cannot be observed", err.getvalue())
        self.assertFalse((self.root / "evidence-bundle.json").exists(), "no bundle is written without its companions")

    def test_companions_are_emitted_after_closure_and_before_the_bundle_is_written(self):
        source = (ROOT / "scripts" / "factory_evidence_spine.py").read_text(encoding="utf-8")
        closure = source.index("manifest, index = compile_full_spine(")
        emit = source.index('final["attestations"] = emit_companions(')
        write = source.index("output.write_bytes(canonical_bytes(final))")
        self.assertTrue(closure < emit < write)
        self.assertIn("EVIDENCE_ATTESTATIONS_OK", source)


if __name__ == "__main__":
    unittest.main()
