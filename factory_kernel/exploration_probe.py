"""Fixed data-only experiments. No eval, repository imports, subprocess, files or network.

Lookup strategies are genuinely executed over a declared finite workload. Counters measure
this implementation/workload, not production latency or a general architecture guarantee.
Adding an executable experiment requires a reviewed implementation here, never model argv.
"""
from __future__ import annotations

import bisect
import math
import time

from .canonical import sha256_value
from .frontdoor_intent import IntentRefused, _shape

VERSION = "lookup-workload-v1"
METRICS = frozenset({"comparisons", "build_items", "retained_items", "matches"})


def validate_probe(spec):
    _shape(spec, {"kind", "strategies", "keys", "queries"})
    if spec["kind"] != VERSION:
        raise IntentRefused("unknown executable experiment")
    strategies = spec["strategies"]
    if (not isinstance(strategies, list) or not 1 <= len(strategies) <= 3
            or len(set(strategies)) != len(strategies)
            or not set(strategies) <= {"linear", "binary", "hash"}):
        raise IntentRefused("unknown lookup strategy")
    for field, bound in (("keys", 2000), ("queries", 1000)):
        values = spec[field]
        if (not isinstance(values, list) or not 1 <= len(values) <= bound
                or any(type(value) is not int or abs(value) > 1000000 for value in values)):
            raise IntentRefused("probe data must be bounded integer arrays")
    # Reserve a conservative work bound for every strategy before execution. Hash counts
    # are logical lookups; interpreter implementation details are not a latency prediction.
    return len(strategies) * (len(spec["keys"]) * len(spec["queries"]) + len(spec["keys"]) * 12)


def run_probe(spec, *, check_stop, wall_seconds=5):
    units = validate_probe(spec)
    if type(wall_seconds) not in {int, float} or not math.isfinite(wall_seconds) or not 0 < wall_seconds <= 10:
        raise IntentRefused("invalid experiment deadline")
    deadline = time.monotonic() + wall_seconds
    rows = {}
    for strategy in spec["strategies"]:
        check_stop()
        comparisons, matches = 0, 0
        keys = spec["keys"]
        ordered = sorted(set(keys)) if strategy == "binary" else []
        indexed = set(keys) if strategy == "hash" else set()
        for query in spec["queries"]:
            check_stop()
            if time.monotonic() >= deadline:
                raise IntentRefused("experiment deadline exhausted")
            if strategy == "linear":
                found = False
                for key in keys:
                    comparisons += 1
                    if key == query:
                        found = True
                        break
            elif strategy == "binary":
                # Count the actual comparisons, rather than estimate them from log(n).
                low, high = 0, len(ordered)
                while low < high:
                    middle = (low + high) // 2
                    comparisons += 1
                    if ordered[middle] < query:
                        low = middle + 1
                    else:
                        high = middle
                found = low < len(ordered) and ordered[low] == query
                comparisons += int(low < len(ordered))
                assert low == bisect.bisect_left(ordered, query)
            else:
                comparisons += 1
                found = query in indexed
            matches += int(found)
        rows[strategy] = {"comparisons": comparisons, "build_items": 0 if strategy == "linear" else len(keys),
                          "retained_items": 0 if strategy == "linear" else len(set(keys)), "matches": matches}
    if len({row["matches"] for row in rows.values()}) != 1:
        raise IntentRefused("experiment implementations disagree on correctness")
    return {"kind": "measured", "runner": VERSION, "input_sha256": sha256_value(spec), "results": rows,
            "reserved_units": units, "scope": "finite-declared-workload", "qualification_status": "UNPROVEN",
            "limitations": ["not-production-latency", "hash-counts-are-logical-lookups",
                            "build-items-does-not-measure-sort-cost", "synthetic-data-may-not-represent-production"]}
