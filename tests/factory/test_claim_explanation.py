"""Observation never upgrades records to proof, even for authentic closure-shaped fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.claim_explanation import explain_run
from tests.factory import test_factory_evidence_closure as closure

ROOT = Path(__file__).parents[2]


class ClaimExplanationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture = closure.EvidenceClosureTests()
        pack, legacy = fixture.fixture(self.root)
        with patch("factory_kernel.evidence_closure._load_immunity", return_value=closure.IMMUNITY_RESULT):
            manifest, index = fixture.close(self.root, pack, legacy, fixture.certificates())
        self.bundle = {**legacy, "spine": index, "run_manifest_sha256": manifest.sha256()}
        self.write("evidence-bundle.json", self.bundle)
        self.current = {c.claim_id: c.artifact.sha256 for c in manifest.claims}

    def write(self, name, value):
        (self.root / name).write_bytes(canonical_bytes(value))

    def report(self, **kwargs):
        args = {"artifact_root": self.root, "policy_path": ROOT / ".factory/evidence-spine.json",
                "expected_head_sha": closure.HEAD, "expected_base_sha": closure.BASE,
                "current_claim_hashes": self.current}
        return explain_run(**{**args, **kwargs})

    def rows(self, **kwargs):
        return {row["claim_id"]: row for row in self.report(**kwargs)["claims"]}

    def rewrite_claim(self, claim_id, mutate):
        """Coherently rewrite all hashes so envelope checks, not accidental hash damage, detect forgery."""
        path = "spine/run-manifest.json"
        manifest = json.loads((self.root / path).read_text())
        claim = next(c for c in manifest["claims"] if c["claim_id"] == claim_id)
        mutate(claim)
        self.write(path, manifest)
        digest = sha256_value(manifest)
        self.bundle["run_manifest_sha256"] = digest
        self.bundle["spine"]["manifest_sha256"] = digest
        index = next(c for c in self.bundle["spine"]["claims"] if c["claim_id"] == claim_id)
        for kind in ("deterministic", "independent"):
            index[kind + "_sha256"] = claim[kind]["artifact"]["sha256"] if claim[kind] else None
        self.write("evidence-bundle.json", self.bundle)

    def test_intact_closure_is_observation_not_current_proof(self):
        report = self.report()
        self.assertEqual(report["authority"], "observation-only")
        self.assertIs(report["proof_reuse_allowed"], False)
        self.assertEqual(len(report["claims"]), 21)
        for row in report["claims"]:
            self.assertEqual(row["recorded_dependency_status"], "current", row)
            self.assertEqual(row["evidence_status"], "insufficient")
            self.assertEqual(row["proof_status"], "not-established")
            self.assertIn("toolchain-identity-unavailable", row["gaps"])

    def test_missing_current_identities_remain_unknown(self):
        self.assertTrue(all(row["recorded_dependency_status"] == "unknown"
                            for row in self.rows(current_claim_hashes=None).values()))

    def test_wrong_revision_and_base_stale_every_claim(self):
        original = self.rows()
        for field in ("expected_head_sha", "expected_base_sha"):
            with self.subTest(field=field):
                changed = self.rows(**{field: "f" * 40})
                self.assertTrue(all(row["evidence_status"] == "stale" for row in changed.values()))
                self.assertEqual([r["record_id"] for r in original.values()],
                                 [r["record_id"] for r in changed.values()])

    def test_changed_dependency_propagates_only_through_declared_edges(self):
        changed = {**self.current, "red-proof": "1" * 64}
        rows = self.rows(current_claim_hashes=changed)
        self.assertEqual(rows["contract"]["recorded_dependency_status"], "current")
        self.assertEqual(rows["architecture-policy"]["recorded_dependency_status"], "current")
        for key in ("red-proof", "green-proof", "immunity"):
            self.assertEqual(rows[key]["evidence_status"], "stale")

    def test_missing_evidence_does_not_inherit_bundle_success(self):
        (self.root / "spine/builder/contract.json").unlink()
        rows = self.rows()
        self.assertIn("artifact-absent", rows["contract"]["gaps"])
        self.assertEqual(rows["design"]["recorded_dependency_status"], "unknown")

    def test_missing_manifest_preserves_index_subject_and_gap(self):
        (self.root / "spine/run-manifest.json").unlink()
        row = self.rows()["green-proof"]
        self.assertEqual(row["subject_sha256"], self.current["green-proof"])
        self.assertIsNone(row["recorded_claim"])
        self.assertIn("manifest-absent", row["gaps"])
        self.assertEqual(row["evidence_status"], "insufficient")

    def test_empty_directory_is_absent(self):
        with tempfile.TemporaryDirectory() as empty:
            rows = self.rows(artifact_root=Path(empty))
            self.assertTrue(all(row["evidence_status"] == "absent" for row in rows.values()))

    def test_changed_policy_is_stale(self):
        policy = json.loads((ROOT / ".factory/evidence-spine.json").read_text())
        policy["required_claims"][0]["stage"] = "changed"
        self.write("policy.json", policy)
        self.assertTrue(all("policy.spine" in row["changed_dependencies"]
                            for row in self.rows(policy_path=self.root / "policy.json").values()))

    def test_forged_deterministic_authority_is_not_accepted_after_rehash(self):
        def forge(claim):
            cert = claim["deterministic"]
            raw = json.loads((self.root / cert["artifact"]["path"]).read_text())
            raw["authority_id"] = cert["authority_id"] = "invented-authority"
            self.write(cert["artifact"]["path"], raw)
            cert["artifact"]["sha256"] = sha256_value(raw)
        self.rewrite_claim("contract", forge)
        self.assertIn("authority-envelope-mismatch", self.rows()["contract"]["gaps"])

    def test_builder_judgement_cannot_self_certify_after_rehash(self):
        for judgement in (self.current["design"], "not-a-digest"):
            def forge(claim):
                cert = claim["independent"]["artifact"]
                raw = json.loads((self.root / cert["path"]).read_text())
                raw["evidence"]["judgement_sha256"] = judgement
                self.write(cert["path"], raw)
                cert["sha256"] = sha256_value(raw)
            self.rewrite_claim("contract", forge)
            self.assertIn("independent-origin-unestablished", self.rows()["contract"]["gaps"])

    def test_independent_certificate_for_another_subject_is_insufficient_after_rehash(self):
        def forge(claim):
            cert = claim["independent"]["artifact"]
            raw = json.loads((self.root / cert["path"]).read_text())
            raw["evidence"]["subject_sha256"] = "d" * 64
            self.write(cert["path"], raw)
            cert["sha256"] = sha256_value(raw)
        self.rewrite_claim("contract", forge)
        self.assertIn("independent-binding-mismatch", self.rows()["contract"]["gaps"])

    def test_oversized_artifacts_and_invalid_current_hashes_fail_closed(self):
        (self.root / "spine/builder/contract.json").write_bytes(b" " * 250001)
        self.assertIn("artifact-invalid-artifact", self.rows()["contract"]["gaps"])
        with self.assertRaises(ValueError):
            self.report(current_claim_hashes={"contract": "unknown"})

    def test_duplicate_claims_and_duplicate_json_keys_are_gaps(self):
        self.bundle["spine"]["claims"].append(deepcopy(self.bundle["spine"]["claims"][0]))
        self.write("evidence-bundle.json", self.bundle)
        self.assertIn("invalid-or-duplicate-index-claims", self.rows()["contract"]["gaps"])
        (self.root / "evidence-bundle.json").write_text('{"spine": {}, "spine": {}}')
        self.assertIn("bundle-invalid-artifact", self.rows()["contract"]["gaps"])

    def test_replayed_manifest_claim_is_refused(self):
        path = "spine/run-manifest.json"
        manifest = json.loads((self.root / path).read_text())
        manifest["claims"].append(deepcopy(manifest["claims"][0]))
        self.write(path, manifest)
        self.assertIn("manifest-invalid-manifest", self.rows()["contract"]["gaps"])

    def test_tampered_bytes_and_index_are_not_accepted(self):
        self.write("spine/builder/contract.json", {"forged": True})
        self.assertIn("artifact-hash-mismatch", self.rows()["contract"]["gaps"])
        self.bundle["spine"]["claims"][0]["artifact_sha256"] = "e" * 64
        self.write("evidence-bundle.json", self.bundle)
        self.assertIn("index-claim-mismatch", self.rows()["contract"]["gaps"])

    def test_windows_paths_are_not_read_and_output_has_no_raw_evidence(self):
        self.rewrite_claim("contract", lambda claim: claim["artifact"].update(path="C:/private.json"))
        self.assertIn("artifact-unsafe-path", self.rows()["contract"]["gaps"])
        self.assertNotIn("Example contract summary", json.dumps(self.report()))

    def test_read_only_repeat_is_deterministic(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.report(), self.report())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
