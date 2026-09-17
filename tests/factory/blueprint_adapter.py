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


OPERATIONS = {
    "plan_dispatch": _plan_dispatch,
}


def evaluate(operation: str, payload: dict, scratch_dir: str) -> dict:
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise NotImplementedError(f"{operation}: no real implementation has landed for this operation yet")
    return handler(payload)
