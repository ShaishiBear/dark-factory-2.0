"""Pure projection of the project as a typed graph (SPECIFICATION section 10, WP04 shadow).

Nodes: requirements, questions and assumptions, candidates and recommendations, owner and
delegated decisions, programme items, runs, implementation components, observations and proof
obligations with their attestations. Edges are typed; for `depends_on` the direction is
*source depends on target*; `implements`, `selected`, `produced`, `supported_by`, `certifies`,
`decided_by` and `observed_by` have the directions documented on RELATIONS. Every node and edge
carries the records it came from and a currentness with gap reasons.

Two labels stay separate on proof: `recorded_match` (the retained record names this exact
subject) and `trusted_currency` (what the shadow currency assessment says now). A green badge
is neither. Nothing here reads a network, a clock or a mutable file; the caller passes the
intent events, the compiled claims, the retained proof index and any external observations, and
the projection is bounded and deterministic so `graph_delta` between two snapshots is exact.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .canonical import sha256_value
from .exploration_records import OPERATION as EXPLORATION_OPERATION, projection as exploration_projection
from .frontdoor_intent import IntentStore

SCHEMA = "dark-factory/project-graph"
SCHEMA_VERSION = "1.0"
MAX_NODES = 2000
MAX_EDGES = 6000
RELATIONS: Mapping[str, str] = {
    "depends_on": "source depends on target",
    "supported_by": "source requirement is supported by target obligation",
    "refines": "source assumption refines target requirement",
    "implements": "source component implements target claim (proposed unless observed)",
    "selected": "source recommendation selected target candidate",
    "produced": "source run produced target observation or attestation",
    "certifies": "source attestation certifies target obligation",
    "decided_by": "source spec revision was decided by target owner decision",
    "observed_by": "source assumption was observed by target observation",
    "scheduled_as": "source programme item is scheduled as target obligation set",
}


def _node(nodes: dict, node_id: str, kind: str, label: str, *, source_refs: Sequence[str] = (),
          currentness: str = "unknown", gaps: Sequence[str] = (), **extra: Any) -> None:
    if node_id in nodes:
        return
    nodes[node_id] = {"id": node_id, "kind": kind, "label": str(label)[:200], "source_refs": sorted(set(source_refs)),
                      "currentness": currentness, "gaps": sorted(set(gaps)), **extra}


def _edge(edges: list, relation: str, source: str, target: str, *, source_refs: Sequence[str] = (),
          currentness: str = "unknown", gaps: Sequence[str] = ()) -> None:
    if relation not in RELATIONS:
        raise ValueError(f"unregistered relation {relation!r}")
    edges.append({"relation": relation, "source": source, "target": target, "source_refs": sorted(set(source_refs)),
                  "currentness": currentness, "gaps": sorted(set(gaps))})


def project_graph(events: Sequence[Mapping[str, Any]], claims: Any, proof: Mapping[str, Any] | None,
                  observations: Mapping[str, Any] | None, *, max_nodes: int = MAX_NODES, max_edges: int = MAX_EDGES) -> dict:
    """The bounded graph. `events` are the intent-store events as read; `claims` is a
    `claims.ClaimSet` (or None before a programme exists) optionally with `statuses` from
    `claims.claim_status` under `observations["claim_status"]`; `proof` is a retained companion
    index (`spine/attestations/index.json`); `observations` may carry `cursor`, `claim_status`,
    `items` (programme item -> {issue, pr, head}), `runs` and `components`."""
    observations = dict(observations or {})
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    snapshot = IntentStore._snapshot(list(events))
    exploration = exploration_projection(list(events))
    statuses = observations.get("claim_status") if isinstance(observations.get("claim_status"), Mapping) else {}

    # Owner decisions and the specification revisions they decided.
    approvals = snapshot["approvals"]
    for approval in approvals:
        decision_id = f"decision:v{approval['project_version']}"
        _node(nodes, decision_id, "owner-decision", f"approve-spec revision {approval['spec']['revision']}",
              source_refs=[f"intent:{approval['project_version']}"], currentness="recorded",
              actor=dict(approval["actor"]))
        spec_id = f"spec:{approval['spec_sha256']}"
        _node(nodes, spec_id, "specification", f"{approval['spec']['id']} v{approval['spec']['revision']}",
              source_refs=[f"intent:{approval['project_version']}"], currentness="recorded")
        _edge(edges, "decided_by", spec_id, decision_id, source_refs=[f"intent:{approval['project_version']}"], currentness="recorded")
    draft = snapshot.get("draft")
    if draft is not None:
        _node(nodes, f"proposal:v{draft['draft_version']}", "proposal", f"spec proposal revision {draft['spec']['revision']}",
              source_refs=[f"intent:{draft['draft_version']}"], currentness="unapproved",
              gaps=["awaiting-owner-decision"] if not any(a.get("draft_version") == draft["draft_version"] for a in approvals) else [],
              open_questions=len(draft.get("open_questions") or []))

    # Requirements, obligations and their typed edges from the compiled claims.
    if claims is not None:
        for claim in claims.claims:
            status = statuses.get(claim.key, {})
            kind = {"requirement": "requirement", "proof-obligation": "proof-obligation", "assumption": "assumption",
                    "implementation-property": "implementation-claim"}[claim.kind]
            gaps = list(status.get("reason_codes", []))
            currentness = status.get("currency", "unobserved")
            _node(nodes, f"claim:{claim.key}", kind, claim.statement, source_refs=[claim.source_ref], currentness=currentness,
                  gaps=gaps, intent_status=status.get("intent_status"), proof_status=status.get("proof_status"),
                  authority_profile=claim.authority_profile, acceptance_ids=list(claim.acceptance_ids))
        for edge in claims.edges:
            _edge(edges, edge.relation, f"claim:{edge.source}", f"claim:{edge.target}", source_refs=[f"claims:{claims.digest()[:12]}"],
                  currentness="derived")
        items = observations.get("items") if isinstance(observations.get("items"), Mapping) else {}
        for item_id, obligations in sorted(claims.item_obligations.items()):
            item = items.get(item_id) if isinstance(items.get(item_id), Mapping) else {}
            item_node = f"item:{item_id}"
            _node(nodes, item_node, "programme-item", item_id, source_refs=[f"programme:{claims.programme_sha256}"],
                  currentness="recorded" if item else "unlinked", gaps=[] if item else ["no-issue-observed"],
                  issue=item.get("issue"), pr=item.get("pr"))
            for key in obligations:
                _edge(edges, "scheduled_as", item_node, f"claim:{key}", source_refs=[f"programme:{claims.programme_sha256}"], currentness="derived")

    # Exploration: assumptions, questions, candidates, recommendations, observations.
    for claim_id, claim in sorted(exploration["claims"].items()):
        node_id = f"exploration:{claim_id}"
        _node(nodes, node_id, "assumption" if claim.get("kind", "assumption") == "assumption" else "question",
              claim.get("statement", claim_id), source_refs=[f"exploration:{claim_id}"],
              currentness={"active": "unobserved", "supported": "supported", "invalidated": "invalidated"}.get(claim.get("status"), "unknown"),
              gaps=[] if claim.get("observations") else ["no-observation"])
        for dep in claim.get("depends_on", []):
            _edge(edges, "depends_on", node_id, f"exploration:{dep}", source_refs=[f"exploration:{claim_id}"], currentness="recorded")
        for index, observation in enumerate(claim.get("observations", [])):
            obs_id = f"observation:{claim_id}:{index}"
            _node(nodes, obs_id, "observation", str(observation.get("outcome") or observation.get("status") or "observation"),
                  source_refs=[f"exploration:{claim_id}"], currentness="recorded",
                  evidence_class=observation.get("evidence_class"))
            _edge(edges, "observed_by", node_id, obs_id, source_refs=[f"exploration:{claim_id}"], currentness="recorded")
    for session_id, session in sorted(exploration["sessions"].items()):
        for candidate_id, candidate in sorted(session.get("candidates", {}).items()):
            cand_id = f"candidate:{session_id}:{candidate_id}"
            _node(nodes, cand_id, "candidate", candidate.get("title") or candidate_id, source_refs=[f"exploration-session:{session_id}"],
                  currentness="predicted", gaps=["prediction-not-observed"])
            for claim_id in candidate.get("claim_ids", []) if isinstance(candidate.get("claim_ids"), list) else []:
                if f"exploration:{claim_id}" in nodes:
                    _edge(edges, "depends_on", cand_id, f"exploration:{claim_id}", source_refs=[f"exploration-session:{session_id}"], currentness="recorded")
        for index, recommendation in enumerate(session.get("recommendations", [])):
            rec_id = f"recommendation:{session_id}:{index}"
            stale = session.get("status") in {"reconsideration-required", "challenged"}
            _node(nodes, rec_id, "recommendation", str(recommendation.get("summary") or f"recommendation {index}"),
                  source_refs=[f"exploration-session:{session_id}"], currentness="reconsider" if stale else "recorded",
                  gaps=["assumption-invalidated"] if stale else [])
            selected = recommendation.get("candidate_id") or recommendation.get("selected")
            if selected and f"candidate:{session_id}:{selected}" in nodes:
                _edge(edges, "selected", rec_id, f"candidate:{session_id}:{selected}", source_refs=[f"exploration-session:{session_id}"], currentness="recorded")

    # Proof companions: attestations certify the obligations whose authority profile they carry,
    # for the exact subject the caller observed for that programme item's PR.
    items = observations.get("items") if isinstance(observations.get("items"), Mapping) else {}
    heads = {str(item.get("head")): item_id for item_id, item in items.items() if isinstance(item, Mapping) and item.get("head")}
    if isinstance(proof, Mapping) and isinstance(proof.get("attestations"), list):
        verification = proof.get("verification") if isinstance(proof.get("verification"), Mapping) else {}
        shadow = proof.get("shadow_currency") if isinstance(proof.get("shadow_currency"), Mapping) else {}
        run_id = str(proof.get("run_id") or "run")
        _node(nodes, f"run:{run_id}", "run", run_id, source_refs=[f"attestations:{run_id}"], currentness="recorded",
              head=proof.get("head_sha"), base=proof.get("base_sha"))
        for record in proof["attestations"]:
            if not isinstance(record, Mapping) or not isinstance(record.get("attestation_id"), str):
                continue
            aid = record["attestation_id"]
            verified = bool((verification.get(aid) or {}).get("verified"))
            currency = (shadow.get(aid) or {}).get("status", "unobserved")
            subject = record.get("subject") if isinstance(record.get("subject"), Mapping) else {}
            item_id = heads.get(str(subject.get("candidate_commit")))
            _node(nodes, f"attestation:{aid}", "attestation", f"{record.get('claim_key')} attestation",
                  source_refs=[f"attestations:{run_id}#{aid[:12]}"],
                  currentness=currency if verified else "unverified",
                  gaps=([] if verified else ["verification-refused"]) + ([] if item_id else ["subject-unlinked"]),
                  recorded_match=item_id is not None, trusted_currency=currency, verified=verified,
                  claim_key=record.get("claim_key"))
            _edge(edges, "produced", f"run:{run_id}", f"attestation:{aid}", source_refs=[f"attestations:{run_id}"], currentness="recorded")
            if claims is not None and item_id is not None:
                for key in claims.item_obligations.get(item_id, ()):
                    claim = claims.by_key()[key]
                    if claim.authority_profile == record.get("claim_key"):
                        _edge(edges, "certifies", f"attestation:{aid}", f"claim:{key}",
                              source_refs=[f"attestations:{run_id}#{aid[:12]}"],
                              currentness=currency if verified else "unverified",
                              gaps=[] if verified and currency == "current" else ["not-current-proof"])

    # Implementation components (from bindings the caller observed): proposed until observed.
    components = observations.get("components") if isinstance(observations.get("components"), list) else []
    for row in components:
        if not isinstance(row, Mapping):
            continue
        subject = str(row.get("subject_identity") or "")
        if not subject:
            continue
        comp_id = f"component:{subject[:24]}"
        standing = str(row.get("standing") or "proposed")
        _node(nodes, comp_id, "implementation-component", subject, source_refs=[f"binding:{row.get('source')}"],
              currentness="observed" if standing == "observed" else "proposed",
              gaps=[] if standing == "observed" else ["model-proposed-not-observed"])
        target = f"claim:{row.get('claim_key')}"
        if target in nodes:
            _edge(edges, "implements", comp_id, target, source_refs=[f"binding:{row.get('source')}"],
                  currentness="observed" if standing == "observed" else "proposed")

    node_ids = set(nodes)
    edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]
    ordered_nodes = sorted(nodes.values(), key=lambda n: (n["kind"], n["id"]))
    ordered_edges = sorted(edges, key=lambda e: (e["relation"], e["source"], e["target"]))
    truncated = len(ordered_nodes) > max_nodes or len(ordered_edges) > max_edges
    ordered_nodes = ordered_nodes[:max_nodes]
    kept = {n["id"] for n in ordered_nodes}
    ordered_edges = [e for e in ordered_edges if e["source"] in kept and e["target"] in kept][:max_edges]
    counts: dict[str, int] = {}
    for node in ordered_nodes:
        counts[node["kind"]] = counts.get(node["kind"], 0) + 1
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "authority": "projection-only",
        "project_version": snapshot["project_version"], "observation_cursor": observations.get("cursor"),
        "relations": dict(RELATIONS), "nodes": ordered_nodes, "edges": ordered_edges, "counts": counts,
        "truncated": truncated, "bounds": {"max_nodes": max_nodes, "max_edges": max_edges},
    }


def graph_delta(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict:
    """Exact difference between two projections: node ids added, removed and changed (any field),
    edges added and removed. A cursor gap is the caller's concern; this compares two snapshots."""
    old_nodes = {n["id"]: n for n in old.get("nodes", [])}
    new_nodes = {n["id"]: n for n in new.get("nodes", [])}
    old_edges = {(e["relation"], e["source"], e["target"]): e for e in old.get("edges", [])}
    new_edges = {(e["relation"], e["source"], e["target"]): e for e in new.get("edges", [])}
    changed = sorted(node_id for node_id in old_nodes.keys() & new_nodes.keys()
                     if sha256_value(old_nodes[node_id]) != sha256_value(new_nodes[node_id]))
    return {
        "schema": "dark-factory/project-graph-delta", "schema_version": "1.0",
        "from_version": old.get("project_version"), "to_version": new.get("project_version"),
        "added_nodes": sorted(new_nodes.keys() - old_nodes.keys()), "removed_nodes": sorted(old_nodes.keys() - new_nodes.keys()),
        "changed_nodes": changed,
        "added_edges": [list(key) for key in sorted(new_edges.keys() - old_edges.keys())],
        "removed_edges": [list(key) for key in sorted(old_edges.keys() - new_edges.keys())],
        "changed_edges": [list(key) for key in sorted(k for k in old_edges.keys() & new_edges.keys()
                                                       if sha256_value(old_edges[k]) != sha256_value(new_edges[k]))],
    }


__all__ = ["MAX_EDGES", "MAX_NODES", "RELATIONS", "graph_delta", "project_graph"]
