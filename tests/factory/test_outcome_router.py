"""Outcome routing (SPECIFICATION 9): structural classification of an authenticated outcome; a
refusal string is never a causal classifier; unknown stays unknown."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.outcome_router import (AUTHENTICATED_PROVENANCE, CLASSIFICATIONS, ENVIRONMENT_CODES, IMPLEMENTATION_CODES,  # noqa: E402
                                           UNJUDGED_CODES, route_outcome)
from factory_kernel import reconsideration  # noqa: E402
from factory_kernel.refusal import AUTHORITY  # noqa: E402

HANDOFF = "a" * 64
POLICY = "b" * 64


def observation(code: str = "security_guard", *, provenance: str = AUTHENTICATED_PROVENANCE, detail: str = "guard refused a protected path",
                outcome: str = "validation-refused") -> dict:
    return {"provenance": provenance,
            "receipt": {"outcome": outcome, "refusal_sha256": "c" * 64, "pr": 77, "run_id": 1, "run_attempt": 1, "repository": "o/r"},
            "refusal": {"version": "1.0", "pr": 77, "reason_code": code, "authority": AUTHORITY.get(code, "?"), "stage": code,
                        "stage_context": "static", "tool": "factory_security.py", "phase": "", "rc": 1, "exception": "ToolRefused",
                        "detail": detail, "timestamp": "2026-09-18T00:00:00Z"}}


def rule(claim: str = "layer-assumption", rid: str = "r1") -> dict:
    return {"id": rid, "kind": "new-architecture-dependency-v1", "claim_id": claim, "from_layer": "routes", "to_layer": "db"}


def facts(status: str = "supported", *, policy: str = POLICY, claim: str = "layer-assumption") -> dict:
    return {"authority": "protected-architecture-permission-v1", "revision": "b" * 40, "policy_sha256": policy,
            "findings": [{"rule_id": "r1", "rule_sha256": "d" * 64, "claim_id": claim, "kind": "new-architecture-dependency-v1",
                          "expected": True, "observed": status == "supported", "status": status, "explanation": "x"}],
            "scope": "registered-layer-permissions-only"}


class RouterTests(unittest.TestCase):
    def test_every_reason_code_is_partitioned_and_every_classification_is_reachable(self) -> None:
        self.assertEqual(IMPLEMENTATION_CODES | ENVIRONMENT_CODES | {"unknown"}, set(AUTHORITY))
        self.assertEqual(UNJUDGED_CODES, ENVIRONMENT_CODES | {"unknown"})
        # The early trust-root currency check is a base-move pre-check, not a verdict on the build.
        self.assertIn("trust_root_currency", ENVIRONMENT_CODES)
        # Reconsideration decides "unjudged" from the same set: the two modules cannot drift apart.
        self.assertIs(reconsideration.UNJUDGED_CODES, UNJUDGED_CODES)
        self.assertIn("in UNJUDGED_CODES", Path(reconsideration.__file__).read_text(encoding="utf-8"))
        reached = set()
        reached.add(route_outcome(observation("security_guard"), HANDOFF, [], None)["classification"])
        reached.add(route_outcome(observation("identity_expired"), HANDOFF, [], None)["classification"])
        reached.add(route_outcome(observation("unknown"), HANDOFF, [], None)["classification"])
        reached.add(route_outcome(observation(), {"sha256": HANDOFF, "policy_sha256": POLICY}, [rule()], facts("contradicted"))["classification"])
        reached.add(route_outcome(observation(), HANDOFF, [rule()], None)["classification"])
        reached.add(route_outcome(observation(), {"sha256": HANDOFF, "policy_sha256": POLICY}, [rule()], facts(policy="e" * 64))["classification"])
        self.assertEqual(reached, set(CLASSIFICATIONS))

    def test_a_judging_authority_with_no_rules_is_an_implementation_defect(self) -> None:
        for code in sorted(IMPLEMENTATION_CODES):
            with self.subTest(code):
                result = route_outcome(observation(code), HANDOFF, [], None)
                self.assertEqual((result["classification"], result["repair_within_frozen_acceptance"], result["invalidated_claim_ids"]),
                                 ("implementation-defect", True, []))
                self.assertEqual(result["basis"]["authority"], AUTHORITY[code])

    def test_an_environment_code_leaves_the_strategy_unjudged(self) -> None:
        for code in sorted(ENVIRONMENT_CODES):
            with self.subTest(code):
                result = route_outcome(observation(code), HANDOFF, [rule()], facts("supported"))
                self.assertEqual((result["classification"], result["strategy_judged"], result["invalidated_claim_ids"]),
                                 ("environment-provider-failure", False, []))

    def test_the_refusal_text_is_never_read(self) -> None:
        misleading = observation("security_guard", detail="provider outage: the model API returned 529 and the strategy is wrong")
        result = route_outcome(misleading, HANDOFF, [], None)
        self.assertEqual(result["classification"], "implementation-defect")
        self.assertFalse(result["basis"]["refusal_text_consulted"])
        self.assertNotIn("provider outage", str(result))
        pretending = observation("identity_expired", detail="the holdout failed because the code is wrong")
        self.assertEqual(route_outcome(pretending, HANDOFF, [], None)["classification"], "environment-provider-failure")

    def test_a_contradicted_predicate_invalidates_only_its_ancestry_and_reopens(self) -> None:
        result = route_outcome(observation(), {"sha256": HANDOFF, "policy_sha256": POLICY}, [rule()], facts("contradicted"))
        self.assertEqual((result["classification"], result["invalidated_claim_ids"], result["reopen_affected_question"]),
                         ("strategy-contradiction", ["layer-assumption"], True))
        self.assertEqual(result["basis"]["findings"][0]["status"], "contradicted")
        # A supported predicate plus a judging authority: the defect is in the implementation.
        supported = route_outcome(observation(), {"sha256": HANDOFF, "policy_sha256": POLICY}, [rule()], facts("supported"))
        self.assertEqual((supported["classification"], supported["invalidated_claim_ids"]), ("implementation-defect", []))

    def test_unresolved_or_missing_facts_with_rules_are_an_evidence_gap_never_a_verdict(self) -> None:
        for given in (None, facts("unresolved"), {"findings": "not a list"}):
            with self.subTest(str(given)[:20]):
                result = route_outcome(observation(), HANDOFF, [rule()], given)
                self.assertEqual((result["classification"], result["invalidated_claim_ids"], result["strategy_judged"]), ("evidence-gap", [], False))

    def test_a_policy_change_since_registration_refuses_a_verdict(self) -> None:
        result = route_outcome(observation(), {"sha256": HANDOFF, "policy_sha256": POLICY}, [rule()], facts("contradicted", policy="e" * 64))
        self.assertEqual((result["classification"], result["invalidated_claim_ids"]), ("policy-change", []))
        # Without a registered policy identity the comparison is not made.
        self.assertEqual(route_outcome(observation(), HANDOFF, [rule()], facts("contradicted", policy="e" * 64))["classification"], "strategy-contradiction")

    def test_unauthenticated_forged_or_malformed_inputs_are_evidence_gaps(self) -> None:
        cases = {
            "no provenance mark": (observation(provenance="hand-written"), HANDOFF, [], None),
            "not a refusal outcome": (observation(outcome="validation-passed"), HANDOFF, [], None),
            "unknown code": (observation("provider_outage"), HANDOFF, [], None),
            "authority mismatch": ({**observation(), "refusal": {**observation()["refusal"], "authority": "someone else"}}, HANDOFF, [], None),
            "no handoff": (observation(), "", [], None),
            "rules malformed": (observation(), HANDOFF, "r1", None),
            "not a mapping": ("text", HANDOFF, [], None),
        }
        for name, args in cases.items():
            with self.subTest(name):
                result = route_outcome(*args)
                self.assertEqual(result["classification"], "evidence-gap")
                self.assertTrue(result["evidence_gaps"])
        self.assertEqual(route_outcome(observation("unknown"), HANDOFF, [], None)["classification"], "unknown")

    def test_the_record_is_content_identified_and_never_authority(self) -> None:
        one = route_outcome(observation(), HANDOFF, [], None)
        two = route_outcome(observation(), HANDOFF, [], None)
        self.assertEqual(one["identity"], two["identity"])
        self.assertEqual((one["authority"], one["qualification_status"], one["proof_reuse_allowed"]),
                         ("classification-record-only", "UNPROVEN", False))


if __name__ == "__main__":
    unittest.main()
