"""The project graph over HTTP (SPECIFICATION 10, C11, WP10A): bounded snapshot or delta, details.

`project_graph.project_graph` is the pure projection; this module is the read-only transport
around it: it reads the intent store under its lock and authorization, projects the graph,
names the snapshot by the sources it was projected from, answers a cursor with an exact delta
against the snapshot that cursor names or with an explicit resnapshot instruction, and serves
one node's details after authorization and before existence. A GET here creates no event,
runs no job and makes no paid call: the only inputs are the recorded events and whatever
external observations the caller already holds.

Two cursor namespaces stay distinct: `project_version` counts owner decision events;
`observation_cursor` is whatever the caller's observation source reported (None when nothing
was observed), so a platform change can update the view without fabricating a decision.
"""
from __future__ import annotations

from typing import Any, Mapping

from .canonical import sha256_value
from .claim_views import compile_project_claims
from .frontdoor_intent import IntentRefused, IntentStore, Principal
from .project_graph import SCHEMA_VERSION as GRAPH_VERSION, graph_delta, project_graph

SCHEMA = "dark-factory/project-graph-transport"
SCHEMA_VERSION = "1.0"
MAX_DELTA_SPAN = 500  # a cursor further behind than this gets a resnapshot, not a delta


class GraphRefused(ValueError):
    pass


def _events(store: IntentStore, project: str, *, principal: Principal) -> list:
    store._authorize(principal)
    if principal.role != "owner":
        raise IntentRefused("the project graph is owner-only")
    with store._locked(project) as path:
        return store._read(path)


def _project(events: list, *, project: str, observations: Mapping[str, Any] | None) -> dict:
    _requirements, claim_set, _exploratory, meta = compile_project_claims(events, None, project=project, policy=None)
    graph = project_graph(events, claim_set, None, observations)
    return {**graph, "claim_gaps": list(meta.get("gaps", []))}


def snapshot_id(events: list, *, project: str, principal: Principal, observation_cursor: Any) -> str:
    """Names a projection by its sources: the exact events, the observation cursor, the project,
    the principal's role (the privacy projection) and the projection version."""
    return sha256_value({"events": sha256_value(list(events)), "observation_cursor": observation_cursor, "project": project,
                         "role": principal.role, "graph_version": GRAPH_VERSION, "transport_version": SCHEMA_VERSION})


def graph_view(store: IntentStore, project: str, *, principal: Principal, after_version: int | None = None,
               observations: Mapping[str, Any] | None = None) -> dict:
    """The snapshot, or the delta since `after_version` against the projection of the events that
    existed at that version. A cursor ahead of the store, or further behind than MAX_DELTA_SPAN,
    is answered with `resnapshot` and nothing else."""
    events = _events(store, project, principal=principal)
    current = len(events)
    cursor = observations.get("cursor") if isinstance(observations, Mapping) else None
    graph = _project(events, project=project, observations=observations)
    identity = snapshot_id(events, project=project, principal=principal, observation_cursor=cursor)
    base = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "project": project, "project_version": current,
            "observation_cursor": cursor, "snapshot_id": identity, "source_freshness": {"events": "as-read-under-lock",
            "observations": "caller-supplied" if observations else "none"}, "authority": "projection-only"}
    if after_version is None:
        return {**base, "kind": "snapshot", "graph": graph}
    if type(after_version) is not int or after_version < 0:
        raise GraphRefused("after_version must be a non-negative integer")
    if after_version > current:
        return {**base, "kind": "resnapshot", "reason": "cursor-ahead-of-project", "after_version": after_version,
                "instruction": "discard the client snapshot and GET without a cursor"}
    if current - after_version > MAX_DELTA_SPAN:
        return {**base, "kind": "resnapshot", "reason": "cursor-gap-exceeds-bound", "after_version": after_version,
                "bound": MAX_DELTA_SPAN, "instruction": "discard the client snapshot and GET without a cursor"}
    previous_events = events[:after_version]
    previous = _project(previous_events, project=project, observations=observations)
    delta = graph_delta(previous, graph)
    return {**base, "kind": "delta", "after_version": after_version,
            "from_snapshot_id": snapshot_id(previous_events, project=project, principal=principal, observation_cursor=cursor),
            "delta": delta, "counts": graph["counts"], "truncated": graph["truncated"]}


def node_details(store: IntentStore, project: str, *, principal: Principal, node_id: Any,
                 observations: Mapping[str, Any] | None = None) -> dict:
    """One node with its edges and source references. Authorization is checked by `_events`
    before the id is even looked at; an unknown or malformed id is refused afterwards, so a
    caller learns nothing about the graph without the right to read it."""
    events = _events(store, project, principal=principal)
    if not isinstance(node_id, str) or not 1 <= len(node_id) <= 200:
        raise GraphRefused("node id must be an opaque string")
    graph = _project(events, project=project, observations=observations)
    node = next((n for n in graph["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise GraphRefused("unknown node")
    edges = [e for e in graph["edges"] if e["source"] == node_id or e["target"] == node_id]
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "project": project, "project_version": len(events),
            "snapshot_id": snapshot_id(events, project=project, principal=principal,
                                       observation_cursor=observations.get("cursor") if isinstance(observations, Mapping) else None),
            "node": node, "edges": edges, "source_refs": list(node.get("source_refs", [])), "authority": "projection-only"}


__all__ = ["GraphRefused", "MAX_DELTA_SPAN", "SCHEMA", "SCHEMA_VERSION", "graph_view", "node_details", "snapshot_id"]
