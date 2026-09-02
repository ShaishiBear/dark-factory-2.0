"""Canonical factory benchmark schema and deterministic scorer.

The benchmark authority is intentionally separate from the benchmark cases. Public smoke cases may
live in this repository, while hidden cases can be supplied from a private companion checkout. The
factory under test emits observed outcomes; this module alone decides whether they match the trusted
case set.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
OUTCOMES = {
    "stop",
    "do-not-guess",
    "decompose",
    "reject",
    "merge",
    "recover",
    "single-execution",
    "wait",
    "needs-human",
}


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    expected: str
    visibility: str
    description: str


@dataclass(frozen=True)
class BenchmarkSuite:
    suite_id: str
    visibility: str
    cases: tuple[BenchmarkCase, ...]
    sha256: str


def _load(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read benchmark JSON {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"benchmark JSON must be an object: {path}")
    return value


def load_suite(path: Path) -> BenchmarkSuite:
    value = _load(path)
    if value.get("version") != "1.0":
        raise ValueError("benchmark suite version must be 1.0")
    suite_id = str(value.get("suite_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", suite_id):
        raise ValueError("benchmark suite_id is invalid")
    visibility = str(value.get("visibility") or "")
    if visibility not in {"public", "private"}:
        raise ValueError("benchmark visibility must be public or private")
    raw_cases = value.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("benchmark suite must contain cases")
    cases: list[BenchmarkCase] = []
    ids: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise ValueError("benchmark case must be an object")
        case_id = str(raw.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,100}", case_id) or case_id in ids:
            raise ValueError("benchmark case ids must be valid and unique")
        ids.add(case_id)
        expected = str(raw.get("expected") or "")
        if expected not in OUTCOMES:
            raise ValueError(f"unsupported benchmark expected outcome: {expected}")
        description = str(raw.get("description") or "").strip()
        if not description:
            raise ValueError(f"benchmark case {case_id} has no description")
        cases.append(BenchmarkCase(case_id, expected, visibility, description))
    return BenchmarkSuite(
        suite_id=suite_id,
        visibility=visibility,
        cases=tuple(cases),
        sha256=digest(value),
    )


def load_results(path: Path, *, factory_sha: str) -> dict[str, str]:
    if not OID.fullmatch(factory_sha):
        raise ValueError("factory_sha is not a valid git object id")
    value = _load(path)
    if value.get("version") != "1.0":
        raise ValueError("benchmark result version must be 1.0")
    if value.get("factory_sha") != factory_sha:
        raise ValueError("benchmark results are not bound to the exact factory SHA")
    raw = value.get("results")
    if not isinstance(raw, list):
        raise ValueError("benchmark results must be an array")
    results: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("benchmark result must be an object")
        case_id = str(item.get("id") or "")
        outcome = str(item.get("outcome") or "")
        if not case_id or case_id in results:
            raise ValueError("benchmark result ids must be non-empty and unique")
        if outcome not in OUTCOMES:
            raise ValueError(f"unsupported benchmark observed outcome: {outcome}")
        results[case_id] = outcome
    return results


def score(
    suites: Iterable[BenchmarkSuite],
    *,
    results: Mapping[str, str],
    factory_sha: str,
    require_private: bool = False,
) -> dict:
    suite_list = tuple(suites)
    if not suite_list:
        raise ValueError("at least one benchmark suite is required")
    if not OID.fullmatch(factory_sha):
        raise ValueError("factory_sha is not a valid git object id")
    if require_private and not any(s.visibility == "private" for s in suite_list):
        raise ValueError("a private hidden benchmark suite is required")

    cases: dict[str, BenchmarkCase] = {}
    for suite in suite_list:
        for case in suite.cases:
            if case.case_id in cases:
                raise ValueError(f"duplicate benchmark case id across suites: {case.case_id}")
            cases[case.case_id] = case
    if set(results) != set(cases):
        missing = sorted(set(cases) - set(results))
        extra = sorted(set(results) - set(cases))
        raise ValueError(f"benchmark result coverage mismatch: missing={missing} extra={extra}")

    failures = [
        {
            "id": case_id,
            "expected": cases[case_id].expected,
            "observed": results[case_id],
        }
        for case_id in sorted(cases)
        if results[case_id] != cases[case_id].expected
    ]
    suite_binding = [
        {"suite_id": suite.suite_id, "visibility": suite.visibility, "sha256": suite.sha256}
        for suite in sorted(suite_list, key=lambda item: item.suite_id)
    ]
    return {
        "version": "1.0",
        "verdict": "pass" if not failures else "fail",
        "factory_sha": factory_sha,
        "cases_total": len(cases),
        "cases_passed": len(cases) - len(failures),
        "private_suite_present": any(s.visibility == "private" for s in suite_list),
        "suites": suite_binding,
        "case_set_sha256": digest(suite_binding),
        "results_sha256": digest(dict(sorted(results.items()))),
        "failures": failures,
    }
