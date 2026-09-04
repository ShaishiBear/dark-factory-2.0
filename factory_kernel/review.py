"""Two-axis code review: Spec and Standards judged by separate fresh processes.

One model asked to weigh "does it do what the contract says" and "is it built well" in a
single context lets one impression colour the other. The two axes therefore run as separate
workers with disjoint prompts and disjoint artifacts, and this deterministic aggregator combines
them. It fails closed: a missing, malformed or mislabelled artifact is a refusal, not a pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

AXES: tuple[str, ...] = ("spec", "standards")
ROLE_FOR_AXIS: dict[str, str] = {"spec": "review-spec", "standards": "review-standards"}
ARTIFACT_FOR_AXIS: dict[str, str] = {"spec": "review-spec.json", "standards": "review-standards.json"}
SEVERITIES = frozenset({"critical", "high", "medium", "low"})
BLOCKING = frozenset({"critical", "high"})


class ReviewInvalid(ValueError):
    """An axis artifact that cannot be trusted; the caller escalates rather than guesses."""


@dataclass(frozen=True)
class ReviewOutcome:
    verdict: str  # "pass" | "fail"
    findings: tuple[dict[str, Any], ...]
    axes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": "1.0",
            "verdict": self.verdict,
            "axes": list(self.axes),
            "findings": [dict(f) for f in self.findings],
        }


def validate_axis(value: object, axis: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewInvalid(f"{axis} review is not an object")
    if value.get("version") != "1.0":
        raise ReviewInvalid(f"{axis} review has an unsupported version")
    if value.get("axis") != axis:
        raise ReviewInvalid(f"{axis} review artifact is labelled {value.get('axis')!r}")
    verdict = value.get("verdict")
    if verdict not in ("pass", "fail"):
        raise ReviewInvalid(f"{axis} review verdict is invalid: {verdict!r}")
    findings = value.get("findings")
    if not isinstance(findings, list):
        raise ReviewInvalid(f"{axis} review findings must be a list")
    blocking = False
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise ReviewInvalid(f"{axis} review finding is not an object")
        severity = finding.get("severity")
        if severity not in SEVERITIES:
            raise ReviewInvalid(f"{axis} review finding has invalid severity {severity!r}")
        if not isinstance(finding.get("description"), str) or not finding["description"].strip():
            raise ReviewInvalid(f"{axis} review finding lacks a description")
        blocking = blocking or severity in BLOCKING
    # The verdict must agree with the findings: a "pass" with a critical finding, or a "fail"
    # with nothing blocking, is a reviewer that did not follow its own rule.
    if blocking and verdict != "fail":
        raise ReviewInvalid(f"{axis} review passes despite a blocking finding")
    if not blocking and verdict != "pass":
        raise ReviewInvalid(f"{axis} review fails without a blocking finding")
    return {"version": "1.0", "axis": axis, "verdict": verdict, "findings": [dict(f) for f in findings]}


def aggregate(artifacts: Mapping[str, object]) -> ReviewOutcome:
    """Combine one artifact per axis. Every axis must be present and valid; any axis failing fails."""
    missing = [axis for axis in AXES if axis not in artifacts]
    if missing:
        raise ReviewInvalid(f"review axes missing: {missing}")
    validated = {axis: validate_axis(artifacts[axis], axis) for axis in AXES}
    findings: list[dict[str, Any]] = []
    for axis in AXES:
        for finding in validated[axis]["findings"]:
            findings.append({**finding, "axis": axis})
    verdict = "fail" if any(validated[axis]["verdict"] == "fail" for axis in AXES) else "pass"
    return ReviewOutcome(verdict=verdict, findings=tuple(findings), axes=AXES)


def read_axes(artifacts_dir: Path, reader) -> dict[str, object]:
    """Load each axis artifact through the kernel's JSON reader; absence is the reader's error."""
    return {axis: reader(artifacts_dir / ARTIFACT_FOR_AXIS[axis]) for axis in AXES}
