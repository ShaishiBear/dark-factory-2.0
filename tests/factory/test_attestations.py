"""Typed proof companions: an envelope built only from trusted observations, verified by
re-deriving every one of them (SPECIFICATION 3.4, WP05)."""
from __future__ import annotations

import copy
import json
import unittest

from factory_kernel.attestations import (
    AttestationRefused,
    Refusal,
    TrustedExecution,
    VerifiedAttestation,
    attestation_body,
    build_attestation,
    dependency_digest,
    verify_attestation,
)
from factory_kernel.canonical import sha256_bytes, sha256_value

TREE = "1" * 40
BASE = "a" * 40
HEAD = "b" * 40
KERNEL = "c" * 40
OBJECTS = {"spine/builder/contract.json": b'{"version":"2.0"}', "spine/certifications/contract-deterministic.json": b'{"kind":"deterministic"}'}
AUTHORITY = {"id": "evidence-spine-closure", "program_closure_sha256": "d" * 64, "policy_sha256": "e" * 64,
             "independence_profile_sha256": "f" * 64}
ISSUER = {"platform": "github-actions", "workflow_id": "ShaishiBear/dark-factory-2.0/.github/workflows/dark-factory-trust-root.yml@refs/heads/main",
          "run_id": 35281425505, "attempt": 1, "job_id": "trust-root-authority", "source_revision": KERNEL}


def execution(**overrides) -> TrustedExecution:
    fields = dict(
        claim_key="contract", obligation_profile_id="full-closure-v1",
        subject={"repository_id": 1341036238, "base_commit": BASE, "candidate_commit": HEAD, "candidate_tree": TREE, "programme_sha256": None},
        authority=dict(AUTHORITY),
        environment={"toolchain_digest": "1" * 64, "dependency_lock_digests": {"app/backend/uv.lock": "2" * 64},
                     "runtime_image_digest": "3" * 64, "configuration_digest": "4" * 64},
        inputs=[
            {"kind": "exact-tree", "identity": "candidate_tree", "digest": TREE, "coverage": "complete"},
            {"kind": "trusted-authority", "identity": "program_closure", "digest": "d" * 64, "coverage": "complete"},
            {"kind": "trusted-policy", "identity": "spine_policy", "digest": "e" * 64, "coverage": "complete"},
            {"kind": "environment", "identity": "toolchain", "digest": "1" * 64, "coverage": "complete"},
        ],
        evidence=[{"retained_object_id": path, "sha256": sha256_bytes(data), "media_type": "application/json"}
                  for path, data in OBJECTS.items()],
        outcome={"verdict": "pass", "reason_codes": []},
        issuer=dict(ISSUER), created_at="2026-09-17T23:30:00+00:00",
    )
    fields.update(overrides)
    return TrustedExecution(**fields)


def reader(object_id):
    return OBJECTS.get(object_id)


REGISTRY = {"evidence-spine-closure": dict(AUTHORITY)}


class BuildTests(unittest.TestCase):
    def test_the_identity_is_the_digest_of_the_canonical_body_and_nothing_else(self):
        record = build_attestation(execution())
        body = {key: value for key, value in record.items() if key != "attestation_id"}
        self.assertEqual(record["attestation_id"], sha256_value(body))
        self.assertEqual(record, build_attestation(execution()), "deterministic")
        shuffled = execution(inputs=list(reversed(execution().inputs)))
        self.assertEqual(build_attestation(shuffled)["attestation_id"], record["attestation_id"], "input order is not identity")
        self.assertEqual(json.loads(json.dumps(record)), record, "plain JSON")

    def test_builder_shaped_values_are_refused_at_construction(self):
        cases = {
            "unknown input kind": dict(inputs=[{"kind": "vibes", "identity": "x", "digest": "1" * 64, "coverage": "complete"}]),
            "extra subject field": dict(subject={**execution().subject, "note": "trust me"}),
            "non-hex tree": dict(subject={**execution().subject, "candidate_tree": "main"}),
            "boolean repository id": dict(subject={**execution().subject, "repository_id": True}),
            "unregistered platform": dict(issuer={**ISSUER, "platform": "laptop"}),
            "negative run": dict(issuer={**ISSUER, "run_id": -1}),
            "unregistered verdict": dict(outcome={"verdict": "green", "reason_codes": []}),
            "duplicate input identity": dict(inputs=execution().inputs + [execution().inputs[0]]),
            "escaping object id": dict(evidence=[{"retained_object_id": "../secret", "sha256": "1" * 64, "media_type": "x"}]),
            "unknown coverage": dict(inputs=[{**execution().inputs[0], "coverage": "probably"}]),
            "authority digest not sha256": dict(authority={**AUTHORITY, "policy_sha256": "P"}),
        }
        for name, override in cases.items():
            with self.subTest(name), self.assertRaises(AttestationRefused):
                attestation_body(execution(**override))


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.record = build_attestation(execution())

    def verify(self, record=None, issuer=None, objects=None, registry=None):
        return verify_attestation(record or self.record, issuer or ISSUER,
                                  (lambda oid: (objects if objects is not None else OBJECTS).get(oid)),
                                  REGISTRY if registry is None else registry)

    def test_a_genuine_record_verifies_and_carries_its_dependency_digest(self):
        verified = self.verify()
        self.assertIsInstance(verified, VerifiedAttestation)
        self.assertEqual(verified.claim_key, "contract")
        self.assertEqual(verified.dependency_digest, dependency_digest(self.record["inputs"]))
        self.assertEqual(verified.record["attestation_id"], self.record["attestation_id"])

    def refusal(self, **kwargs) -> Refusal:
        outcome = self.verify(**kwargs)
        self.assertIsInstance(outcome, Refusal, outcome)
        return outcome

    def test_a_flipped_verdict_changes_the_identity_so_the_record_is_rejected(self):
        forged = copy.deepcopy(self.record)
        forged["outcome"]["verdict"] = "pass" if forged["outcome"]["verdict"] != "pass" else "fail"
        self.assertEqual(self.refusal(record=forged).reason_codes, ("record_digest_mismatch",))
        forged["attestation_id"] = sha256_value({k: v for k, v in forged.items() if k != "attestation_id"})
        # Re-signed by whoever edited it: the digest matches again, but its issuer now has to match
        # an independent observation, and its objects still have to exist with the attested bytes.
        self.assertIsInstance(self.verify(record=forged), VerifiedAttestation)

    def test_the_issuer_must_match_the_independent_observation_exactly(self):
        for field, value in (("run_id", 1), ("attempt", 2), ("source_revision", "9" * 40), ("job_id", "other")):
            with self.subTest(field):
                outcome = self.refusal(issuer={**ISSUER, field: value})
                self.assertEqual((outcome.status, outcome.reason_codes), ("rejected", ("issuer_mismatch",)))
                self.assertIn(field, outcome.detail)

    def test_a_local_issuer_is_recorded_but_never_authenticated(self):
        local = {"platform": "local", "workflow_id": "local", "run_id": 0, "attempt": 0, "job_id": "local", "source_revision": KERNEL}
        record = build_attestation(execution(issuer=local))
        outcome = self.refusal(record=record, issuer=local)
        self.assertEqual((outcome.status, outcome.reason_codes), ("rejected", ("issuer_unauthenticated",)))

    def test_a_workflow_success_bit_is_not_an_issuer_observation(self):
        outcome = self.refusal(issuer={"conclusion": "success"})
        self.assertEqual(outcome.reason_codes, ("issuer_unobserved",))

    def test_the_authority_must_be_registered_with_the_same_closure_identities(self):
        self.assertEqual(self.refusal(registry={}).reason_codes, ("authority_unknown",))
        drifted = {"evidence-spine-closure": {**AUTHORITY, "program_closure_sha256": "0" * 64}}
        outcome = self.refusal(registry=drifted)
        self.assertEqual(outcome.reason_codes, ("authority_mismatch",))
        self.assertIn("program_closure_sha256", outcome.detail)

    def test_every_retained_object_is_rehashed(self):
        tampered = {**OBJECTS, "spine/builder/contract.json": b'{"version":"2.0","verdict":"pass"}'}
        outcome = self.refusal(objects=tampered)
        self.assertEqual((outcome.status, outcome.reason_codes), ("rejected", ("artifact_tampered",)))
        missing = {k: v for k, v in OBJECTS.items() if "certifications" not in k}
        outcome = self.refusal(objects=missing)
        self.assertEqual((outcome.status, outcome.reason_codes), ("insufficient", ("artifact_missing",)))
        self.assertIn("contract-deterministic", outcome.detail)

    def test_tampering_outranks_absence(self):
        objects = {"spine/builder/contract.json": b"other"}
        self.assertEqual(self.refusal(objects=objects).reason_codes, ("artifact_tampered",))

    def test_declared_dependencies_must_be_complete_and_cover_the_required_classes(self):
        partial = build_attestation(execution(inputs=[{**row, "coverage": "partial" if row["identity"] == "toolchain" else "complete"}
                                                     for row in execution().inputs]))
        outcome = self.refusal(record=partial)
        self.assertEqual((outcome.status, outcome.reason_codes), ("insufficient", ("dependency_coverage_unknown",)))
        self.assertIn("toolchain", outcome.detail)
        no_tree = build_attestation(execution(inputs=[row for row in execution().inputs if row["kind"] != "exact-tree"]))
        outcome = self.refusal(record=no_tree)
        self.assertEqual(outcome.reason_codes, ("dependency_class_missing",))
        self.assertIn("exact-tree", outcome.detail)

    def test_a_record_with_extra_or_missing_fields_is_rejected_before_anything_else(self):
        extra = {**self.record, "signature": "trust-me"}
        self.assertEqual(self.refusal(record=extra).reason_codes, ("record_invalid",))
        missing = {k: v for k, v in self.record.items() if k != "issuer"}
        self.assertEqual(self.refusal(record=missing).reason_codes, ("record_invalid",))
        self.assertEqual(self.refusal(record="not a record").reason_codes, ("record_invalid",))


if __name__ == "__main__":
    unittest.main()
