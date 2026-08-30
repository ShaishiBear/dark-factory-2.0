import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.provenance import BUILDER_ARTIFACTS, NOTE_REF, build_pack, materialize, verify_pack


class BuilderProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.artifacts = self.root / "artifacts"
        self.repo.mkdir()
        self.artifacts.mkdir()
        (self.repo / ".factory").mkdir()
        self.issue = 7
        self.base = "a" * 40
        self.head = "b" * 40

        contract = {
            "version": "2.0",
            "issue": {"number": self.issue, "title": "Example"},
            "summary": "Example contract summary",
            "behaviors": [{"id": "AC-1", "given": "x", "when": "y", "then": "z", "seam": "svc"}],
            "invariants": [], "out_of_scope": [], "risks": [], "ambiguities": [],
        }
        contract_hash = sha256_value(contract)
        ticket = {
            "version": "1.0", "issue": self.issue, "contract_sha256": contract_hash,
            "title": "Example", "parent": None, "acceptance": ["AC-1"],
            "test_seams": {"AC-1": "svc"}, "body_sha256": "c" * 64,
        }
        frontier = {
            "version": "1.0", "issue": self.issue, "accepted": True, "blockers": [],
            "ready": True, "ticket_sha256": sha256_value(ticket),
        }
        context = {
            "version": "1.0", "contract_sha256": contract_hash, "files": ["app/x.py"],
            "symbols": [], "callers": [], "tests": [], "invariants": [], "adrs": [],
            "history": [], "file_sha256": {"app/x.py": "d" * 64},
        }
        design = {
            "version": "1.0", "contract_sha256": contract_hash,
            "context_sha256": sha256_value(context), "modules": ["x"], "seams": ["svc"],
            "public_interfaces": [], "invariants": ["i"], "data_flows": ["f"],
            "ac_mapping": {"AC-1": ["svc"]}, "planned_files": ["app/x.py"],
            "allowed_new_files": [],
        }
        policy = {"version": "1.0", "principles": [], "migrations": [], "debts": []}
        generic = {"version": "1.0", "ok": True}
        values = {
            "contract": contract,
            "tickets": ticket,
            "frontier": frontier,
            "context": context,
            "architecture-policy": policy,
            "design": design,
            "architecture-governor": dict(generic, decision="proceed"),
            "test-plan": generic,
            "red-proof": generic,
            "green-proof": generic,
            "impact": generic,
            "architecture-drift": generic,
            "architecture-conformance": generic,
        }
        for claim_id, rel in BUILDER_ARTIFACTS:
            target = self.repo / rel if claim_id == "architecture-policy" else self.artifacts / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(canonical_bytes(values[claim_id]))

    def tearDown(self):
        self.tmp.cleanup()

    def pack(self):
        return build_pack(
            artifact_root=self.artifacts,
            repo_root=self.repo,
            issue=self.issue,
            base_sha=self.base,
            head_sha=self.head,
        )

    def test_pack_is_complete_and_exact_head_bound(self):
        pack = self.pack()
        self.assertEqual(pack["note_ref"], NOTE_REF)
        self.assertEqual(set(pack["artifacts"]), {claim for claim, _ in BUILDER_ARTIFACTS})
        self.assertEqual(verify_pack(pack, expected_head_sha=self.head), pack)
        with self.assertRaisesRegex(ValueError, "different PR head"):
            verify_pack(pack, expected_head_sha="e" * 40)

    def test_tampered_embedded_artifact_is_rejected(self):
        pack = json.loads(json.dumps(self.pack()))
        pack["artifacts"]["context"]["content"]["history"].append("tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_pack(pack)

    def test_materialize_recreates_canonical_artifact_bytes(self):
        pack = self.pack()
        output = self.root / "materialized"
        paths = materialize(pack, output)
        self.assertEqual(set(paths), {claim for claim, _ in BUILDER_ARTIFACTS})
        for claim_id, path in paths.items():
            self.assertEqual(path.read_bytes(), canonical_bytes(pack["artifacts"][claim_id]["content"]))

    def test_frontier_must_have_authorized_work(self):
        path = self.artifacts / "frontier.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["ready"] = False
        path.write_bytes(canonical_bytes(value))
        with self.assertRaisesRegex(ValueError, "frontier did not authorize"):
            self.pack()


if __name__ == "__main__":
    unittest.main()
