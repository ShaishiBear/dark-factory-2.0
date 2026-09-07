#!/usr/bin/env python3
"""The mutation rung's clock, derived from what the rung was measured to cost.

`harness/ci.py` gave the mutation rung `timeout=900` as a bare literal. Nothing recorded
where 900 came from, and nothing noticed when the rung outgrew it: the catalogue went from
four application defects to nine, the factory trust-root catalogue from about a hundred to
several hundred, and the suites each defect re-runs roughly quadrupled. The first validation
run that reached the rung (PR #134, run 34066724127) printed `TIMEOUT after 900s` and
`GATE_FAILED: mutations` after passing every other gate including the browser journey.

A budget nobody measured is a deadline, not a bound. So the number lives in
`harness/mutations/budget.json` as **measurements plus a headroom factor**, and the budget is
DERIVED from them here:

    budget = ceil(p100(measurements) * headroom / 60) * 60

p100 and not the mean, because the rung has to finish on its worst observed run, not on its
average one. `tests/factory/test_factory_mutation_budget.py` pins the relation, so raising the
budget requires recording the measurement that justifies it, in the same reviewed change.

The second half of the answer is that drift stops being silent. Both mutation runners print
their total against this budget, and warn once the total crosses `warn_fraction` of it, so the
rung announces that it is running out of room on the runs that still pass -- instead of being
discovered as a timeout on the one run that mattered.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

RECORD = Path(__file__).resolve().parent / "mutations" / "budget.json"
REQUIRED_FIELDS = ("id", "date", "environment", "kind", "scope", "source", "total_seconds")
KINDS = ("measured", "projected")
# Two clocks, because two callers bound two different things: `harness/ci.py` bounds the whole
# rung (the application family plus the factory family nested inside it), while the spine's
# `observe_factory_authority` and the nested call bound the factory family alone.
SCOPES = ("mutation-rung", "factory-family")
# A budget with less than half again the worst observation is not a budget; it is the
# observation with a rounding error. FACTORY_RULES section 4 states the same floor in prose.
MINIMUM_HEADROOM = 1.5


def load(path: Path | None = None) -> dict[str, Any]:
    """Read the measurement record. Raises rather than defaulting: a missing or malformed
    record must stop the gate, not silently restore the unmeasured literal."""
    record = json.loads((path or RECORD).read_text(encoding="utf-8"))
    validate(record)
    return record


def validate(record: dict[str, Any]) -> None:
    measurements = record.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise ValueError("the mutation budget record holds no measurements")
    for entry in measurements:
        missing = [field for field in REQUIRED_FIELDS if not entry.get(field)]
        if missing:
            raise ValueError(
                f"measurement {entry.get('id', '?')!r} is missing {', '.join(missing)}; "
                "every recorded number names where and how it was obtained"
            )
        if entry["kind"] not in KINDS:
            raise ValueError(f"measurement {entry['id']!r} has an unknown kind {entry['kind']!r}")
        if entry["scope"] not in SCOPES:
            raise ValueError(f"measurement {entry['id']!r} has an unknown scope {entry['scope']!r}")
        if float(entry["total_seconds"]) <= 0:
            raise ValueError(f"measurement {entry['id']!r} records a non-positive total")
    for scope in SCOPES:
        if not [entry for entry in measurements if entry["scope"] == scope]:
            raise ValueError(f"no measurement records the {scope!r} scope; every derived budget "
                             "must have an observation behind it")
    headroom = float(record.get("headroom", 0))
    if headroom < MINIMUM_HEADROOM:
        raise ValueError(
            f"headroom {headroom} is below the {MINIMUM_HEADROOM} floor; the budget must "
            "leave at least half again the worst observed run"
        )
    warn_fraction = float(record.get("warn_fraction", 0))
    if not 0.0 < warn_fraction < 1.0:
        raise ValueError("warn_fraction must be a fraction of the budget, strictly between 0 and 1")


def measurements(record: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    if scope not in SCOPES:
        raise ValueError(f"unknown budget scope {scope!r}")
    return [entry for entry in record["measurements"] if entry["scope"] == scope]


def measured_seconds(record: dict[str, Any], scope: str) -> float:
    """p100 of the recorded totals for a scope: the worst run, not the typical one."""
    return max(float(entry["total_seconds"]) for entry in measurements(record, scope))


def budget_seconds(record: dict[str, Any], scope: str) -> int:
    """The derived budget: the worst observation plus headroom, rounded up to a whole minute."""
    generous = measured_seconds(record, scope) * float(record["headroom"])
    return int(math.ceil(generous / 60.0) * 60)


def warn_seconds(record: dict[str, Any], scope: str) -> float:
    """The total at which a runner starts saying it is running out of room."""
    return budget_seconds(record, scope) * float(record["warn_fraction"])


def drift_warning(seconds: float, record: dict[str, Any], *, scope: str, marker: str) -> str | None:
    """The line a runner prints when its total crosses the warning threshold, else None.

    Returned rather than printed so the threshold is testable without capturing stdout.
    """
    budget = budget_seconds(record, scope)
    threshold = warn_seconds(record, scope)
    if seconds <= threshold:
        return None
    remaining = 100 - float(record["warn_fraction"]) * 100
    return (
        f"{marker} seconds={seconds:.1f} budget={budget} threshold={threshold:.1f} "
        f"scope={scope} - this run used more than "
        f"{float(record['warn_fraction']) * 100:.0f}% of the budget, so under {remaining:.0f}% "
        f"is left. Re-measure the rung and record the new number in "
        f"harness/mutations/budget.json before the drift arrives as a TIMEOUT on a run that "
        f"mattered."
    )


def summary(record: dict[str, Any]) -> str:
    parts = [
        f"{scope}={budget_seconds(record, scope)}s"
        f"(measured={measured_seconds(record, scope):.0f}s"
        f",n={len(measurements(record, scope))})"
        for scope in SCOPES
    ]
    return f"MUTATION_BUDGET headroom={float(record['headroom'])} " + " ".join(parts)


if __name__ == "__main__":  # a human asking what the gate will allow
    print(summary(load()))
