"""Capabilities (SPECIFICATION 5, WP06): deterministic grants, every gate listed, unknown fails closed."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.capabilities import (DEFAULT_POLICY, FIXED_OPERATIONS, CapabilityRefused, Grant, Refusal,  # noqa: E402
                                         authorize, grant_from_dict, narrow_grant, policy_digest, resources_for, validate_grant)

HEAD, BASE, TREE, EVIDENCE = "a" * 40, "b" * 40, "d" * 40, "e" * 64


def subject(**overrides) -> dict:
    value = {"repository": "octo/dynachat", "pr_number": 134, "head_sha": HEAD, "base_sha": BASE, "head_tree_sha": TREE,
             "evidence_sha256": EVIDENCE}
    value.update(overrides)
    return value


def evidence(**overrides) -> dict:
    value = {"version": "1.0", "base_ref": "main", "base_sha": BASE, "head_sha": HEAD, "head_tree_sha": TREE, "evidence_sha256": EVIDENCE}
    value.update(overrides)
    return value


def request(**overrides) -> dict:
    value = {"operation": "merge_exact_head", "caller_role": "merge-executor", "caller_instance": "kernel:run-1",
             "request_id": "merge-134-aaaaaaaaaaaa", "source_sha": BASE}
    value.update(overrides)
    return value


def lease(**overrides) -> dict:
    value = {"lease_id": "L1", "epoch": "epoch-0", "resources": ["pr:octo/dynachat#134", f"branch:octo/dynachat:base:{BASE}"]}
    value.update(overrides)
    return value


class AuthorizeTests(unittest.TestCase):
    def test_a_well_formed_merge_request_is_granted_and_deterministic(self) -> None:
        one = authorize(request(), subject(), DEFAULT_POLICY, evidence(), None, None, now=1000)
        two = authorize(request(), subject(), DEFAULT_POLICY, evidence(), None, None, now=1000)
        self.assertIsInstance(one, Grant)
        self.assertEqual(one, two)
        self.assertEqual(one.semantic_operation, "merge_exact_head")
        self.assertEqual(one.resources, ("pr:octo/dynachat#134", f"branch:octo/dynachat:base:{BASE}"))
        self.assertEqual((one.issued_at, one.expires_at, one.max_uses), (1000, 1000 + DEFAULT_POLICY["grant_ttl_seconds"], 1))
        self.assertEqual(one.lease_route, "legacy-serial-route")
        self.assertEqual(one.policy_sha256, policy_digest(DEFAULT_POLICY))
        self.assertEqual(one.to_dict()["authority"], "grant-record-only")
        self.assertLess(DEFAULT_POLICY["grant_ttl_seconds"], 1200)  # never outlives the identity's age bound

    def test_every_failing_gate_is_listed_and_unknown_fails_closed(self) -> None:
        cases = {
            "operation_not_fixed": (request(operation="run_command"), subject(), DEFAULT_POLICY, evidence(), None, None),
            "role_unknown": (request(caller_role="root"), subject(), DEFAULT_POLICY, evidence(), None, None),
            "role_not_permitted": (request(caller_role="observer"), subject(), DEFAULT_POLICY, evidence(), None, None),
            "caller_instance_invalid": (request(caller_instance=""), subject(), DEFAULT_POLICY, evidence(), None, None),
            "request_id_invalid": (request(request_id="../x"), subject(), DEFAULT_POLICY, evidence(), None, None),
            "source_sha_invalid": (request(source_sha="main"), subject(), DEFAULT_POLICY, evidence(), None, None),
            "subject_inexact": (request(), subject(head_sha="HEAD"), DEFAULT_POLICY, evidence(), None, None),
            "evidence_missing": (request(), subject(), DEFAULT_POLICY, None, None, None),
            "evidence_head_sha_mismatch": (request(), subject(), DEFAULT_POLICY, evidence(head_sha="c" * 40), None, None),
            "evidence_head_tree_sha_mismatch": (request(), subject(), DEFAULT_POLICY, evidence(head_tree_sha="c" * 40), None, None),
            "evidence_evidence_sha256_mismatch": (request(), subject(), DEFAULT_POLICY, evidence(evidence_sha256="f" * 64), None, None),
            "policy_ttl_invalid": (request(), subject(), {**DEFAULT_POLICY, "grant_ttl_seconds": 0}, evidence(), None, None),
            "policy_max_uses_invalid": (request(), subject(), {**DEFAULT_POLICY, "max_uses": 0}, evidence(), None, None),
            "policy_epoch_invalid": (request(), subject(), {**DEFAULT_POLICY, "revocation_epoch": ""}, evidence(), None, None),
            "lease_required": (request(), subject(), {**DEFAULT_POLICY, "legacy_serial_route": False}, evidence(), None, None),
            "lease_malformed": (request(), subject(), DEFAULT_POLICY, evidence(), {"lease_id": "L1"}, None),
            "lease_resources_insufficient": (request(), subject(), DEFAULT_POLICY, evidence(), lease(resources=["pr:octo/dynachat#134"]), None),
            "budget_malformed": (request(), subject(), DEFAULT_POLICY, evidence(), None, {"microusd": 5}),
        }
        for code, args in cases.items():
            with self.subTest(code):
                result = authorize(*args, now=1000)
                self.assertIsInstance(result, Refusal)
                self.assertIn(code, result.reason_codes)
        several = authorize(request(operation="run_command", caller_role="root"), subject(head_sha="x"), DEFAULT_POLICY, evidence(), None, None, now=1000)
        self.assertEqual(set(several.reason_codes) >= {"operation_not_fixed", "role_unknown", "subject_inexact"}, True)

    def test_a_subject_lie_gets_no_grant(self) -> None:
        # The caller claims a different head than the authority judged: refused, not narrowed.
        result = authorize(request(), subject(head_sha="c" * 40), DEFAULT_POLICY, evidence(), None, None, now=1000)
        self.assertEqual(result.reason_codes, ("evidence_head_sha_mismatch",))

    def test_a_lease_bundle_covering_the_resources_is_recorded_on_the_grant(self) -> None:
        grant = authorize(request(), subject(), DEFAULT_POLICY, evidence(), lease(), None, now=5)
        self.assertIsInstance(grant, Grant)
        self.assertEqual((grant.lease_route, grant.lease["lease_id"]), ("lease-bundle", "L1"))
        without = authorize(request(), subject(), DEFAULT_POLICY, evidence(), None, None, now=5)
        self.assertNotEqual(grant.grant_id, without.grant_id)

    def test_resources_are_derived_never_supplied(self) -> None:
        for operation in FIXED_OPERATIONS:
            with self.subTest(operation):
                self.assertIsInstance(resources_for(operation, subject()), tuple)
        self.assertEqual(resources_for("observe", subject()), ())
        self.assertEqual(resources_for("publish_transition_data", subject()), (f"branch:octo/dynachat:base:{BASE}",))


class ValidateAndNarrowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grant = authorize(request(), subject(), DEFAULT_POLICY, evidence(), None, None, now=1000)
        self.epoch = DEFAULT_POLICY["revocation_epoch"]

    def test_a_live_grant_passes_every_gate(self) -> None:
        self.assertIsNone(validate_grant(self.grant, now=1001, epoch=self.epoch, uses=0, operation="merge_exact_head",
                                         subject=subject(), policy=DEFAULT_POLICY))

    def test_expiry_epoch_uses_operation_subject_and_policy_gates(self) -> None:
        ttl = DEFAULT_POLICY["grant_ttl_seconds"]
        cases = {
            "grant_expired": dict(now=1000 + ttl, epoch=self.epoch, uses=0),
            "grant_revoked_by_epoch": dict(now=1001, epoch="epoch-1", uses=0),
            "grant_uses_exhausted": dict(now=1001, epoch=self.epoch, uses=1),
            "operation_mismatch": dict(now=1001, epoch=self.epoch, uses=0, operation="observe"),
            "subject_mismatch": dict(now=1001, epoch=self.epoch, uses=0, subject=subject(head_sha="c" * 40)),
            "policy_changed": dict(now=1001, epoch=self.epoch, uses=0, policy={**DEFAULT_POLICY, "grant_ttl_seconds": 10}),
            "clock_invalid": dict(now=-1, epoch=self.epoch, uses=0),
            "uses_invalid": dict(now=1001, epoch=self.epoch, uses=-1),
        }
        for code, kwargs in cases.items():
            with self.subTest(code):
                result = validate_grant(self.grant, **kwargs)
                self.assertIsInstance(result, Refusal)
                self.assertIn(code, result.reason_codes)
        self.assertEqual(validate_grant("not a grant", now=1, epoch=self.epoch, uses=0).reason_codes, ("grant_malformed",))
        self.assertIsNone(validate_grant(self.grant, now=1000 + ttl - 1, epoch=self.epoch, uses=0))

    def test_narrowing_is_a_subset_never_wider(self) -> None:
        narrowed = narrow_grant(self.grant, resources=("pr:octo/dynachat#134",))
        self.assertEqual((narrowed.resources, narrowed.narrowed_from, narrowed.expires_at), (("pr:octo/dynachat#134",), self.grant.grant_id, self.grant.expires_at))
        self.assertNotEqual(narrowed.grant_id, self.grant.grant_id)
        self.assertIs(narrow_grant(self.grant, resources=self.grant.resources), self.grant)
        for bad in ((), ("pr:octo/dynachat#999",), ("pr:octo/dynachat#134", "pr:octo/dynachat#134")):
            with self.subTest(bad), self.assertRaises(CapabilityRefused):
                narrow_grant(self.grant, resources=bad)

    def test_a_grant_round_trips_through_its_record(self) -> None:
        self.assertEqual(grant_from_dict(self.grant.to_dict()), self.grant)
        with self.assertRaises(CapabilityRefused):
            grant_from_dict({"schema": "other"})
        with self.assertRaises(CapabilityRefused):
            grant_from_dict({**self.grant.to_dict(), "max_uses": "many"})


if __name__ == "__main__":
    unittest.main()
