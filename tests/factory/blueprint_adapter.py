"""Thin adapter from the blueprint's public conformance vectors to the real implementation.

Run with:

    python docs/implementation-blueprint-2026-09-17/conformance/run.py --adapter tests/factory/blueprint_adapter.py
    python docs/implementation-blueprint-2026-09-17/conformance/run.py --adapter tests/factory/blueprint_adapter.py --operation plan_dispatch

Each `evaluate` builds real repository records from the fixture payload, calls the production
method, and projects its public result into the small output shape `cases.json` specifies.
It reads no case IDs, no expected results and no `cases.json`; it switches on nothing but the
operation name; it returns no canned verdict. An operation whose implementation has not
landed raises `NotImplementedError`, which the runner reports as a failure for that case, so a
partial adapter can never look like full conformance. Passing these public vectors is
necessary for the named interfaces and sufficient for nothing else: repository tests, causal
mutations and independent qualification remain the gates (conformance/README.md).
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _plan_dispatch(payload: dict) -> dict:
    """`plan_dispatch` -> `factory_kernel.dispatch_plan.select_dispatch` (R00 / C04).

    The fixture's lists are already-admitted work: review PR numbers select by the existing
    oldest-number rule; re-head and build lists are in their existing source priority order.
    Records are built with that ordering metadata (identical labels, an `updatedAt` that
    preserves list position) so the production key sees exactly the ordering the fixture
    describes, and the production selector decides everything else.
    """
    from factory_kernel.dispatch_plan import DispatchObservation, select_dispatch

    def rows(numbers):
        return tuple({"number": int(n), "updatedAt": f"{index:08d}", "labels": []} for index, n in enumerate(numbers))

    observation = DispatchObservation(
        control_observed=bool(payload["control_observed"]),
        stopped=bool(payload["stopped"]),
        fenced=bool(payload["fenced"]),
        reconciliation_required=bool(payload["reconciliation"]),
        review=tuple({"number": int(n), "updatedAt": "", "labels": []} for n in payload["review"]),
        rehead=tuple(int(n) for n in payload["rehead"]),
        build=rows(payload["build"]),
        budget=bool(payload["budget"]),
    )
    plan = select_dispatch(observation)
    return {"status": plan.status, "reason_codes": list(plan.reason_codes), "action": plan.action,
            "subject": plan.subject, "mutation_count": plan.mutation_count}


def _event_replay(payload: dict) -> dict:
    """`event_replay` -> `factory_kernel.project_events.ProjectEvents.append` (R01 / C02).

    A real IntentStore in the scratch directory is seeded to `current_version` events; when the
    fixture says the key exists, the first event carries `old_command` by `old_actor`. Then the
    production primitive is asked to append `new_command` by `new_actor` with the key at
    `expected_version`, and its outcome is projected: replayed / appended / refused with the
    primitive's own reason, plus how many events were actually appended by that call.
    """
    from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
    from factory_kernel.project_events import OperationSpec, ProjectEvents, exact_replay

    def principal(role):
        return Principal("maintainer" if role == "owner" else role, role)

    store = IntentStore(Path.cwd() / "journal", repository="owner/product", owner="maintainer")
    events = ProjectEvents(store, {"note": OperationSpec("note", frozenset({"owner", "service"}), exact_replay)})
    key = "fixture-key"
    seeded = 0
    if payload["key_exists"]:
        events.append(project="p", principal=principal(payload["old_actor"]), expected_version=0, idempotency_key=key,
                      operation="note", payload=payload["old_command"], validate_transition=lambda e: payload["old_command"])
        seeded += 1
    while seeded < int(payload["current_version"]):
        events.append(project="p", principal=principal("owner"), expected_version=seeded, idempotency_key=f"filler-{seeded}",
                      operation="note", payload={"filler": seeded}, validate_transition=lambda e, n=seeded: {"filler": n})
        seeded += 1
    before = len(events.read("p", principal("owner")))
    try:
        result = events.append(project="p", principal=principal(payload["new_actor"]), expected_version=int(payload["expected_version"]),
                               idempotency_key=key, operation="note", payload=payload["new_command"],
                               validate_transition=lambda e: payload["new_command"])
    except IntentRefused as exc:
        text = str(exc)
        code = "idempotency_conflict" if "idempotency_conflict" in text else "stale_version" if "stale project version" in text else "refused"
        return {"status": "refused", "reason_codes": [code], "append_count": len(events.read("p", principal("owner"))) - before}
    after = len(events.read("p", principal("owner")))
    return {"status": "replayed" if result.replayed else "appended", "reason_codes": [], "append_count": after - before}


def _source_span(payload: dict) -> dict:
    """`source_span` -> `factory_kernel.code_subjects.resolve_span` (R02 / C03).

    The fixture's `text` is the exact source as text; its UTF-8 encoding is the exact blob.
    That blob is indexed as a one-file tree through the real memory tree reader, and the
    production resolver is asked for the requested half-open byte span with the independently
    supplied expected hash. The resolved text is returned as the resolver decoded it.
    """
    from factory_kernel.code_subjects import MemoryTreeReader, Refusal, index_source, resolve_span

    raw = payload["text"].encode("utf-8")
    path = payload["path"]
    files = {path: raw} if isinstance(path, str) and "/" in path and ".." not in path else {"src/a.py": raw}
    index = index_source(MemoryTreeReader(files, repository_id="conformance"))
    result = resolve_span(index, {"path": path, "byte_start": payload["start"], "byte_end": payload["end"],
                                  "source_bytes_sha256": payload["expected_sha256"]})
    if isinstance(result, Refusal):
        return {"status": "refused", "reason_codes": sorted(result.reason_codes)}
    return {"status": "resolved", "reason_codes": [], "text": result.text}


OPERATIONS = {
    "plan_dispatch": _plan_dispatch,
    "event_replay": _event_replay,
    "source_span": _source_span,
}


def evaluate(operation: str, payload: dict, scratch_dir: str) -> dict:
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise NotImplementedError(f"{operation}: no real implementation has landed for this operation yet")
    return handler(payload)
