"""Outcome routing (SPECIFICATION 9, WP09): what an authenticated factory outcome is evidence of.

`route_outcome(authenticated_receipt, admitted_handoff, registered_rules, independently_observed_facts)`
classifies one imported outcome as exactly one of: implementation defect, strategy contradiction,
environment/provider failure, evidence gap, policy change, or unknown. Every input is structural:
the kernel's refusal record (its reason code and the authority that code names), the recorded
handoff identity, the rules the owner registered before selection, and the independent findings
`strategy_findings.establish` produced from protected policy. No refusal string is read: a
refusal's prose is context for a human, never a cause. Unknown stays unknown.

What a classification licenses, and only that:
- strategy-contradiction: the registered assumption ancestry the finding names is invalidated and
  the affected question reopens (the assessment does this; the router only names the claims);
- implementation-defect: ordinary repair within the existing frozen acceptance boundary;
- environment-provider-failure: the strategy is unjudged; nothing is invalidated;
- evidence-gap: a registered question could not be decided from the facts available; nothing
  is invalidated and nothing is inferred from the refusal alone;
- policy-change: the protected policy the rules were registered under is not the policy the
  facts were observed under; the rules must be re-registered before any verdict;
- unknown: no authority reported a code the router maps.
"""
from __future__ import annotations

from typing import Any, Mapping

from .canonical import sha256_value
from .refusal import AUTHORITY

SCHEMA = "dark-factory/outcome-classification"
SCHEMA_VERSION = "1.0"
CLASSIFICATIONS = ("implementation-defect", "strategy-contradiction", "environment-provider-failure", "evidence-gap",
                   "policy-change", "unknown")
AUTHENTICATED_PROVENANCE = "canonical-worker-artifact-and-kernel-receipt"

# The reason codes of factory_kernel.refusal, partitioned by what refused. An authority that judged
# the candidate (guard, holdouts, certifiers, spine, merge pre-authorisation, provenance, attached
# evidence) reports a defect in what was built. The identity broker, a base that moved, and the
# early trust-root currency check report the environment: the currency check is a base-move
# pre-check that runs after the security guard has already spoken on trust-root touches, so what
# reaches its code (after every stale-base text is classified `stale_base`) is a stale validator
# worktree, a gh/git failure, or the guard-duplicate check that the guard's own verdict already
# covers; routing that residue as unjudged withholds a repair licence rather than granting one.
# `unknown` reports nothing. `reconsideration.py` uses the same unjudged set, so the two modules
# cannot disagree about which refusals judged the candidate.
IMPLEMENTATION_CODES = frozenset({"security_guard", "attached_evidence", "code_holdout", "provenance",
                                  "architecture_holdout", "certifier:contract", "certifier:design", "certifier:governor",
                                  "evidence_spine", "merge_preauth"})
ENVIRONMENT_CODES = frozenset({"identity", "identity_expired", "stale_base", "trust_root_currency"})
UNJUDGED_CODES = ENVIRONMENT_CODES | {"unknown"}
assert IMPLEMENTATION_CODES | UNJUDGED_CODES == set(AUTHORITY), "every reason code must be partitioned"
assert not IMPLEMENTATION_CODES & UNJUDGED_CODES


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def route_outcome(authenticated_receipt: Mapping[str, Any], admitted_handoff: Any, registered_rules: Any,
                  independently_observed_facts: Mapping[str, Any] | None) -> dict:
    """Deterministic. `authenticated_receipt` is the observation `feedback_observation.observe_feedback`
    returned (it carries the kernel's receipt and refusal record and its provenance mark);
    `admitted_handoff` is the recorded handoff digest, or a mapping with `sha256` and the
    `policy_sha256` the rules were registered under; `registered_rules` the rules registered before
    selection (may be empty); `independently_observed_facts` the `establish` report or None."""
    basis: dict[str, Any] = {"refusal_text_consulted": False}
    handoff_sha = admitted_handoff.get("sha256") if isinstance(admitted_handoff, Mapping) else admitted_handoff
    registered_policy = admitted_handoff.get("policy_sha256") if isinstance(admitted_handoff, Mapping) else None
    rules = list(registered_rules) if isinstance(registered_rules, (list, tuple)) else None
    gaps: list[str] = []
    if not isinstance(authenticated_receipt, Mapping) or authenticated_receipt.get("provenance") != AUTHENTICATED_PROVENANCE:
        gaps.append("receipt_not_authenticated")
    receipt = authenticated_receipt.get("receipt") if isinstance(authenticated_receipt, Mapping) else None
    refusal = authenticated_receipt.get("refusal") if isinstance(authenticated_receipt, Mapping) else None
    if not isinstance(receipt, Mapping) or receipt.get("outcome") != "validation-refused" or not _sha(receipt.get("refusal_sha256")):
        gaps.append("receipt_outcome_unrecognised")
    code = refusal.get("reason_code") if isinstance(refusal, Mapping) else None
    if code not in AUTHORITY or (isinstance(refusal, Mapping) and refusal.get("authority") != AUTHORITY[code]):
        gaps.append("refusal_code_unrecognised")
        code = None
    if not _sha(handoff_sha):
        gaps.append("handoff_unrecorded")
    if rules is None:
        gaps.append("rules_malformed")
    basis.update({"handoff_sha256": handoff_sha if _sha(handoff_sha) else None, "reason_code": code,
                  "authority": AUTHORITY.get(code) if code else None, "registered_rule_ids": [r.get("id") for r in rules] if rules else [],
                  "findings": None})
    facts = independently_observed_facts if isinstance(independently_observed_facts, Mapping) else None
    findings = facts.get("findings") if facts is not None and isinstance(facts.get("findings"), list) else None
    if findings is not None:
        basis["findings"] = [{k: row.get(k) for k in ("rule_id", "claim_id", "status", "rule_sha256")} for row in findings]
        basis["facts_authority"] = facts.get("authority")
        basis["facts_policy_sha256"] = facts.get("policy_sha256")

    def record(classification: str, *, invalidated: list | None = None, reopen: bool = False, detail: str) -> dict:
        value = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "classification": classification,
                 "invalidated_claim_ids": sorted(set(invalidated or [])), "reopen_affected_question": reopen,
                 "repair_within_frozen_acceptance": classification == "implementation-defect",
                 "strategy_judged": classification in ("strategy-contradiction", "implementation-defect"),
                 "detail": detail, "basis": basis, "evidence_gaps": gaps,
                 "authority": "classification-record-only", "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}
        return {**value, "identity": sha256_value(value)}

    if gaps:
        return record("evidence-gap", detail="the outcome's provenance, receipt, refusal code, handoff or rules could not be established")
    assert rules is not None
    if rules:
        if facts is None or findings is None:
            return record("evidence-gap", detail="registered rules exist but no independent findings were established; the refusal alone decides nothing about the strategy")
        if registered_policy is not None and facts.get("policy_sha256") not in (None, registered_policy):
            return record("policy-change", detail="the protected policy the facts were observed under differs from the policy the rules were registered under; re-register before any verdict")
        contradicted = [row.get("claim_id") for row in findings if row.get("status") == "contradicted"]
        if contradicted:
            return record("strategy-contradiction", invalidated=contradicted, reopen=True,
                          detail="a registered predicate was contradicted by independently observed protected policy; only its assumption ancestry is invalidated")
        if any(row.get("status") != "supported" for row in findings):
            return record("evidence-gap", detail="a registered predicate is unresolved; the strategy question stays open")
    if code in ENVIRONMENT_CODES:
        return record("environment-provider-failure", detail=f"the refusal was reported by {AUTHORITY[code]}; the strategy is unjudged")
    if code in IMPLEMENTATION_CODES:
        return record("implementation-defect", detail=f"the refusal was reported by {AUTHORITY[code]}" + (
            "; every registered predicate is supported" if rules else "; no strategy rule is registered") + "; repair stays within the frozen acceptance")
    return record("unknown", detail="no authority reported this failure; nothing is attributed")


__all__ = ["AUTHENTICATED_PROVENANCE", "CLASSIFICATIONS", "ENVIRONMENT_CODES", "IMPLEMENTATION_CODES", "SCHEMA", "UNJUDGED_CODES", "route_outcome"]
