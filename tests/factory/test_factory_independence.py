"""Structural authority separation for independently certified spine claims.

These tests exist because the evidence spine once filled the independent slots of the pre-code
``design`` and ``architecture-governor`` claims with the builder's own post-code
architecture-conformance artifact. The manifest reported independence that had never been earned.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.evidence_closure import DETERMINISTIC_AUTHORITIES, PRODUCERS
from factory_kernel.independence import (
    CERTIFICATE_KIND,
    REGISTRY,
    IndependentAuthority,
    authority_for,
    authority_inputs,
    claims_for_authority,
    build_certificate,
    externally_supplied_claims,
    verify_certificate,
)
from factory_kernel.provenance import BUILDER_CLAIMS
from factory_kernel.spine import load_policy

ROOT = Path(__file__).parents[2]
HEAD = "b" * 40
BASE = "a" * 40


def hashes() -> dict[str, str]:
    return {
        claim_id: sha256_value({"claim": claim_id})
        for claim_id in (
            "contract", "context", "architecture-policy", "design",
            "architecture-governor", "architecture-drift", "architecture-conformance",
        )
    }


def certificate(claim_id: str, **overrides) -> dict:
    value = build_certificate(
        claim_id=claim_id,
        claim_hashes=hashes(),
        head_sha=HEAD,
        base_sha=BASE,
        judgement={"version": "1.0", "verdict": "pass", "certifies": claim_id, "findings": []},
    )
    value.update(overrides)
    return value


def verify(claim_id: str, value: dict, *, builder: dict | None = None) -> dict:
    return verify_certificate(
        value,
        claim_id=claim_id,
        claim_hashes=hashes(),
        builder_artifact_hashes=builder if builder is not None else {},
        head_sha=HEAD,
        base_sha=BASE,
    )


class RegistryTests(unittest.TestCase):
    def test_every_independence_required_claim_has_a_registered_authority(self):
        """Raising policy without adding an authority must fail here, not silently at merge."""
        policy = load_policy(ROOT / ".factory" / "evidence-spine.json")
        required = {
            requirement.claim_id
            for requirement in policy.requirements
            if requirement.independent_required
        }
        self.assertTrue(required)
        for claim_id in sorted(required):
            self.assertEqual(authority_for(claim_id).claim_id, claim_id)

    def test_every_authority_is_shown_everything_its_claim_is_bound_to(self):
        """Independence without competence is not independence."""
        for entry in REGISTRY:
            self.assertTrue(set(entry.binds) <= set(entry.sees), entry.claim_id)
            self.assertIn(entry.subject_claim, entry.sees, entry.claim_id)

    def test_an_authority_blind_to_its_bindings_is_refused(self):
        with self.assertRaisesRegex(ValueError, "it is never shown design"):
            IndependentAuthority(
                claim_id="architecture-conformance",
                authority_id="architecture-holdout",
                subject_claim="architecture-conformance",
                binds=("design", "architecture-conformance"),
                sees=("architecture-conformance",),
                externally_supplied=False,
            )

    def test_an_authority_blind_to_its_subject_is_refused(self):
        with self.assertRaisesRegex(ValueError, "never shown the design artifact"):
            IndependentAuthority(
                claim_id="design",
                authority_id="blinded-design-certifier",
                subject_claim="design",
                binds=("contract",),
                sees=("contract",),
                externally_supplied=True,
            )

    def test_conformance_authority_sees_the_design_and_governor_it_judges(self):
        """Conformance asserts the code matches the design and the governor's decision."""
        spec = authority_for("architecture-conformance")
        for name in ("architecture-policy", "design", "architecture-governor"):
            self.assertIn(name, spec.sees)
            self.assertIn(name, spec.binds)

    def test_shared_authority_input_covers_every_claim_it_certifies(self):
        claims = claims_for_authority("architecture-holdout")
        self.assertEqual(set(claims), {"architecture-drift", "architecture-conformance"})
        seen, extra = authority_inputs(claims)
        for claim_id in claims:
            self.assertTrue(set(authority_for(claim_id).sees) <= set(seen), claim_id)
        self.assertIn("diff", extra)

    def test_contract_certifier_is_never_shown_the_diff(self):
        """It must judge the contract against the issue, not rationalise from an implementation."""
        spec = authority_for("contract")
        self.assertEqual(spec.extra_inputs, ("issue",))
        self.assertNotIn("diff", spec.extra_inputs)
        for pre_code in ("design", "architecture-governor"):
            self.assertNotIn("diff", authority_for(pre_code).extra_inputs)

    def test_unregistered_claims_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "no independent authority is registered"):
            authority_for("impact")

    def test_no_independent_authority_is_a_builder_or_deterministic_authority(self):
        """An authority is only independent if it is nobody who produced or compiled the claim.

        Disjointness is asserted against *every* producer, not just the claim's own, so an
        independent slot cannot be quietly re-pointed at a builder-path worker such as the
        post-code conformance worker.
        """
        producers = set(PRODUCERS.values())
        for entry in REGISTRY:
            self.assertNotIn(entry.authority_id, producers)
            self.assertNotEqual(entry.authority_id, DETERMINISTIC_AUTHORITIES[entry.claim_id])

    def test_pre_code_claims_are_certified_outside_evidence_closure(self):
        self.assertEqual(
            externally_supplied_claims(),
            frozenset({"contract", "design", "architecture-governor"}),
        )

    def test_contract_is_not_certified_by_the_code_holdout(self):
        """The code holdout judges the diff against the contract; that is the opposite question."""
        contract = authority_for("contract")
        self.assertTrue(contract.externally_supplied)
        self.assertEqual(contract.authority_id, "blinded-contract-certifier")
        self.assertNotEqual(contract.authority_id, PRODUCERS["holdout-code"])
        self.assertNotEqual(contract.authority_id, DETERMINISTIC_AUTHORITIES["holdout-code"])

    def test_every_dedicated_certifier_has_its_own_authority(self):
        dedicated = [
            entry.authority_id for entry in REGISTRY if entry.externally_supplied
        ]
        self.assertEqual(len(dedicated), len(set(dedicated)))

    def test_design_and_governor_do_not_share_one_authority(self):
        self.assertNotEqual(
            authority_for("design").authority_id,
            authority_for("architecture-governor").authority_id,
        )

    def test_certificate_binds_the_whole_pre_code_chain(self):
        self.assertEqual(
            authority_for("architecture-governor").binds,
            ("contract", "context", "architecture-policy", "design", "architecture-governor"),
        )


class CertificateTests(unittest.TestCase):
    def test_valid_certificate_verifies(self):
        for claim_id in ("contract", "design", "architecture-governor"):
            evidence = verify(claim_id, certificate(claim_id))
            self.assertEqual(evidence["verdict"], "pass")
            self.assertEqual(evidence["head_sha"], HEAD)

    def test_kernel_not_model_fills_the_bindings(self):
        """A judgement claiming its own bindings cannot change the envelope."""
        value = build_certificate(
            claim_id="design",
            claim_hashes=hashes(),
            head_sha=HEAD,
            base_sha=BASE,
            judgement={
                "version": "1.0", "verdict": "pass", "findings": [],
                "bindings": {"design": "0" * 64}, "head_sha": "9" * 40,
                "subject_sha256": "0" * 64,
            },
        )
        self.assertEqual(value["subject_sha256"], hashes()["design"])
        self.assertEqual(value["head_sha"], HEAD)
        self.assertEqual(value["bindings"]["design"], hashes()["design"])

    def test_raw_builder_artifact_is_not_a_certificate(self):
        conformance = {"version": "1.0", "verdict": "conform", "head_sha": HEAD}
        with self.assertRaisesRegex(ValueError, "is not an independent certification"):
            verify("design", conformance)

    def test_builder_judgement_is_refused_for_every_builder_claim(self):
        builder = {claim_id: sha256_value({"builder": claim_id}) for claim_id in BUILDER_CLAIMS}
        for source, digest in builder.items():
            value = certificate("design", judgement_sha256=digest)
            with self.assertRaisesRegex(ValueError, f"builder-produced artifact '{source}'"):
                verify("design", value, builder=builder)

    def test_builder_origin_is_checked_before_verdict_and_bindings(self):
        """Origin is structural; a failing or unbound builder artifact is refused as builder-made."""
        builder = {"architecture-conformance": sha256_value({"builder": "conformance"})}
        value = certificate(
            "design",
            judgement_sha256=builder["architecture-conformance"],
            verdict="fail",
            bindings={"design": "0" * 64},
        )
        with self.assertRaisesRegex(ValueError, "builder-produced artifact"):
            verify("design", value, builder=builder)

    def test_wrong_authority_is_refused(self):
        value = certificate("design", authority_id="architecture-conformance-worker")
        with self.assertRaisesRegex(ValueError, "requires authority 'blinded-design-certifier'"):
            verify("design", value)

    def test_certificate_for_another_claim_is_refused(self):
        with self.assertRaisesRegex(ValueError, "certifies 'design'"):
            verify("architecture-governor", certificate("design"))

    def test_kind_must_be_an_independent_certification(self):
        value = certificate("design", kind="architecture-conformance")
        with self.assertRaisesRegex(ValueError, "is not an independent certification"):
            verify("design", value)
        self.assertEqual(certificate("design")["kind"], CERTIFICATE_KIND)

    def test_stale_head_base_and_predecessor_bindings_are_refused(self):
        with self.assertRaisesRegex(ValueError, "different head SHA"):
            verify("design", certificate("design", head_sha="9" * 40))
        with self.assertRaisesRegex(ValueError, "different base SHA"):
            verify("design", certificate("design", base_sha="9" * 40))
        for name in ("contract", "context", "architecture-policy", "design"):
            value = certificate("design")
            value["bindings"][name] = "0" * 64
            with self.assertRaisesRegex(ValueError, f"not bound to this run's {name}"):
                verify("design", value)

    def test_forged_subject_and_failed_verdict_are_refused(self):
        with self.assertRaisesRegex(ValueError, "does not certify the design artifact"):
            verify("design", certificate("design", subject_sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "did not pass"):
            verify("design", certificate("design", verdict="fail"))

    def test_judgement_must_declare_its_own_subject(self):
        """A verdict produced for another purpose cannot be re-wrapped as this certification."""
        code_holdout = {"version": "1.0", "verdict": "pass", "findings": []}
        value = build_certificate(
            claim_id="contract",
            claim_hashes=hashes(),
            head_sha=HEAD,
            base_sha=BASE,
            judgement=code_holdout,
        )
        with self.assertRaisesRegex(ValueError, "does not certify contract"):
            verify("contract", value)

    def test_judgement_declaring_another_claim_is_refused(self):
        value = build_certificate(
            claim_id="design",
            claim_hashes=hashes(),
            head_sha=HEAD,
            base_sha=BASE,
            judgement={"version": "1.0", "verdict": "pass", "certifies": "contract",
                       "findings": []},
        )
        with self.assertRaisesRegex(ValueError, "does not certify design"):
            verify("design", value)

    def test_in_process_wrapping_does_not_require_a_declared_subject(self):
        """Closure constructs those envelopes itself around a judgement of known purpose."""
        value = build_certificate(
            claim_id="architecture-drift",
            claim_hashes=hashes(),
            head_sha=HEAD,
            base_sha=BASE,
            judgement={"version": "1.0", "verdict": "pass"},
        )
        self.assertEqual(verify("architecture-drift", value)["verdict"], "pass")

    def test_certificate_cannot_certify_its_own_judgement(self):
        value = certificate("design", judgement_sha256=hashes()["design"])
        with self.assertRaisesRegex(ValueError, "certifies its own judgement"):
            verify("design", value)


if __name__ == "__main__":
    unittest.main()
