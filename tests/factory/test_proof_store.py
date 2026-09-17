"""Proof object store: immutable content-addressed objects, role partitions, indexes only after
their objects, and an inventory check that rebuilds from canonical sources."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import canonical_bytes, sha256_bytes
from factory_kernel.proof_store import (
    ProofStoreRefused,
    get_by_digest,
    index_run,
    put_verified_object,
    resolve_attestation,
    role_of,
    verify_inventory,
)


def attestation(object_bytes: bytes, *, claim="contract", extra="") -> dict:
    return {"schema": "dark-factory/attestation", "schema_version": "1.0",
            "attestation_id": sha256_bytes(b"att:" + claim.encode() + extra.encode()), "claim_key": claim,
            "obligation_profile_id": "full-closure-v1", "subject": {"candidate_tree": "1" * 40},
            "inputs": [{"kind": "exact-tree", "identity": "candidate_tree", "digest": "1" * 40, "coverage": "complete"}],
            "evidence": [{"retained_object_id": f"spine/{claim}.json", "sha256": sha256_bytes(object_bytes), "media_type": "application/json"}]}


class ObjectTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_objects_are_content_addressed_immutable_and_rehashed_on_read(self):
        digest = put_verified_object(self.root, b"one")
        self.assertEqual(digest, sha256_bytes(b"one"))
        self.assertEqual(put_verified_object(self.root, b"one"), digest, "idempotent for identical bytes")
        self.assertEqual(get_by_digest(self.root, digest), b"one")
        self.assertIsNone(get_by_digest(self.root, "0" * 64))
        (self.root / "objects" / "public" / digest).write_bytes(b"two")
        with self.assertRaises(ProofStoreRefused):
            put_verified_object(self.root, b"one")
        with self.assertRaises(ProofStoreRefused):
            get_by_digest(self.root, digest)

    def test_roles_partition_and_never_share_an_object(self):
        digest = put_verified_object(self.root, b"judge material", role="judge")
        self.assertEqual(role_of(self.root, digest), "judge")
        self.assertIsNone(get_by_digest(self.root, digest, role="public"))
        self.assertEqual(get_by_digest(self.root, digest, role="judge"), b"judge material")
        with self.assertRaises(ProofStoreRefused):
            put_verified_object(self.root, b"judge material", role="public")
        with self.assertRaises(ProofStoreRefused):
            put_verified_object(self.root, b"x", role="owner")
        with self.assertRaises(ProofStoreRefused):
            put_verified_object(self.root, b"")


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.contract = b'{"claim":"contract"}'
        self.holdout = b'{"claim":"holdout"}'

    def test_an_index_needs_every_object_first_and_is_then_immutable(self):
        record = attestation(self.contract)
        with self.assertRaises(ProofStoreRefused):
            index_run(self.root, "pr-42-evidence-abc", [record])
        self.assertFalse((self.root / "index").exists(), "no partial index")
        self.assertFalse((self.root / "objects").exists(), "no attestation object retained before the index could be")
        put_verified_object(self.root, self.contract)
        index = index_run(self.root, "pr-42-evidence-abc", [record])
        self.assertEqual(index["entries"][0]["evidence"], [{"retained_object_id": "spine/contract.json", "sha256": sha256_bytes(self.contract), "role": "public"}])
        self.assertEqual(index_run(self.root, "pr-42-evidence-abc", [record]), index, "identical rewrite is idempotent")
        with self.assertRaises(ProofStoreRefused):
            index_run(self.root, "pr-42-evidence-abc", [attestation(self.contract, extra="v2")])
        with self.assertRaises(ProofStoreRefused):
            index_run(self.root, "../escape", [record])
        with self.assertRaises(ProofStoreRefused):
            index_run(self.root, "pr-43", [record, record])

    def test_resolution_returns_rehashed_records_and_presence_is_not_proof(self):
        put_verified_object(self.root, self.contract)
        put_verified_object(self.root, self.holdout, role="judge")
        records = [attestation(self.contract), attestation(self.holdout, claim="holdout-code")]
        index_run(self.root, "pr-42", records)
        self.assertEqual(resolve_attestation(self.root, records[1]["attestation_id"]), records[1])
        self.assertIsNone(resolve_attestation(self.root, "0" * 64))
        entries = {entry["claim_key"]: entry for entry in index_run(self.root, "pr-42", records)["entries"]}
        self.assertEqual(entries["holdout-code"]["evidence"][0]["role"], "judge")
        self.assertEqual(entries["contract"]["evidence"][0]["role"], "public")

    def test_inventory_rebuilds_from_canonical_objects_and_reports_every_defect(self):
        put_verified_object(self.root, self.contract)
        records = [attestation(self.contract)]
        index_run(self.root, "pr-42", records)
        self.assertEqual(verify_inventory(self.root), {"objects": 2, "indexes": 1, "consistent": True, "problems": []})
        # A rewritten index that names the same objects but a different entry is a defect.
        index_path = self.root / "index" / "pr-42.json"
        recorded = json.loads(index_path.read_text(encoding="utf-8"))
        recorded["entries"][0]["claim_key"] = "green-proof"
        index_path.write_bytes(canonical_bytes(recorded))
        report = verify_inventory(self.root)
        self.assertFalse(report["consistent"])
        self.assertTrue(any("rebuilt index differs" in problem for problem in report["problems"]))
        # A corrupted object is named; a partial write left behind is named.
        (self.root / "objects" / "public" / sha256_bytes(self.contract)).write_bytes(b"corrupt")
        (self.root / "objects" / "public" / "deadbeef.partial").write_bytes(b"x")
        problems = verify_inventory(self.root)["problems"]
        self.assertTrue(any("object corrupted" in problem for problem in problems))
        self.assertTrue(any("partial object" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
