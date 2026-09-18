"""Lesson admission (SPECIFICATION 11.1, contract C10, WP11): a protected policy and a fixed order.

A lesson is a proposal until a deterministic evaluator admits it under a protected
`LessonAdmissionPolicy` over independently verified observations. The order never changes:
authenticated evidence -> matching policy and context -> uncontaminated required cohort coverage
-> hard regression and negative-transfer constraints -> practical benefit -> supported
uncertainty criterion -> expiry/drift standing. `reject` names an observed prohibition (forged
evidence, an out-of-scope claim, a hard regression, no benefit); `insufficient` names what is
missing or unknown (no policy, a contaminated or underpowered cohort, an unsupported uncertainty
criterion, a dormant lesson); `admit` requires every gate to pass. Unknown sample sufficiency
never admits. Missing policy permits proposed lessons and offline reports only.

The proposing model cannot change the policy or vote a lesson in: the policy is a versioned
protected file installed through governance, and the observations come from the retained
experiment records, not from the proposal. Negative evidence cannot be removed by a new lesson
id; lineage connects revisions, and a contradiction or a toolchain/context change marks the
lesson dormant pending re-evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .canonical import sha256_value

POLICY_SCHEMA = "dark-factory/lesson-admission-policy"
POLICY_SCHEMA_VERSION = "1.0"
STATUSES = ("admit", "reject", "insufficient")
GATES = ("authenticated", "policy", "in_scope", "clean_split", "adequate", "hard_regression", "benefit",
         "uncertainty_supported", "current")
# Gate -> (status when the gate fails, reason code). The order of this tuple is the admission order.
ORDER: tuple[tuple[str, str, str], ...] = (
    ("authenticated", "reject", "evidence_unauthenticated"),
    ("policy", "insufficient", "policy_missing"),
    ("in_scope", "reject", "outside_applicability"),
    ("clean_split", "insufficient", "cohort_contaminated"),
    ("adequate", "insufficient", "coverage_insufficient"),
    ("hard_regression", "reject", "hard_regression"),
    ("benefit", "reject", "benefit_not_met"),
    ("uncertainty_supported", "insufficient", "uncertainty_unsupported"),
    ("current", "insufficient", "lesson_dormant"),
)
SHA256 = re.compile(r"[0-9a-f]{64}")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")


class LessonRefused(ValueError):
    pass


@dataclass(frozen=True)
class LessonAdmissionPolicy:
    """The protected, versioned record. Values are preregistered for the intended domain; the
    loader validates shape, never invents a threshold."""

    policy_id: str
    version: str
    eligible_roles: tuple[str, ...]
    task_families: tuple[str, ...]
    cohort_digest: str
    baseline_method: str
    equal_total_cost_cap_microusd: int
    minimum_family_coverage: int
    minimum_samples: int
    hard_regression_constraints: tuple[str, ...]
    benefit_metric: str
    minimum_meaningful_effect: float
    uncertainty_rule: str
    maximum_confirmation_exposures: int
    negative_transfer_limit: float
    expiry_observations: int
    drift_triggers: tuple[str, ...]
    sha256: str

    def to_dict(self) -> dict:
        return {"policy_id": self.policy_id, "version": self.version, "eligible_roles": list(self.eligible_roles),
                "task_families": list(self.task_families), "cohort_digest": self.cohort_digest, "baseline_method": self.baseline_method,
                "equal_total_cost_cap_microusd": self.equal_total_cost_cap_microusd, "minimum_family_coverage": self.minimum_family_coverage,
                "minimum_samples": self.minimum_samples, "hard_regression_constraints": list(self.hard_regression_constraints),
                "benefit_metric": self.benefit_metric, "minimum_meaningful_effect": self.minimum_meaningful_effect,
                "uncertainty_rule": self.uncertainty_rule, "maximum_confirmation_exposures": self.maximum_confirmation_exposures,
                "negative_transfer_limit": self.negative_transfer_limit, "expiry_observations": self.expiry_observations,
                "drift_triggers": list(self.drift_triggers), "sha256": self.sha256}


POLICY_FIELDS = {"schema", "schema_version", "policy_id", "version", "eligible_roles", "task_families", "cohort_digest",
                 "baseline_method", "equal_total_cost_cap_microusd", "minimum_family_coverage", "minimum_samples",
                 "hard_regression_constraints", "benefit_metric", "minimum_meaningful_effect", "uncertainty_rule",
                 "maximum_confirmation_exposures", "negative_transfer_limit", "expiry_observations", "drift_triggers"}


def _idents(value: Any, name: str, *, empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not empty) or len(value) > 256:
        raise LessonRefused(f"{name} must be a bounded non-empty list")
    rows = []
    for item in value:
        if not isinstance(item, str) or not IDENT.fullmatch(item) or item in rows:
            raise LessonRefused(f"{name} contains a malformed or repeated identity")
        rows.append(item)
    return tuple(rows)


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LessonRefused(f"{name} must be a positive integer")
    return value


def _fraction(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < float(value) <= 1):
        raise LessonRefused(f"{name} must be a number in (0, 1]")
    return float(value)


def load_policy(path: str | Path) -> LessonAdmissionPolicy:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LessonRefused(f"cannot read lesson admission policy: {exc}") from exc
    return parse_policy(raw)


def parse_policy(raw: Any) -> LessonAdmissionPolicy:
    if not isinstance(raw, dict) or raw.get("schema") != POLICY_SCHEMA or raw.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise LessonRefused("lesson admission policy schema is not supported")
    if set(raw) != POLICY_FIELDS:
        raise LessonRefused("lesson admission policy must contain exactly its registered fields")
    if not isinstance(raw["policy_id"], str) or not IDENT.fullmatch(raw["policy_id"]) or not isinstance(raw["version"], str) or not raw["version"]:
        raise LessonRefused("policy id or version is malformed")
    if not isinstance(raw["cohort_digest"], str) or not SHA256.fullmatch(raw["cohort_digest"]):
        raise LessonRefused("cohort digest must be a sha256")
    for name in ("baseline_method", "benefit_metric", "uncertainty_rule"):
        if not isinstance(raw[name], str) or not raw[name].strip():
            raise LessonRefused(f"{name} must be a non-empty string")
    return LessonAdmissionPolicy(
        policy_id=raw["policy_id"], version=raw["version"], eligible_roles=_idents(raw["eligible_roles"], "eligible_roles"),
        task_families=_idents(raw["task_families"], "task_families"), cohort_digest=raw["cohort_digest"],
        baseline_method=raw["baseline_method"], equal_total_cost_cap_microusd=_positive_int(raw["equal_total_cost_cap_microusd"], "equal_total_cost_cap_microusd"),
        minimum_family_coverage=_positive_int(raw["minimum_family_coverage"], "minimum_family_coverage"),
        minimum_samples=_positive_int(raw["minimum_samples"], "minimum_samples"),
        hard_regression_constraints=_idents(raw["hard_regression_constraints"], "hard_regression_constraints", empty=True),
        benefit_metric=raw["benefit_metric"], minimum_meaningful_effect=_fraction(raw["minimum_meaningful_effect"], "minimum_meaningful_effect"),
        uncertainty_rule=raw["uncertainty_rule"], maximum_confirmation_exposures=_positive_int(raw["maximum_confirmation_exposures"], "maximum_confirmation_exposures"),
        negative_transfer_limit=_fraction(raw["negative_transfer_limit"], "negative_transfer_limit"),
        expiry_observations=_positive_int(raw["expiry_observations"], "expiry_observations"),
        drift_triggers=_idents(raw["drift_triggers"], "drift_triggers", empty=True), sha256=sha256_value(raw))


@dataclass(frozen=True)
class AdmissionResult:
    status: str
    reason_codes: tuple[str, ...]
    gates: Mapping[str, bool | None]
    evidence_refs: tuple[str, ...]
    policy_sha256: str | None

    def to_dict(self) -> dict:
        return {"status": self.status, "reason_codes": list(self.reason_codes), "gates": dict(self.gates),
                "evidence_refs": list(self.evidence_refs), "policy_sha256": self.policy_sha256, "authority": "admission-evaluator"}


def gate_results(observations: Mapping[str, Any], policy: LessonAdmissionPolicy | None) -> dict:
    """Derive the nine gate results from independently verified observations and the policy.
    Each observation may already be a gate result (a boolean), or raw values the policy decides.
    Anything unknown is None, which never admits."""
    gates: dict[str, bool | None] = {}
    for gate in GATES:
        value = observations.get(gate)
        gates[gate] = value if isinstance(value, bool) else None
    # No policy object means no policy, whatever an observation claims: the evaluator cannot
    # apply thresholds it does not hold, and a missing policy permits proposals only.
    gates["policy"] = policy is not None and gates["policy"] is not False
    if policy is not None:
        if gates["in_scope"] is None and isinstance(observations.get("role"), str) and isinstance(observations.get("task_family"), str):
            gates["in_scope"] = observations["role"] in policy.eligible_roles and observations["task_family"] in policy.task_families
        if gates["clean_split"] is None and isinstance(observations.get("cohort_digest"), str):
            gates["clean_split"] = observations["cohort_digest"] == policy.cohort_digest and observations.get("contaminated") is False
        if gates["adequate"] is None and all(isinstance(observations.get(k), int) for k in ("families_covered", "samples")):
            gates["adequate"] = (observations["families_covered"] >= policy.minimum_family_coverage
                                 and observations["samples"] >= policy.minimum_samples)
        if gates["hard_regression"] is None and isinstance(observations.get("regressions"), list):
            gates["hard_regression"] = any(item in policy.hard_regression_constraints for item in observations["regressions"]) or \
                (isinstance(observations.get("negative_transfer"), (int, float)) and float(observations["negative_transfer"]) > policy.negative_transfer_limit)
        if gates["benefit"] is None and isinstance(observations.get("effect"), (int, float)) and not isinstance(observations.get("effect"), bool):
            equal_budget = isinstance(observations.get("total_cost_microusd"), int) and observations["total_cost_microusd"] <= policy.equal_total_cost_cap_microusd
            gates["benefit"] = float(observations["effect"]) >= policy.minimum_meaningful_effect and equal_budget
        if gates["uncertainty_supported"] is None and isinstance(observations.get("uncertainty_rule_met"), bool):
            gates["uncertainty_supported"] = observations["uncertainty_rule_met"]
        if gates["current"] is None and isinstance(observations.get("observations_since_admission"), int):
            drifted = any(item in policy.drift_triggers for item in observations.get("drift_events", []) if isinstance(item, str))
            gates["current"] = observations["observations_since_admission"] < policy.expiry_observations and not drifted \
                and observations.get("contradicted") is not True
    return gates


def evaluate(observations: Mapping[str, Any], policy: LessonAdmissionPolicy | None) -> AdmissionResult:
    """The fixed admission order over gate results. The first failing gate decides the status;
    an unknown gate is insufficient with that gate's reason. `admit` only if every gate passes."""
    if not isinstance(observations, Mapping):
        raise LessonRefused("observations must be a mapping of verified observer results")
    gates = gate_results(observations, policy)
    refs = tuple(sorted(str(r) for r in observations.get("evidence_refs", []) if isinstance(r, str)))
    policy_sha = policy.sha256 if policy is not None else None
    for gate, failure_status, code in ORDER:
        value = gates[gate]
        passed = (value is False) if gate == "hard_regression" else (value is True)
        if passed:
            continue
        if value is None:
            # Unknown never admits: an unobserved gate is insufficient, a prohibition class or not.
            return AdmissionResult("insufficient", (code,), gates, refs, policy_sha)
        return AdmissionResult(failure_status, (code,), gates, refs, policy_sha)
    return AdmissionResult("admit", (), gates, refs, policy_sha)


def admit(proposal: Mapping[str, Any], result: AdmissionResult, *, lineage: Sequence[str] = ()) -> dict:
    """The deterministic admission record for a proposal and its evaluation. Only `admit`
    produces an admitted lesson; the others stay proposals with their reasons attached, and a
    lesson id can never shed the negative evidence of its lineage."""
    if not isinstance(proposal, Mapping) or not isinstance(proposal.get("id"), str) or not proposal["id"]:
        raise LessonRefused("a lesson proposal needs an id")
    record = {
        "schema": "dark-factory/lesson-admission", "schema_version": "1.0", "lesson_id": proposal["id"],
        "proposal_sha256": sha256_value(dict(proposal)), "lineage": list(lineage),
        "status": "admitted" if result.status == "admit" else "proposed",
        "evaluation": result.to_dict(), "authority": "admission-evaluator",
        "retrieval_eligible": result.status == "admit",
    }
    record["record_sha256"] = sha256_value({k: v for k, v in record.items() if k != "record_sha256"})
    return record


def retrieval_eligible(record: Mapping[str, Any], *, role: str, policy: LessonAdmissionPolicy | None, current: bool) -> bool:
    """Admitted AND currently applicable AND the role may see learned material. Blind roles
    never receive a packet; a dormant or contradicted lesson is ineligible whatever its record says."""
    if policy is None or role not in policy.eligible_roles or not current:
        return False
    return record.get("status") == "admitted" and record.get("retrieval_eligible") is True


__all__ = ["AdmissionResult", "GATES", "LessonAdmissionPolicy", "LessonRefused", "ORDER", "STATUSES", "admit", "evaluate",
           "gate_results", "load_policy", "parse_policy", "retrieval_eligible"]
