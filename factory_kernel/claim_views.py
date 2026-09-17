"""Offline claim views for the CLI (WP04, shadow): `explain-claims` and `plan-obligations`.

Both read retained records (the Front Door intent store, an optional programme candidate, an
optional retained companion index) and caller-supplied trusted observations, compile the claim
identities and the graph or the allowed-action proposals in pure code, and write one bounded
JSON record. They contact nothing: no GitHub, no provider, no clock beyond the record they
write, no intent-store writer. `plan-obligations` records how the shadow compiler's proposals
relate to a recorded `plan-dispatch` decision; the record authorises nothing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_bytes, sha256_bytes
from .claims import (
    ClaimSet,
    RequirementClaimSet,
    bind_programme_claims,
    claim_status,
    compile_exploratory_claims,
    compile_requirement_claims,
)
from .claim_scheduler import compare_legacy_frontier, explain_actions
from .exploration_records import projection as exploration_projection
from .frontdoor_intent import IntentRefused, IntentStore, Principal
from .programme import ProgrammeRefused, compile_programme, parse_json
from .project_graph import project_graph
from .project_profile import current_profile
from .spine import load_policy

ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT = 2_000_000


class ClaimViewRefused(ValueError):
    pass


def _read_json(path: Path | None, *, what: str) -> Any:
    if path is None:
        return None
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ClaimViewRefused(f"cannot read {what}: {exc}") from exc
    if len(raw) > MAX_INPUT:
        raise ClaimViewRefused(f"{what} exceeds the input bound")
    try:
        return parse_json(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ClaimViewRefused(f"{what} is not valid JSON: {exc}") from exc


def _events(state_dir: Path, owner: str, project: str, repository: str) -> list:
    store = IntentStore(Path(state_dir), repository=repository, owner=owner)
    principal = Principal(identity=owner, role="owner")
    store._authorize(principal)
    with store._locked(project) as path:
        events = store._read(path)
    # The history's owner-role events must name the configured owner: a projection built for
    # another identity would present someone else's decisions as this owner's (decision_history rule).
    for event in events:
        actor = event.get("actor") if isinstance(event, Mapping) else None
        if not isinstance(actor, Mapping) or (actor.get("role") == "owner" and actor.get("identity") != owner):
            raise IntentRefused("intent history does not name the configured owner")
    return events


def _programme(path: Path | None, repository: str):
    raw = _read_json(path, what="programme candidate")
    if raw is None:
        return None
    try:
        return compile_programme(raw, repository=repository)
    except ProgrammeRefused as exc:
        raise ClaimViewRefused(f"programme candidate refused: {exc}") from exc


def _proof(path: Path | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    index = _read_json(Path(path) / "spine" / "attestations" / "index.json", what="companion index")
    if not isinstance(index, dict) or index.get("schema") != "dark-factory/attestation-index":
        raise ClaimViewRefused("companion index is not an attestation index")
    return index


def compile_project_claims(events: list, programme, *, project: str, policy) -> tuple[RequirementClaimSet | None, ClaimSet | None, Any, dict]:
    """Requirement identities from the latest approval, exploratory claims from the recorded
    hypotheses, programme binding only when a programme candidate is supplied."""
    snapshot = IntentStore._snapshot(events)
    approvals = snapshot["approvals"]
    if not approvals:
        return None, None, None, {"gaps": ["no-approved-specification"], "spec_sha256": None}
    approval = approvals[-1]
    profile = current_profile()
    requirements = compile_requirement_claims(
        approval["spec"], repository_id=str(profile.repository_id), project=project,
        approval={"actor": approval["actor"], "spec": approval["spec"], "spec_sha256": approval["spec_sha256"]})
    exploration = exploration_projection(events)
    hypotheses = {claim_id: {**claim, "id": claim_id} for claim_id, claim in exploration["claims"].items()
                  if claim.get("spec_sha256") == approval["spec_sha256"]}
    exploratory = compile_exploratory_claims(requirements, hypotheses) if hypotheses else None
    claim_set = None
    gaps = []
    if programme is not None:
        claim_set = bind_programme_claims(requirements, programme, programme.strategy, policy)
    else:
        gaps.append("no-programme-candidate")
    return requirements, claim_set, exploratory, {"gaps": gaps, "spec_sha256": approval["spec_sha256"],
                                                  "approved_at": approval["approved_at"], "approval_version": approval["project_version"]}


def _write(path: Path, record: Mapping[str, Any]) -> str:
    raw = canonical_bytes(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return sha256_bytes(raw)


def explain_claims(args: argparse.Namespace, cfg: Any) -> int:
    policy = load_policy(ROOT / ".factory" / "evidence-spine.json")
    try:
        events = _events(args.state_dir, args.owner, args.project, cfg.repository)
        programme = _programme(args.programme, cfg.repository)
        proof = _proof(args.proof)
        observations = _read_json(args.observations, what="observations") or {}
        if not isinstance(observations, dict):
            raise ClaimViewRefused("observations must be a JSON object")
        requirements, claim_set, exploratory, meta = compile_project_claims(events, programme, project=args.project, policy=policy)
    except (IntentRefused, ClaimViewRefused, ValueError) as exc:
        print(f"FACTORY_CLAIMS_REFUSED reason={str(exc)[:300]!r}", flush=True)
        return 1
    statuses = claim_status(claim_set, approved=True, exploratory=exploratory) if claim_set is not None else {}
    graph = project_graph(events, claim_set, proof, {**observations, "claim_status": statuses})
    record = {
        "schema": "dark-factory/claim-explanation", "schema_version": "1.0", "authority": "projection-only",
        "repository": cfg.repository, "project": args.project, "project_version": len(events),
        "spec_sha256": meta["spec_sha256"], "gaps": meta["gaps"],
        "requirements": [claim.to_dict() for claim in requirements.claims] if requirements else [],
        "claim_set_digest": claim_set.digest() if claim_set else None,
        "claim_status": statuses,
        "graph": graph,
    }
    digest = _write(args.output, record)
    print(f"FACTORY_CLAIMS project={args.project} version={len(events)} requirements={len(record['requirements'])} "
          f"claims={len(claim_set.claims) if claim_set else 0} nodes={len(graph['nodes'])} edges={len(graph['edges'])} "
          f"gaps={','.join(meta['gaps']) or '-'} sha256={digest}", flush=True)
    return 0


def plan_obligations(args: argparse.Namespace, cfg: Any) -> int:
    policy = load_policy(ROOT / ".factory" / "evidence-spine.json")
    try:
        events = _events(args.state_dir, args.owner, args.project, cfg.repository)
        programme = _programme(args.programme, cfg.repository)
        proof_index = _proof(args.proof)
        observations = _read_json(args.observations, what="observations") or {}
        plan = _read_json(args.dispatch_plan, what="dispatch plan")
        if not isinstance(observations, dict):
            raise ClaimViewRefused("observations must be a JSON object")
        if not isinstance(plan, dict) or plan.get("schema") != "dark-factory/dispatch-plan":
            raise ClaimViewRefused("dispatch plan is not a plan-dispatch record")
        _, claim_set, _, meta = compile_project_claims(events, programme, project=args.project, policy=policy)
    except (IntentRefused, ClaimViewRefused, ValueError) as exc:
        print(f"FACTORY_OBLIGATIONS_REFUSED reason={str(exc)[:300]!r}", flush=True)
        return 1
    verified_proof = verified_proof_from_index(proof_index, policy) if proof_index else {}
    # The recorded plan's observations are the trusted control values; the caller's observations
    # add what the planner does not see (programme item bindings, heads, leases).
    recorded = plan.get("observations") if isinstance(plan.get("observations"), dict) else {}
    inputs = plan.get("inputs") if isinstance(plan.get("inputs"), dict) else {}
    merged = {
        "control_observed": not (plan.get("status") == "blocked" and "control_unobserved" in plan.get("reason_codes", [])),
        "stopped": plan.get("status") == "blocked" and "stopped" in plan.get("reason_codes", []),
        "fenced": plan.get("status") == "blocked" and "fenced" in plan.get("reason_codes", []),
        "reconciliation_required": plan.get("status") == "blocked" and "reconciliation_required" in plan.get("reason_codes", []),
        "budget": {"available": True, "unavailable": False}.get(recorded.get("budget")),
        "review": recorded.get("review", []), "rehead": recorded.get("rehead", []), "build": recorded.get("build", []),
        "resume": recorded.get("resume"), "programme_ready": recorded.get("programme_ready") is True,
        "continuation": recorded.get("continuation") or "",
        **{key: observations[key] for key in ("heads", "issue_items", "items", "leases", "exact_head_checked") if key in observations},
    }
    explanation = explain_actions(claim_set, policy, verified_proof, programme, merged)
    comparison = compare_legacy_frontier(explanation, plan)
    record = {
        "schema": "dark-factory/obligation-plan", "schema_version": "1.0", "authority": "shadow-record",
        "repository": cfg.repository, "project": args.project, "project_version": len(events),
        "spec_sha256": meta["spec_sha256"], "gaps": meta["gaps"], "plan_inputs": inputs,
        "proposals": [p.to_dict() for p in explanation["proposals"]],
        "blocked": [b.to_dict() for b in explanation["blocked"]],
        "comparison": comparison,
    }
    digest = _write(args.output, record)
    print(f"FACTORY_OBLIGATIONS project={args.project} proposals={len(record['proposals'])} blocked={len(record['blocked'])} "
          f"classification={comparison['classification']} legacy={comparison['legacy']['action'] or comparison['legacy']['status']} "
          f"sha256={digest}", flush=True)
    return 0


def verified_proof_from_index(index: Mapping[str, Any], policy) -> dict:
    """Proof keyed by exact head from a retained companion index: only verified attestations
    count, with their shadow currency, one per spine claim."""
    head = str(index.get("head_sha") or "")
    verification = index.get("verification") if isinstance(index.get("verification"), Mapping) else {}
    shadow = index.get("shadow_currency") if isinstance(index.get("shadow_currency"), Mapping) else {}
    rows: dict[str, dict] = {}
    for record in index.get("attestations", []):
        if not isinstance(record, Mapping):
            continue
        aid = record.get("attestation_id")
        subject = record.get("subject") if isinstance(record.get("subject"), Mapping) else {}
        if subject.get("candidate_commit") != head or not (verification.get(aid) or {}).get("verified"):
            continue
        rows[str(record.get("claim_key"))] = {"status": (shadow.get(aid) or {}).get("status", "unobserved"), "attestation_id": aid}
    return {head: rows} if head and rows else {}
