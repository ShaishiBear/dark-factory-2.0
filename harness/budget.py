#!/usr/bin/env python3
"""Every clock in the ladder, derived from what the thing it bounds was measured to cost.

THE DEFECT THIS FILE EXISTS TO END, three instances in two days:

  1. `harness/ci.py` gave the mutation rung `timeout=900`. Nothing recorded where 900 came
     from, the catalogue grew underneath it, and the first validation run that reached the
     rung died on `TIMEOUT after 900s` with every other gate green (PR #134, run
     34066724127).
  2. The same literal, one authority further out: the spine's independent re-observation of
     the factory family carried `timeout=1200` against a catalogue of several hundred
     defects.
  3. D-073 fixed both by deriving them from measurement -- and the wrapper that CONTAINS
     them was left as a literal. `scripts/factory_evidence.py` runs the whole ladder as one
     subprocess with `timeout=1800`, so the mutation rung's new 8460 s budget sat inside an
     1800 s wrapper. Run 34081507222 of PR #134 passed security, provenance and all five
     judges and then died on `TimeoutExpired: harness/ci.py timed out after 1800 seconds`
     (D-075).

So there are two rules here, and the second is the one that was missing.

    A DURATION COMES FROM A MEASUREMENT.  budget = ceil(p100(observations) * headroom / 60)
    * 60, per scope. p100 and not the mean, because a rung has to finish on its worst
    observed run, not on its average one. Every observation says where it came from, and a
    number nobody has observed says `kind: projected` out loud.

    A WRAPPER BOUNDS WHAT IT CONTAINS.  Every scope declares the scopes whose clocks run
    inside it, plus the contained work that has a bound of its own (`bounded_seconds`) and
    the contained work that has none (`unbudgeted_seconds`, a measurement, so it gets the
    headroom). `validate()` REFUSES a record in which any wrapper's budget is smaller than
    that sum. Raising an inner budget past its wrapper therefore fails the suite -- and the
    gate itself -- in the change that raises it, instead of on some future validation run.

`harness/budgets.json` holds the scopes and the observations; nothing outside it names a
number of seconds. `tests/factory/test_factory_ladder_budget.py` pins the containment rule
and the absence of literals; `tests/factory/test_factory_mutation_budget.py` pins the
derivation and the drift warnings.

The third rule is that drift stops being silent: every timed rung prints how much of its
budget it used once it crosses `warn_fraction` of it, so a rung announces that it is running
out of room on the runs that still pass.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

RECORD = Path(__file__).resolve().parent / "budgets.json"
REQUIRED_FIELDS = ("id", "date", "environment", "kind", "scope", "source", "total_seconds")
KINDS = ("measured", "projected")
SCOPE_FIELDS = ("what", "applied_by")
# A budget with less than half again the worst observation is not a budget; it is the
# observation with a rounding error. FACTORY_RULES section 4 states the same floor in prose.
MINIMUM_HEADROOM = 1.5
# What a runner keeps back from its own deadline so that IT reports which step ran long,
# before the wrapper one level out kills the whole process with nothing to say.
RESERVE_SECONDS = 15


def load(path: Path | None = None) -> dict[str, Any]:
    """Read the budget record. Raises rather than defaulting: a missing, malformed or
    self-inconsistent record must stop the gate, not silently restore an unmeasured literal."""
    record = json.loads((path or RECORD).read_text(encoding="utf-8"))
    validate(record)
    return record


def scopes(record: dict[str, Any]) -> dict[str, Any]:
    declared = record.get("scopes")
    if not isinstance(declared, dict) or not declared:
        raise ValueError("the budget record declares no scopes")
    return declared


def scope_names(record: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(scopes(record)))


def contained(record: dict[str, Any], scope: str) -> tuple[tuple[str, int], ...]:
    """The (inner scope, count) pairs whose clocks run inside `scope`.

    `count` is how many of that inner budget can elapse IN SEQUENCE before this scope's own
    deadline is reached. A sub-bound that shares its wrapper's deadline -- one static check
    inside the static rung, one focused test file inside the factory family -- counts once:
    it is a diagnostic refinement that names which step hung and can never outlive the
    wrapper. A count above one is for a scope genuinely entered N times in a row.
    """
    entry = scopes(record)[scope]
    return tuple((str(item["scope"]), int(item["count"])) for item in entry.get("contains", []))


def _validate_scope(record: dict[str, Any], name: str, entry: Any) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"scope {name!r} is not an object")
    for field in SCOPE_FIELDS:
        if not entry.get(field):
            raise ValueError(
                f"scope {name!r} is missing {field}; a clock nobody can trace to the thing "
                "it bounds is the literal this file retires"
            )
    if not isinstance(entry.get("applied_by"), list) or not entry["applied_by"]:
        raise ValueError(f"scope {name!r} must name at least one caller in applied_by")
    for item in entry.get("contains", []):
        if not isinstance(item, dict) or "scope" not in item or "count" not in item:
            raise ValueError(f"scope {name!r} has a contains entry without scope and count")
        if item["scope"] not in record["scopes"]:
            raise ValueError(f"scope {name!r} contains unknown scope {item['scope']!r}")
        if item["scope"] == name:
            raise ValueError(f"scope {name!r} contains itself")
        count = item["count"]
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(
                f"scope {name!r} contains {item['scope']!r} a non-positive number of times"
            )
    for field in ("bounded_seconds", "unbudgeted_seconds"):
        value = entry.get(field, 0)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError(f"scope {name!r} has a negative or non-numeric {field}")
    has_margin = entry.get("bounded_seconds", 0) or entry.get("unbudgeted_seconds", 0)
    if has_margin and len(str(entry.get("margin_source", ""))) <= 40:
        raise ValueError(
            f"scope {name!r} claims margin seconds without a margin_source that names where "
            "they came from; a margin nobody measured is the literal wearing a different hat"
        )


def _assert_acyclic(record: dict[str, Any]) -> None:
    """A cycle would make budget_seconds recurse forever; say so instead of hanging."""
    state: dict[str, int] = {}

    def walk(name: str, path: tuple[str, ...]) -> None:
        if state.get(name) == 1:
            raise ValueError(
                "the budget record's containment graph has a cycle: " + " -> ".join([*path, name])
            )
        if state.get(name) == 2:
            return
        state[name] = 1
        for inner, _count in contained(record, name):
            walk(inner, (*path, name))
        state[name] = 2

    for name in scopes(record):
        walk(name, ())


def validate(record: dict[str, Any]) -> None:
    declared = scopes(record)
    for name, entry in declared.items():
        _validate_scope(record, name, entry)
    _assert_acyclic(record)

    observations = record.get("measurements")
    if not isinstance(observations, list) or not observations:
        raise ValueError("the budget record holds no measurements")
    for entry in observations:
        missing = [field for field in REQUIRED_FIELDS if not entry.get(field)]
        if missing:
            raise ValueError(
                f"measurement {entry.get('id', '?')!r} is missing {', '.join(missing)}; "
                "every recorded number names where and how it was obtained"
            )
        if entry["kind"] not in KINDS:
            raise ValueError(f"measurement {entry['id']!r} has an unknown kind {entry['kind']!r}")
        if entry["scope"] not in declared:
            raise ValueError(f"measurement {entry['id']!r} has an unknown scope {entry['scope']!r}")
        if float(entry["total_seconds"]) <= 0:
            raise ValueError(f"measurement {entry['id']!r} records a non-positive total")
    for scope in declared:
        if not [entry for entry in observations if entry["scope"] == scope]:
            raise ValueError(
                f"no measurement records the {scope!r} scope; every derived budget must have "
                "an observation behind it"
            )

    headroom = float(record.get("headroom", 0))
    if headroom < MINIMUM_HEADROOM:
        raise ValueError(
            f"headroom {headroom} is below the {MINIMUM_HEADROOM} floor; the budget must "
            "leave at least half again the worst observed run"
        )
    warn_fraction = float(record.get("warn_fraction", 0))
    if not 0.0 < warn_fraction < 1.0:
        raise ValueError("warn_fraction must be a fraction of the budget, strictly between 0 and 1")

    # THE RULE THE LADDER WAS MISSING. Checked here and not only in a test, so a record whose
    # wrappers no longer contain their parts stops every gate that reads it.
    for scope in sorted(declared):
        floor = containment_floor(record, scope)
        budget = budget_seconds(record, scope)
        if budget < floor:
            raise ValueError(
                f"the {scope!r} budget is {budget}s but it contains {floor}s: "
                + describe_containment(record, scope)
                + ". A wrapper must bound what it contains. Record the measurement that "
                "justifies the larger wrapper in this same change, or lower what is inside it."
            )


def measurements(record: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    if scope not in scopes(record):
        raise ValueError(f"unknown budget scope {scope!r}")
    return [entry for entry in record["measurements"] if entry["scope"] == scope]


def measured_seconds(record: dict[str, Any], scope: str) -> float:
    """p100 of the recorded totals for a scope: the worst run, not the typical one."""
    return max(float(entry["total_seconds"]) for entry in measurements(record, scope))


def budget_seconds(record: dict[str, Any], scope: str) -> int:
    """The derived budget: the worst observation plus headroom, rounded up to a whole minute."""
    generous = measured_seconds(record, scope) * float(record["headroom"])
    return int(math.ceil(generous / 60.0) * 60)


def containment_floor(record: dict[str, Any], scope: str) -> int:
    """The smallest budget that still bounds everything inside this scope.

    Inner BUDGETS are added as they stand -- each is already a bound, and giving a bound
    headroom a second time is how a tower of wrappers inflates past what any runner allows.
    `bounded_seconds` is contained work whose deadline is set somewhere else and is added the
    same way. `unbudgeted_seconds` is a MEASUREMENT of contained work with no deadline at
    all, so it gets the headroom every measurement gets.
    """
    entry = scopes(record)[scope]
    total = float(
        sum(count * budget_seconds(record, inner) for inner, count in contained(record, scope))
    )
    total += float(entry.get("bounded_seconds", 0))
    total += float(entry.get("unbudgeted_seconds", 0)) * float(record["headroom"])
    return math.ceil(total)


def describe_containment(record: dict[str, Any], scope: str) -> str:
    entry = scopes(record)[scope]
    parts = [
        f"{count}x{inner}={count * budget_seconds(record, inner)}s"
        for inner, count in contained(record, scope)
    ]
    if entry.get("bounded_seconds", 0):
        parts.append(f"bounded={entry['bounded_seconds']}s")
    if entry.get("unbudgeted_seconds", 0):
        parts.append(f"unbudgeted={entry['unbudgeted_seconds']}s x{float(record['headroom'])}")
    return " + ".join(parts) if parts else "nothing"


def warn_seconds(record: dict[str, Any], scope: str) -> float:
    """The total at which a runner starts saying it is running out of room."""
    return budget_seconds(record, scope) * float(record["warn_fraction"])


def deadline(record: dict[str, Any], scope: str, *, reserve: int = RESERVE_SECONDS) -> float:
    """A monotonic instant this scope's work must finish by.

    A runner that owns several steps under one budget gives each step whatever is LEFT rather
    than the whole budget: five static checks with `timeout=600` each are a 3000 s rung
    wearing a 600 s label, which is how `harness/static.py` came to bound more than the
    `harness/ci.py` rung that contains it. The reserve is what the runner keeps back so it can
    name the step that ran long before its own wrapper kills it with nothing to say.
    """
    return time.monotonic() + max(1, budget_seconds(record, scope) - reserve)


def remaining(until: float, *, floor: float = 1.0) -> float:
    """Seconds left before `until`, never zero or negative: subprocess rejects those."""
    return max(floor, until - time.monotonic())


def drift_warning(seconds: float, record: dict[str, Any], *, scope: str, marker: str) -> str | None:
    """The line a runner prints when its total crosses the warning threshold, else None.

    Returned rather than printed so the threshold is testable without capturing stdout.
    """
    budget = budget_seconds(record, scope)
    threshold = warn_seconds(record, scope)
    if seconds <= threshold:
        return None
    left = 100 - float(record["warn_fraction"]) * 100
    return (
        f"{marker} seconds={seconds:.1f} budget={budget} threshold={threshold:.1f} "
        f"scope={scope} - this run used more than "
        f"{float(record['warn_fraction']) * 100:.0f}% of the budget, so under {left:.0f}% "
        f"is left. Re-measure the rung and record the new number in "
        f"harness/budgets.json before the drift arrives as a TIMEOUT on a run that "
        f"mattered."
    )


def summary(record: dict[str, Any]) -> str:
    lines = [
        f"LADDER_BUDGET headroom={float(record['headroom'])} "
        f"warn_fraction={float(record['warn_fraction'])}"
    ]
    for scope in scope_names(record):
        lines.append(
            f"  {scope:<22} budget={budget_seconds(record, scope):>6}s "
            f"measured={measured_seconds(record, scope):>8.1f}s "
            f"n={len(measurements(record, scope))} "
            f"contains={containment_floor(record, scope):>6}s "
            f"[{describe_containment(record, scope)}]"
        )
    return "\n".join(lines)


if __name__ == "__main__":  # a human asking what the gate will allow
    print(summary(load()))
