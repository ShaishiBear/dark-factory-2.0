#!/usr/bin/env python3
"""Deterministically score Dark Factory behavioural benchmark outcomes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.benchmark import canonical, load_results, load_suite, score  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", action="append", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--factory-sha", required=True)
    parser.add_argument("--require-private", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        suites = [load_suite(path) for path in args.cases]
        results = load_results(args.results, factory_sha=args.factory_sha)
        value = score(
            suites,
            results=results,
            factory_sha=args.factory_sha,
            require_private=args.require_private,
        )
    except ValueError as exc:
        print(f"FACTORY_BENCHMARK_FAIL: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(value))
    if value["verdict"] != "pass":
        print(
            f"FACTORY_BENCHMARK_FAIL cases={value['cases_total']} "
            f"passed={value['cases_passed']} failures={len(value['failures'])}",
            file=sys.stderr,
        )
        return 1
    print(
        f"FACTORY_BENCHMARK_OK cases={value['cases_total']} "
        f"private={str(value['private_suite_present']).lower()} "
        f"case_set_sha256={value['case_set_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
