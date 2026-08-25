import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import sha256_file
from factory_kernel.manifest import ArtifactRef, Certification, ClaimRecord, RunManifest
from factory_kernel.spine import assess_manifest, compile_evidence_index, load_policy


BASE = "1" * 40
HEAD = "2" * 40


class EvidenceSpineTests(unittest.TestCase):
    def write_json(self, root: Path, rel: str, value: dict) -> ArtifactRef:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return ArtifactRef(name=path.stem, path=rel, sha256=sha256_file(path))

    def write_policy(self, root: Path) -> Path:
        path = root / "policy.json"
        path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "required_claims": [
                        {
                            "id": "contract",
                            "stage": "spec",
                            "requires": [],
                            "deterministic_required": True,
                            "independent_required": True,
                            "exact_head_required": False,
                            "final_evidence_required": True,
                        },
                        {
                            "id": "green-proof",
                            "stage": "green",
                            "requires": ["contract"],
                            "deterministic_required": True,
                            "independent_required": False,
                            "exact_head_required": True,
                            "final_evidence_required": True,
                        },
                    ],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def complete_manifest(self, root: Path) -> RunManifest:
        contract = self.write_json(root, "artifacts/contract.json", {"contract": 1})
        contract_det = self.write_json(root, "artifacts/contract-det.json", {"pass": True})
        contract_ind = self.write_json(root, "artifacts/contract-ind.json", {"pass": True})
        green = self.write_json(root, "artifacts/green.json", {"green": True})
        green_det = self.write_json(root, "artifacts/green-det.json", {"pass": True})
        manifest = RunManifest.create(run_id="run-1", issue=42, base_sha=BASE)
        manifest.add(
            ClaimRecord(
                claim_id="contract",
                stage="spec",
                producer="specifier",
                artifact=contract,
                deterministic=Certification("deterministic", "contract-validator", contract_det),
                independent=Certification("independent", "contract-holdout", contract_ind),
            )
        )
        manifest.add(
            ClaimRecord(
                claim_id="green-proof",
                stage="green",
                producer="coder",
                artifact=green,
                deterministic=Certification("deterministic", "green-replay", green_det),
                exact_head_sha=HEAD,
                bindings={"contract_sha256": contract.sha256},
            )
        )
        return manifest

    def test_real_policy_is_canonical_and_final_evidence_bound(self):
        policy = load_policy(Path(__file__).parents[2] / ".factory/evidence-spine.json")
        ids = [requirement.claim_id for requirement in policy.requirements]
        self.assertEqual(ids[0], "contract")
        self.assertIn("architecture-drift", ids)
        self.assertIn("architecture-conformance", ids)
        self.assertIn("mutation", ids)
        self.assertIn("immunity", ids)
        drift = next(x for x in policy.requirements if x.claim_id == "architecture-drift")
        self.assertTrue(drift.deterministic_required)
        self.assertTrue(drift.independent_required)
        self.assertTrue(drift.exact_head_required)
        self.assertTrue(drift.final_evidence_required)
        self.assertTrue(all(requirement.final_evidence_required for requirement in policy.requirements))

    def test_policy_rejects_forward_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "version": "1.0",
                        "required_claims": [
                            {
                                "id": "green",
                                "stage": "green",
                                "requires": ["contract"],
                                "deterministic_required": True,
                                "independent_required": False,
                                "exact_head_required": True,
                                "final_evidence_required": True,
                            },
                            {
                                "id": "contract",
                                "stage": "spec",
                                "requires": [],
                                "deterministic_required": True,
                                "independent_required": False,
                                "exact_head_required": False,
                                "final_evidence_required": True,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown/forward"):
                load_policy(path)

    def test_missing_independent_authority_stops_at_60(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = load_policy(self.write_policy(root))
            manifest = self.complete_manifest(root)
            contract = manifest.claim("contract")
            self.assertIsNotNone(contract)
            manifest.claims[0] = ClaimRecord(
                claim_id=contract.claim_id,
                stage=contract.stage,
                producer=contract.producer,
                artifact=contract.artifact,
                deterministic=contract.deterministic,
            )
            assessment = assess_manifest(policy, manifest, expected_head_sha=HEAD)
            row = next(row for row in assessment["claims"] if row["claim_id"] == "contract")
            self.assertEqual(row["level"], 60)
            self.assertFalse(assessment["ready_for_evidence"])
            self.assertTrue(any("independent certification missing" in item for item in assessment["blockers"]))

    def test_required_provenance_binding_is_not_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = load_policy(self.write_policy(root))
            manifest = self.complete_manifest(root)
            green = manifest.claim("green-proof")
            self.assertIsNotNone(green)
            manifest.claims[1] = ClaimRecord(
                claim_id=green.claim_id,
                stage=green.stage,
                producer=green.producer,
                artifact=green.artifact,
                deterministic=green.deterministic,
                exact_head_sha=green.exact_head_sha,
            )
            assessment = assess_manifest(policy, manifest, expected_head_sha=HEAD)
            self.assertFalse(assessment["ready_for_evidence"])
            self.assertTrue(any("provenance bindings missing" in item for item in assessment["blockers"]))

    def test_exact_head_mismatch_blocks_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = load_policy(self.write_policy(root))
            manifest = self.complete_manifest(root)
            assessment = assess_manifest(policy, manifest, expected_head_sha="3" * 40)
            self.assertFalse(assessment["ready_for_evidence"])
            self.assertTrue(any("exact head" in item for item in assessment["blockers"]))

    def test_artifact_bytes_are_rechecked_before_100_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = load_policy(self.write_policy(root))
            manifest = self.complete_manifest(root)
            (root / "artifacts/green.json").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                compile_evidence_index(policy, manifest, artifact_root=root, head_sha=HEAD)

    def test_complete_spine_compiles_to_100_percent_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = load_policy(self.write_policy(root))
            manifest = self.complete_manifest(root)
            index = compile_evidence_index(policy, manifest, artifact_root=root, head_sha=HEAD)
            self.assertEqual(index["completion_level"], 100)
            self.assertEqual([row["completion_level"] for row in index["claims"]], [100, 100])
            self.assertEqual(index["head_sha"], HEAD)


if __name__ == "__main__":
    unittest.main()
