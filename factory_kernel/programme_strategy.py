"""Bounded planning advice, bound to programme identity but never proof authority.

The protected programme establishes provenance of the advice, not correctness of its
assertions. Only plan/investigate and context/design receive this input directly. Their output remains a
worker proposal subject to all existing contract, design and independent proof gates.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .canonical import canonical_bytes, sha256_value
from .programme import ProgrammeRefused, _id, _ids, _list, _object, _text

MAX_STRATEGY_BYTES = 32000
CLAIM_FIELDS = {"id", "statement", "kind", "depends_on", "acceptance", "revisit_when", "status"}
TRAJECTORY_FIELDS = {"implementation", "validation", "failure_repair", "migration_reversal"}


def closure(claims, roots):
    found = set(roots)
    while True:
        expanded = found | {dep for key in found for dep in claims[key]["depends_on"]}
        if expanded == found:
            return found
        found = expanded


def validate_strategy(raw, spec):
    """Structural validation is not endorsement or authentication of model assertions."""
    if len(canonical_bytes(raw)) > MAX_STRATEGY_BYTES:
        raise ProgrammeRefused("strategy exceeds bounded planning context")
    value = _object(raw, {"qualification_status", "proof_reuse_allowed", "spec_sha256", "source",
                          "candidate", "claims", "rationale", "remaining_uncertainty"}, "strategy")
    if value["qualification_status"] != "UNPROVEN" or value["proof_reuse_allowed"] is not False:
        raise ProgrammeRefused("strategy cannot certify itself or grant proof reuse")
    if value["spec_sha256"] != sha256_value(spec):
        raise ProgrammeRefused("strategy is bound to a different approved scope")
    source = _object(value["source"], {"session_id", "recommendation_sha256", "context_identity"}, "strategy source")
    _id(source["session_id"])
    for field in ("recommendation_sha256", "context_identity"):
        if not isinstance(source[field], str) or not re.fullmatch(r"[0-9a-f]{64}", source[field]):
            raise ProgrammeRefused("strategy source requires exact SHA256 references")
    candidate = _object(value["candidate"], {"id", "family", "mechanism", "trajectory", "claim_ids"}, "selected candidate")
    _id(candidate["id"])
    _id(candidate["family"])
    _text(candidate["mechanism"], "strategy mechanism", 4000)
    _object(candidate["trajectory"], TRAJECTORY_FIELDS, "strategy trajectory")
    for text in candidate["trajectory"].values():
        _text(text, "strategy trajectory", 4000)
    roots = _ids(candidate["claim_ids"], "selected claims")
    acceptance = {ac["id"] for req in spec["requirements"] for ac in req["acceptance"]}
    claims = {}
    for claim in _list(value["claims"], "causal claims"):
        _object(claim, CLAIM_FIELDS, "causal claim")
        key = _id(claim["id"])
        if key in claims or claim["kind"] not in {"assumption", "strategy"} or claim["status"] != "active":
            raise ProgrammeRefused("strategy requires distinct active unproven causal claims")
        for field in ("statement", "revisit_when"):
            _text(claim[field], "causal " + field, 2000)
        if not set(_ids(claim["acceptance"], "claim acceptance")) <= acceptance:
            raise ProgrammeRefused("strategy claim exceeds approved acceptance")
        _ids(claim["depends_on"], "claim dependencies", empty=True)
        claims[key] = claim
    if not set(roots) <= claims.keys() or any(not set(c["depends_on"]) <= claims.keys() for c in claims.values()):
        raise ProgrammeRefused("strategy omits a selected claim or causal ancestor")
    done = set()
    while len(done) < len(claims):
        ready = {key for key, claim in claims.items() if key not in done and set(claim["depends_on"]) <= done}
        if not ready:
            raise ProgrammeRefused("strategy claim dependency cycle")
        done.update(ready)
    if closure(claims, roots) != claims.keys():
        raise ProgrammeRefused("strategy includes unrelated project claims")
    _text(value["rationale"], "selection rationale", 4000)
    for text in _list(value["remaining_uncertainty"], "remaining uncertainty", empty=True):
        _text(text, "remaining uncertainty", 2000)
    return deepcopy(value)


def strategy_from_session(state, session):
    """Called only after the exploration service has revalidated its stored handoff."""
    recommendation = session["recommendations"][-1]
    candidate = session["candidates"][recommendation["candidate_id"]]
    keys = closure(state["claims"], candidate["claim_ids"])
    return {"qualification_status": "UNPROVEN", "proof_reuse_allowed": False,
            "spec_sha256": session["binding"]["spec_sha256"],
            "source": {"session_id": session["id"], "recommendation_sha256": sha256_value(recommendation),
                       "context_identity": session["context"]["identity"]},
            "candidate": {key: deepcopy(candidate[key]) for key in ("id", "family", "mechanism", "trajectory", "claim_ids")},
            "claims": [{field: deepcopy(state["claims"][key][field]) for field in CLAIM_FIELDS} for key in sorted(keys)],
            "rationale": recommendation["rationale"],
            "remaining_uncertainty": deepcopy(recommendation["remaining_uncertainty"])}


def planning_advice(admission):
    """Use only the kernel's current ProgrammeQueue.admit result, never issue-supplied JSON."""
    if admission is None or admission[0].strategy is None:
        return ""
    programme, item = admission
    if item not in programme.items:
        raise ProgrammeRefused("strategy requested for a nonmember item")
    strategy = validate_strategy(programme.strategy, programme.spec)
    return ("\n\nUNPROVEN PREFLIGHT STRATEGY — PLANNING ADVICE ONLY\n"
            "The original issue defines approved scope. This historical exploration selected a mechanism, "
            "not a qualified implementation. Source hashes identify earlier context; they do not establish "
            "currency at this build. Recheck assumptions against current code and report any contradiction "
            "in the planning or design notes. Active claims are assumptions, not proven facts. Do not expand acceptance, waive "
            "gates or reuse proof on this basis. Other alternatives' production outcomes remain unknown.\n"
            + json.dumps({"programme_sha256": programme.sha256, "item_id": item["id"], "strategy": strategy}, sort_keys=True))
