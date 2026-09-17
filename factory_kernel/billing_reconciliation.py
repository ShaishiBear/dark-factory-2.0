"""Provider billing reconciliation (SPECIFICATION 6.2, contract C06, WP02).

Compares invocation records (what the meter reserved, started and observed) with receipts
retrieved independently from a provider. Each invocation ends in one of three states:
`no_receipt` (nothing retrieved yet), `complete` (one receipt within the ceiling that agrees with
the telemetry when there is any) or `conflicting` (a receipt above the ceiling, disagreeing
with known telemetry, duplicated, or a receipt for an invocation the meter never started).
Missing or ambiguous bills stay unresolved; nothing here settles them.

Every provider adapter is disabled and fails closed until its actual interface has been
verified and enabled by a protected configuration change: no invented billing API, no price
table. The comparison is pure so it can run over retained records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

STATES = ("no_receipt", "complete", "conflicting")
ADAPTERS: Mapping[str, Mapping[str, Any]] = {
    "openrouter": {"enabled": False, "interface_verified": False,
                   "note": "generation receipts endpoint unverified; enable only after a recorded interface check"},
}


class ReconciliationRefused(ValueError):
    pass


@dataclass(frozen=True)
class Receipt:
    provider_request_id: str
    billed_microusd: int
    source: str

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "Receipt":
        pid = row.get("provider_request_id")
        billed = row.get("billed_microusd")
        if not isinstance(pid, str) or not pid or not isinstance(billed, int) or isinstance(billed, bool) or billed < 0:
            raise ReconciliationRefused("receipt needs a provider request id and a non-negative billed amount")
        return cls(pid, billed, str(row.get("source") or "unknown"))


def fetch_receipts(adapter_id: str, *_: Any, **__: Any) -> tuple[Receipt, ...]:
    """Disabled by default. Raises until the adapter is enabled with a verified interface."""
    adapter = ADAPTERS.get(adapter_id)
    if adapter is None:
        raise ReconciliationRefused(f"unknown billing adapter {adapter_id!r}")
    if not adapter["enabled"] or not adapter["interface_verified"]:
        raise ReconciliationRefused(f"billing adapter {adapter_id!r} is disabled: provider interface unverified")
    raise ReconciliationRefused(f"billing adapter {adapter_id!r} has no transport implementation")


def reconcile(invocations: Iterable[Mapping[str, Any]], receipts: Iterable[Mapping[str, Any] | Receipt]) -> dict:
    """Pure comparison. `invocations` rows: call_id, state (reserved/started/observed/not_started),
    ceiling_microusd, reported_microusd (None for unknown), provider_request_id (None if none)."""
    parsed = [row if isinstance(row, Receipt) else Receipt.from_mapping(row) for row in receipts]
    by_pid: dict[str, list[Receipt]] = {}
    for receipt in parsed:
        by_pid.setdefault(receipt.provider_request_id, []).append(receipt)
    rows = []
    counts = {state: 0 for state in STATES}
    seen_pids: set[str] = set()
    for invocation in invocations:
        call_id = str(invocation.get("call_id") or "")
        state = str(invocation.get("state") or "")
        ceiling = invocation.get("ceiling_microusd")
        reported = invocation.get("reported_microusd")
        pid = invocation.get("provider_request_id")
        matched = by_pid.get(pid, []) if isinstance(pid, str) else []
        if isinstance(pid, str):
            seen_pids.add(pid)
        if not matched:
            verdict, reason = "no_receipt", ("never-started" if state in {"reserved", "not_started"} else "no receipt retrieved")
        elif len(matched) > 1:
            verdict, reason = "conflicting", "duplicate receipts for one request id"
        elif state in {"reserved", "not_started"}:
            verdict, reason = "conflicting", "receipt for a call the meter never started"
        elif not isinstance(ceiling, int) or matched[0].billed_microusd > ceiling:
            verdict, reason = "conflicting", "billed above the reserved ceiling"
        elif reported is not None and matched[0].billed_microusd != reported:
            verdict, reason = "conflicting", "billed amount differs from reported telemetry"
        else:
            verdict, reason = "complete", "receipt within ceiling"
        counts[verdict] += 1
        rows.append({"call_id": call_id, "state": state, "verdict": verdict, "reason": reason,
                     "billed_microusd": matched[0].billed_microusd if len(matched) == 1 else None,
                     "provider_request_id": pid})
    orphans = sorted(pid for pid in by_pid if pid not in seen_pids)
    return {"schema": "dark-factory/billing-reconciliation", "schema_version": "1.0", "authority": "observation-only",
            "rows": rows, "counts": counts, "orphan_receipts": orphans,
            "resolved": counts["no_receipt"] == 0 and counts["conflicting"] == 0 and not orphans}


__all__ = ["ADAPTERS", "Receipt", "ReconciliationRefused", "STATES", "fetch_receipts", "reconcile"]
