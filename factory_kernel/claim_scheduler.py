"""Pure allowed-actions compiler (SPECIFICATION 4.1, contract C04), in shadow.

Given the claims, the protected spine policy, verified proof, the programme and one bundle of
trusted observations, `allowed_actions` returns proposals: the registered actions whose subject
matches the current programme, whose control observations are sufficient, whose prerequisites
are satisfied, whose ownership is not contested and whose required resources are declared. It
proposes; it never launches, labels, reserves or merges. No provider, GitHub, SQLite, clock or
mutable filesystem access happens here: time and every remote fact enter as observed values.

The blocked side is a separate explanation (`explain_actions`), so a caller that wants to know
why nothing is proposed reads reasons, not an empty tuple. A known stopped or fenced control
plane blocks before budget is consulted; an unobservable control plane blocks everything; merge
is proposed only with the entire required closure current for the exact verified head.

`compare_legacy_frontier` records how the proposals relate to the existing dispatcher's next
legal action (`dispatch_plan.select_dispatch`), classifying agreement or the kind of
disagreement; the record is retained for cutover analysis and authorises nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .canonical import sha256_value
from .spine import SpinePolicy

SCHEMA = "dark-factory/action-proposals"
SCHEMA_VERSION = "1.0"
CURRENT = "current"

# Registered actions map onto the stages the runtime already has (dispatch_plan.PAID_ACTIONS and
# the legacy order): no new stage order is invented here.
ACTIONS: Mapping[str, Mapping[str, Any]] = {
    "reconcile-lease": {"class": "reconciliation", "paid": False, "resources": ("github:write",)},
    "resume-pr": {"class": "implementation", "paid": True, "resources": ("github:write", "provider:build")},
    "validate-pr": {"class": "authority", "paid": True, "resources": ("github:write", "provider:judges", "validation:database")},
    "merge-pr": {"class": "authority", "paid": False, "resources": ("github:merge",)},
    "rehead-pr": {"class": "implementation", "paid": False, "resources": ("github:write",)},
    "build-issue": {"class": "implementation", "paid": True, "resources": ("github:write", "provider:build")},
    "materialize-programme": {"class": "discovery", "paid": False, "resources": ("github:write",)},
}
LEGACY_ORDER = ("resume-pr", "validate-pr", "rehead-pr", "build-issue", "materialize-programme")


@dataclass(frozen=True)
class ActionProposal:
    action_id: str
    kind: str
    subject_digest: str
    obligation_ids: tuple[str, ...]
    prerequisite_attestation_ids: tuple[str, ...]
    required_resources: tuple[str, ...]
    policy_digest: str
    input_digest: str
    subject: Mapping[str, Any] | None = None

    def to_dict(self) -> dict:
        return {"action_id": self.action_id, "kind": self.kind, "subject_digest": self.subject_digest,
                "subject": dict(self.subject) if self.subject else None,
                "obligation_ids": list(self.obligation_ids), "prerequisite_attestation_ids": list(self.prerequisite_attestation_ids),
                "required_resources": list(self.required_resources), "policy_digest": self.policy_digest,
                "input_digest": self.input_digest}


@dataclass(frozen=True)
class Blocked:
    kind: str
    subject: Mapping[str, Any] | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"kind": self.kind, "subject": dict(self.subject) if self.subject else None, "reason_codes": list(self.reason_codes)}


def _subject_digest(kind: str, subject: Mapping[str, Any] | None) -> str:
    return sha256_value({"kind": kind, "subject": dict(subject) if subject else None})


def _numbers(value: Any) -> list[int]:
    rows: list[int] = []
    for item in value if isinstance(value, (list, tuple)) else ():
        number = item.get("number") if isinstance(item, Mapping) else item
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            rows.append(number)
    return sorted(set(rows))


def _observations(raw: Mapping[str, Any]) -> dict:
    """The observed values this compiler reads, normalised. Every key is a trusted observation
    supplied by the caller; nothing here is looked up."""
    if not isinstance(raw, Mapping):
        raise ValueError("observations must be a mapping of trusted values")
    budget = raw.get("budget")
    result = {
        "control_observed": raw.get("control_observed") is True,
        "stopped": raw.get("stopped") is True,
        "fenced": raw.get("fenced") is True,
        "reconciliation_required": raw.get("reconciliation_required") is True,
        "budget": budget if budget in (True, False) else None,
        "resume": raw.get("resume") if isinstance(raw.get("resume"), int) and not isinstance(raw.get("resume"), bool) else None,
        "review": _numbers(raw.get("review")),
        "rehead": _numbers(raw.get("rehead")),
        "build": _numbers(raw.get("build")),
        "programme_ready": raw.get("programme_ready") is True,
        "continuation": str(raw.get("continuation") or ""),
        # pr number -> observed head sha (exact head under judgement), issue number -> programme item id,
        # subject -> lease holder, pr number -> exact-head check observed for that head.
        "heads": {int(k): str(v) for k, v in (raw.get("heads") or {}).items() if str(v)},
        "issue_items": {int(k): str(v) for k, v in (raw.get("issue_items") or {}).items()},
        # programme item id -> {issue, pr, head}: the observed binding of an item to its work.
        "items": {str(k): {"issue": v.get("issue"), "pr": v.get("pr"), "head": str(v.get("head") or "") or None}
                  for k, v in (raw.get("items") or {}).items() if isinstance(v, Mapping)},
        "leases": {str(k): str(v) for k, v in (raw.get("leases") or {}).items()},
        "exact_head_checked": {int(k): str(v) for k, v in (raw.get("exact_head_checked") or {}).items()},
    }
    for item_id, item in result["items"].items():
        issue = item.get("issue")
        if isinstance(issue, int) and not isinstance(issue, bool):
            result["issue_items"].setdefault(issue, item_id)
    return result


def _proof_for(verified_proof: Mapping[str, Any], head: str) -> Mapping[str, Any] | None:
    """Verified proof keyed by exact head: {claim_id: {"status": ..., "attestation_id": ...}}."""
    entry = verified_proof.get(head) if isinstance(verified_proof, Mapping) else None
    return entry if isinstance(entry, Mapping) else None


def explain_actions(claims: Any, policy: SpinePolicy, verified_proof: Mapping[str, Any], programme: Any,
                    observations: Mapping[str, Any]) -> dict:
    """Proposals plus the reasons for everything not proposed. Pure."""
    o = _observations(observations)
    policy_digest = policy.sha256()
    claim_digest = claims.digest() if claims is not None and hasattr(claims, "digest") else None
    programme_sha = getattr(programme, "sha256", None)
    input_digest = sha256_value({"observations": o, "claims": claim_digest, "programme": programme_sha,
                                 "proof": sorted(verified_proof) if isinstance(verified_proof, Mapping) else None,
                                 "policy": policy_digest})
    proposals: list[ActionProposal] = []
    blocked: list[Blocked] = []
    policy_ids = tuple(req.claim_id for req in policy.requirements)

    def propose(kind: str, subject: Mapping[str, Any] | None, obligations: tuple[str, ...] = (),
                attestations: tuple[str, ...] = ()) -> None:
        digest = _subject_digest(kind, subject)
        proposals.append(ActionProposal(action_id=sha256_value({"kind": kind, "subject": digest, "input": input_digest})[:32],
                                        kind=kind, subject_digest=digest, obligation_ids=obligations,
                                        prerequisite_attestation_ids=attestations,
                                        required_resources=tuple(ACTIONS[kind]["resources"]), policy_digest=policy_digest,
                                        input_digest=input_digest, subject=subject))

    def refuse(kind: str, subject: Mapping[str, Any] | None, *codes: str) -> None:
        blocked.append(Blocked(kind, subject, tuple(codes)))

    # Control before work. A stopped or fenced control plane outranks budget; an unobservable one
    # blocks everything, because nothing below can be trusted to be current.
    control_block = None
    if not o["control_observed"]:
        control_block = "control_unobserved"
    elif o["stopped"]:
        control_block = "stopped"
    elif o["fenced"]:
        control_block = "fenced"
    if control_block is not None:
        for kind in ACTIONS:
            refuse(kind, None, control_block)
        return _result(proposals, blocked, input_digest, policy_digest)

    if o["reconciliation_required"]:
        propose("reconcile-lease", {"kind": "leases"})
    reconciling = ("reconciliation_pending",) if o["reconciliation_required"] else ()

    def paid_block(kind: str) -> tuple[str, ...]:
        if not ACTIONS[kind]["paid"]:
            return ()
        if o["budget"] is None:
            return ("budget_unobserved",)
        return () if o["budget"] else ("budget_required",)

    def lease_block(subject_key: str) -> tuple[str, ...]:
        return ("lease_held",) if subject_key in o["leases"] else ()

    if o["resume"] is not None:
        subject = {"pr": o["resume"]}
        codes = reconciling + paid_block("resume-pr") + lease_block(f"pr:{o['resume']}")
        propose("resume-pr", subject) if not codes else refuse("resume-pr", subject, *codes)

    for pr in o["review"]:
        subject = {"pr": pr, "head": o["heads"].get(pr)}
        codes = reconciling + paid_block("validate-pr") + lease_block(f"pr:{pr}")
        propose("validate-pr", subject) if not codes else refuse("validate-pr", subject, *codes)
        # Merge needs the whole required closure current for the exact verified head, and the
        # exact-head check observed for that same head; never merely "every new claim".
        head = o["heads"].get(pr)
        proof = _proof_for(verified_proof, head) if head else None
        if proof is None:
            refuse("merge-pr", subject, *(reconciling + ("proof_unavailable",)))
            continue
        incomplete = tuple(f"obligation_incomplete:{claim_id}" for claim_id in policy_ids
                           if not isinstance(proof.get(claim_id), Mapping) or proof[claim_id].get("status") != CURRENT)
        exact = () if o["exact_head_checked"].get(pr) == head else ("exact_head_unverified",)
        codes = reconciling + incomplete + exact + lease_block(f"pr:{pr}")
        if codes:
            refuse("merge-pr", subject, *codes)
        else:
            propose("merge-pr", subject, obligations=policy_ids,
                    attestations=tuple(str(proof[claim_id].get("attestation_id") or "") for claim_id in policy_ids))

    for pr in o["rehead"]:
        subject = {"pr": pr}
        codes = reconciling + lease_block(f"pr:{pr}")
        propose("rehead-pr", subject) if not codes else refuse("rehead-pr", subject, *codes)

    item_obligations = dict(getattr(claims, "item_obligations", {}) or {}) if claims is not None else {}
    for issue in o["build"]:
        item = o["issue_items"].get(issue)
        subject = {"issue": issue, "item": item}
        codes = list(reconciling + paid_block("build-issue") + lease_block(f"issue:{issue}"))
        obligations: tuple[str, ...] = ()
        if programme is not None and item is not None:
            items = {row["id"]: row for row in getattr(programme, "items", ())}
            if item not in items:
                codes.append("subject_outside_programme")
            else:
                obligations = tuple(item_obligations.get(item, ()))
                for blocker in items[item].get("blocked_by", ()):
                    blocker_head = _head_of(o, blocker)
                    proof = _proof_for(verified_proof, blocker_head) if blocker_head else None
                    if proof is None or any(not isinstance(proof.get(c), Mapping) or proof[c].get("status") != CURRENT for c in policy_ids):
                        codes.append(f"predecessor_incomplete:{blocker}")
        propose("build-issue", subject, obligations=obligations) if not codes else refuse("build-issue", subject, *codes)

    if o["continuation"] or o["programme_ready"]:
        subject = {"continuation": o["continuation"] or None}
        propose("materialize-programme", subject) if not reconciling else refuse("materialize-programme", subject, *reconciling)

    return _result(proposals, blocked, input_digest, policy_digest)


def _head_of(o: Mapping[str, Any], item: str) -> str | None:
    """The exact head observed for a programme item's PR, from the item binding or the PR heads."""
    bound = o["items"].get(item) or {}
    if bound.get("head"):
        return str(bound["head"])
    pr = bound.get("pr")
    return o["heads"].get(pr) if isinstance(pr, int) and not isinstance(pr, bool) else None


def _result(proposals: list[ActionProposal], blocked: list[Blocked], input_digest: str, policy_digest: str) -> dict:
    order = {kind: index for index, kind in enumerate(("reconcile-lease", *LEGACY_ORDER, "merge-pr"))}
    proposals.sort(key=lambda p: (order.get(p.kind, 99), p.subject_digest))
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "authority": "proposal-only",
            "input_digest": input_digest, "policy_digest": policy_digest,
            "proposals": tuple(proposals), "blocked": tuple(blocked)}


def allowed_actions(claims: Any, policy: SpinePolicy, verified_proof: Mapping[str, Any], programme: Any,
                    observations: Mapping[str, Any]) -> tuple[ActionProposal, ...]:
    """Proposals only. Reasons for what is missing live in `explain_actions`."""
    return explain_actions(claims, policy, verified_proof, programme, observations)["proposals"]


def compare_legacy_frontier(explanation: Mapping[str, Any], dispatch_plan: Mapping[str, Any]) -> dict:
    """Shadow comparison with the existing dispatcher's next legal action (a plan-dispatch record).

    `agree`: the legacy action is among the proposals (or both idle / both blocked for the same
    control reason / legacy blocked on budget and the shadow proposes nothing). `shadow_stricter`:
    legacy is ready but the shadow blocks that action. `shadow_looser`: the shadow proposes work
    legacy would not start. `different_choice`: the legacy action is neither proposed nor blocked
    by the shadow (the shadow never saw that subject). `different_block`: both hold work but for
    different control reasons (legacy stopped, shadow fenced), or legacy waits on reconciliation
    the shadow does not see. Retained, never acted on.
    """
    proposals = [p.to_dict() if isinstance(p, ActionProposal) else dict(p) for p in explanation.get("proposals", ())]
    blocked = [b.to_dict() if isinstance(b, Blocked) else dict(b) for b in explanation.get("blocked", ())]
    legacy = {"status": dispatch_plan.get("status"), "action": dispatch_plan.get("action"),
              "subject": dispatch_plan.get("subject"), "reason_codes": list(dispatch_plan.get("reason_codes") or [])}
    proposed = [(p["kind"], _legacy_subject(p)) for p in proposals if p["kind"] != "merge-pr"]
    blocked_pairs = {(b["kind"], _legacy_subject(b)) for b in blocked}
    shadow_first = proposed[0] if proposed else None
    legacy_pair = (legacy["action"], legacy["subject"]) if legacy["status"] == "ready" else None
    control = {"control_unobserved", "stopped", "fenced"}
    if legacy["status"] == "blocked":
        codes = set(legacy["reason_codes"])
        if codes & control:
            same = any(set(b["reason_codes"]) & codes for b in blocked) and not proposed
        elif codes == {"reconciliation_required"}:
            same = any(p["kind"] == "reconcile-lease" for p in proposals)
        else:  # budget_required: legacy would not start paid work
            same = not proposed
        classification = "agree" if same else ("shadow_looser" if proposed else "different_block")
    elif legacy["status"] == "idle":
        classification = "agree" if not proposed else "shadow_looser"
    elif legacy_pair in proposed:
        # Priorities live outside this compiler: the legacy choice being allowed is agreement,
        # whichever proposal the shadow lists first.
        classification = "agree"
    elif legacy_pair in blocked_pairs:
        classification = "shadow_stricter"
    else:
        classification = "different_choice"
    return {"schema": "dark-factory/frontier-comparison", "schema_version": "1.0", "authority": "shadow-record",
            "legacy": legacy, "shadow_first": list(shadow_first) if shadow_first else None,
            "proposals": proposals, "blocked": blocked, "classification": classification}


def _legacy_subject(row: Mapping[str, Any]) -> int | None:
    subject = row.get("subject") or {}
    for key in ("pr", "issue"):
        if isinstance(subject.get(key), int):
            return subject[key]
    return None


__all__ = ["ACTIONS", "ActionProposal", "Blocked", "allowed_actions", "compare_legacy_frontier", "explain_actions"]
