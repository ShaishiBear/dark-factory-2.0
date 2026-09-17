"""Frozen evaluation protocols and their bounded interpretation (LINE_LEVEL_CLAIMS 3, C09).

A code experiment is a frozen comparison, not a prompt score. The protocol is frozen before
search: which evaluators (trusted, by digest), which workload identity, how many repetitions,
which registered statistic, which practical-effect threshold and which exposure rule. Changing
any of them is a different study. Two adapters exist initially and nothing else:

- `finite-contract-v1`: deterministically execute a frozen finite suite against an independent
  oracle. Eligibility needs every mandatory case complete. Results establish that contract only.
- `paired-fixed-v1`: fresh confirmation units, fixed trial count, per-unit oriented differences,
  the exact one-sided paired sign test with ties reported and excluded from n. It tests direction
  under independent paired units; it proves no magnitude and no generality.

Statistics stay exact rationals (integer numerator over 2**n) and thresholds come from the
protocol, never from a model after seeing results. `equivalent_within_bounds` is disabled:
failure to detect improvement is `inconclusive`, not equivalence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import comb
from typing import Any, Iterable, Mapping

from .canonical import sha256_value

SCHEMA_VERSION = "1.0"
PROTOCOL_SCHEMA = "dark-factory/evaluation-protocol"
ADAPTERS = ("finite-contract-v1", "paired-fixed-v1")
STATISTICS = ("exact-paired-sign-v1",)
MAX_REPETITIONS = 200
MAX_EVALUATORS = 8


class ProtocolRefused(ValueError):
    pass


# ---------- exact statistic ----------

def paired_sign_test(differences: Iterable[Any]) -> dict[str, Any]:
    """Exact one-sided paired sign test over integer differences oriented positive = better.

    Zero differences are ties: reported and excluded from n. `n` nonzero pairs, `k` positive,
    p = sum(comb(n, j) for j in k..n) / 2**n, kept unreduced as numerator/denominator. No pairs
    after ties is `insufficient`; a boolean or non-integer measurement is `refused`.
    """
    values = list(differences)
    for value in values:
        if type(value) is not int:
            return {"status": "refused", "reason_codes": ["invalid_measurement"]}
    ties = sum(1 for v in values if v == 0)
    nonzero = [v for v in values if v != 0]
    n = len(nonzero)
    if n == 0:
        return {"status": "insufficient", "reason_codes": ["no_nonzero_pairs"], "ties": ties}
    k = sum(1 for v in nonzero if v > 0)
    numerator = sum(comb(n, j) for j in range(k, n + 1))
    return {"status": "computed", "reason_codes": [], "n": n, "k": k, "ties": ties,
            "numerator": numerator, "denominator": 2 ** n}


def rational_at_most(numerator: int, denominator: int, alpha_numerator: int, alpha_denominator: int) -> bool:
    """p <= alpha by cross multiplication; no floating point."""
    return numerator * alpha_denominator <= alpha_numerator * denominator


# ---------- protocol ----------

@dataclass(frozen=True)
class Evaluator:
    id: str
    version: str
    authority_closure_digest: str
    admissible_claim_scope: str
    argv: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "authority_closure_digest": self.authority_closure_digest,
                "admissible_claim_scope": self.admissible_claim_scope, "argv": list(self.argv)}


@dataclass(frozen=True)
class EvaluationProtocol:
    adapter: str
    hard_constraints: tuple[str, ...]
    objective_vector: tuple[dict[str, str], ...]  # [{name, unit, direction}]
    priority_or_tradeoff_policy: str
    evaluators: tuple[Evaluator, ...]
    workload_identity: str
    independent_unit: str
    sampling_scheme: str
    seed_policy: str
    paired_comparison_policy: str
    execution_order_policy: str
    warmup_and_cache_policy: str
    repetitions: int
    statistic_id: str | None
    uncertainty_method_id: str | None
    practical_effect_thresholds: Mapping[str, int]
    multiple_comparison_policy: str
    confirmation_policy: str
    maximum_looks: int
    stop_rule: str
    exclusion_rule: str
    exposure_policy: str
    cost_and_resource_limits: Mapping[str, int]
    alpha: tuple[int, int] | None = None  # rational (numerator, denominator)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": PROTOCOL_SCHEMA, "schema_version": SCHEMA_VERSION, "adapter": self.adapter,
                "hard_constraints": list(self.hard_constraints), "objective_vector": [dict(o) for o in self.objective_vector],
                "priority_or_tradeoff_policy": self.priority_or_tradeoff_policy,
                "evaluators": [e.to_dict() for e in self.evaluators], "workload_identity": self.workload_identity,
                "independent_unit": self.independent_unit, "sampling_scheme": self.sampling_scheme,
                "seed_policy": self.seed_policy, "paired_comparison_policy": self.paired_comparison_policy,
                "execution_order_policy": self.execution_order_policy, "warmup_and_cache_policy": self.warmup_and_cache_policy,
                "repetitions": self.repetitions, "statistic_id": self.statistic_id,
                "uncertainty_method_id": self.uncertainty_method_id,
                "practical_effect_thresholds": dict(self.practical_effect_thresholds),
                "multiple_comparison_policy": self.multiple_comparison_policy, "confirmation_policy": self.confirmation_policy,
                "maximum_looks": self.maximum_looks, "stop_rule": self.stop_rule, "exclusion_rule": self.exclusion_rule,
                "exposure_policy": self.exposure_policy, "cost_and_resource_limits": dict(self.cost_and_resource_limits),
                "alpha": list(self.alpha) if self.alpha else None}

    def digest(self) -> str:
        return sha256_value(self.to_dict())


def _positive_int(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise ProtocolRefused(f"{name} must be an integer from 1 to {maximum}")
    return value


def freeze_protocol(proposal: Mapping[str, Any], trusted_evaluators: Iterable[Evaluator],
                    exposure_state: Mapping[str, Any] | None = None) -> EvaluationProtocol:
    """Validate a proposal against reviewed evaluators and freeze it. Evaluators are resolved by
    digest from the trusted registry, never taken from the proposal; a candidate cannot supply
    evaluator code. Thresholds and alpha are explicit inputs; nothing is defaulted from data."""
    if not isinstance(proposal, Mapping):
        raise ProtocolRefused("protocol proposal must be a mapping")
    adapter = proposal.get("adapter")
    if adapter not in ADAPTERS:
        raise ProtocolRefused(f"unknown protocol adapter {adapter!r}")
    trusted = {e.authority_closure_digest: e for e in trusted_evaluators}
    wanted = proposal.get("evaluators")
    if not isinstance(wanted, list) or not wanted or len(wanted) > MAX_EVALUATORS:
        raise ProtocolRefused("protocol needs 1 to 8 evaluators by digest")
    evaluators = []
    for digest in wanted:
        if digest not in trusted:
            raise ProtocolRefused("protocol names an evaluator outside the trusted registry")
        evaluators.append(trusted[digest])
    objectives = proposal.get("objective_vector", [])
    if not isinstance(objectives, list) or len(objectives) > 8:
        raise ProtocolRefused("objective vector must be a list of at most 8 metrics")
    for objective in objectives:
        if (not isinstance(objective, Mapping) or set(objective) != {"name", "unit", "direction"}
                or objective["direction"] not in {"minimise", "maximise"}
                or any(not isinstance(objective[k], str) or not objective[k] for k in ("name", "unit"))):
            raise ProtocolRefused("each objective needs name, unit and an explicit direction")
    thresholds = proposal.get("practical_effect_thresholds", {})
    if not isinstance(thresholds, Mapping) or any(type(v) is not int or v < 0 for v in thresholds.values()):
        raise ProtocolRefused("practical effect thresholds must be non-negative integers per metric")
    limits = proposal.get("cost_and_resource_limits", {})
    if not isinstance(limits, Mapping) or not limits or any(type(v) is not int or v <= 0 for v in limits.values()):
        raise ProtocolRefused("cost and resource limits must be explicit positive integers")
    alpha = None
    statistic = proposal.get("statistic_id")
    if adapter == "paired-fixed-v1":
        if statistic not in STATISTICS:
            raise ProtocolRefused("paired-fixed-v1 requires a registered statistic")
        raw_alpha = proposal.get("alpha")
        if (not isinstance(raw_alpha, list) or len(raw_alpha) != 2 or any(type(v) is not int for v in raw_alpha)
                or raw_alpha[0] <= 0 or raw_alpha[1] <= 0 or raw_alpha[0] >= raw_alpha[1]):
            raise ProtocolRefused("paired-fixed-v1 requires a preregistered rational alpha below one")
        alpha = (raw_alpha[0], raw_alpha[1])
        if not objectives:
            raise ProtocolRefused("paired-fixed-v1 requires at least one objective")
        for objective in objectives:
            if objective["name"] not in thresholds:
                raise ProtocolRefused(f"paired-fixed-v1 requires a practical effect threshold for {objective['name']!r}")
        repetitions = _positive_int(proposal.get("repetitions"), "repetitions", MAX_REPETITIONS)
    else:
        if statistic is not None:
            raise ProtocolRefused("finite-contract-v1 is deterministic; it registers no statistic")
        repetitions = 1
    if exposure_state is not None:
        remaining = exposure_state.get("remaining_confirmations")
        if type(remaining) is not int or remaining <= 0:
            raise ProtocolRefused("confirmation exposure is exhausted or unknown for this lineage")
    text = lambda key, default: str(proposal.get(key, default))  # noqa: E731
    return EvaluationProtocol(
        adapter=adapter, hard_constraints=tuple(str(h) for h in proposal.get("hard_constraints", ("contract-pass",))),
        objective_vector=tuple(dict(o) for o in objectives),
        priority_or_tradeoff_policy=text("priority_or_tradeoff_policy", "lexicographic"),
        evaluators=tuple(evaluators), workload_identity=text("workload_identity", "frozen-acceptance-contract"),
        independent_unit=text("independent_unit", "acceptance-checkpoint"), sampling_scheme=text("sampling_scheme", "exhaustive"),
        seed_policy=text("seed_policy", "none"), paired_comparison_policy=text("paired_comparison_policy", "same-workload"),
        execution_order_policy=text("execution_order_policy", "baseline-first-then-candidates-in-id-order"),
        warmup_and_cache_policy=text("warmup_and_cache_policy", "cold-disposable-tree"),
        repetitions=repetitions, statistic_id=statistic, uncertainty_method_id=proposal.get("uncertainty_method_id"),
        practical_effect_thresholds=dict(thresholds), multiple_comparison_policy=text("multiple_comparison_policy", "bonferroni-min"),
        confirmation_policy=text("confirmation_policy", "none-public-search-only"),
        maximum_looks=_positive_int(proposal.get("maximum_looks", 1), "maximum_looks", 1),
        stop_rule=text("stop_rule", "fixed-final-analysis"), exclusion_rule=text("exclusion_rule", "crash-or-timeout-excluded-and-reported"),
        exposure_policy=text("exposure_policy", "public-search"), cost_and_resource_limits=dict(limits), alpha=alpha)


# ---------- observations ----------

@dataclass(frozen=True)
class AdmissibleObservation:
    candidate_id: str
    protocol_digest: str
    evaluator_id: str
    complete: bool
    hard_pass: bool | None
    metrics: Mapping[str, int | None]
    exit_code: int | None
    duration_ns: int | None
    output_sha256: str | None
    limitations: tuple[str, ...] = ()
    incomplete_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "protocol_digest": self.protocol_digest, "evaluator_id": self.evaluator_id,
                "complete": self.complete, "hard_pass": self.hard_pass, "metrics": dict(self.metrics),
                "exit_code": self.exit_code, "duration_ns": self.duration_ns, "output_sha256": self.output_sha256,
                "limitations": list(self.limitations), "incomplete_reasons": list(self.incomplete_reasons)}


def validate_observation(protocol: EvaluationProtocol, candidate_id: str, observed_run: Mapping[str, Any]) -> AdmissibleObservation:
    """One raw run becomes an admissible observation only if it names the protocol's evaluator
    and reports integers where metrics are due. A crash or timeout is complete=False with the
    reason; it is never a poor numeric measurement."""
    evaluator_ids = {e.id for e in protocol.evaluators}
    if observed_run.get("evaluator_id") not in evaluator_ids:
        raise ProtocolRefused("observation names an evaluator outside the frozen protocol")
    if observed_run.get("protocol_digest") != protocol.digest():
        raise ProtocolRefused("observation belongs to a different frozen protocol")
    status = observed_run.get("status")
    if status not in {"returned", "timeout", "crashed", "not_started"}:
        raise ProtocolRefused("observation status must be returned, timeout, crashed or not_started")
    exit_code = observed_run.get("exit_code")
    if exit_code is not None and type(exit_code) is not int:
        raise ProtocolRefused("exit code must be an integer or null")
    metrics: dict[str, int | None] = {}
    for objective in protocol.objective_vector:
        value = observed_run.get("metrics", {}).get(objective["name"])
        if value is not None and type(value) is not int:
            raise ProtocolRefused(f"metric {objective['name']!r} must be an integer or null")
        metrics[objective["name"]] = value
    duration = observed_run.get("duration_ns")
    if duration is not None and (type(duration) is not int or duration < 0):
        raise ProtocolRefused("duration must be integer nanoseconds or null")
    if status != "returned":
        return AdmissibleObservation(candidate_id, protocol.digest(), observed_run["evaluator_id"], False, None, metrics,
                                     exit_code, duration, observed_run.get("output_sha256"),
                                     tuple(observed_run.get("limitations", ())), (status,))
    missing = [name for name, value in metrics.items() if value is None]
    hard_pass = bool(observed_run.get("hard_pass")) if observed_run.get("hard_pass") is not None else (exit_code == 0)
    return AdmissibleObservation(candidate_id, protocol.digest(), observed_run["evaluator_id"], not missing, hard_pass, metrics,
                                 exit_code, duration, observed_run.get("output_sha256"),
                                 tuple(observed_run.get("limitations", ())), tuple(f"metric_missing:{m}" for m in missing))


# ---------- comparison ----------

@dataclass(frozen=True)
class ComparisonResult:
    adapter: str
    outcome: str  # provisional | tie | no_eligible | insufficient | inconclusive | supported | refused
    reason_codes: tuple[str, ...]
    selected_candidate_id: str | None
    eligible_candidate_ids: tuple[str, ...]
    rejected_with_reasons: tuple[dict[str, str], ...]
    effect_estimates: Mapping[str, Any]
    uncertainty: Mapping[str, Any]
    limitations: tuple[str, ...]
    merge_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"adapter": self.adapter, "outcome": self.outcome, "reason_codes": list(self.reason_codes),
                "selected_candidate_id": self.selected_candidate_id, "eligible_candidate_ids": list(self.eligible_candidate_ids),
                "rejected_with_reasons": list(self.rejected_with_reasons), "effect_estimates": dict(self.effect_estimates),
                "uncertainty": dict(self.uncertainty), "limitations": list(self.limitations), "merge_authorized": False}


def screen_finite(baseline_id: str, rows: Iterable[Mapping[str, Any]]) -> ComparisonResult:
    """Complete public deterministic screening (C09 finite-contract-v1). Each row is
    `{id, metric, hard_pass, complete}`; hard failures are excluded; a lower integer metric
    wins; ties retain an eligible baseline, otherwise a tie is returned. Nothing here
    authorises a merge."""
    rows = list(rows)
    if not rows:
        return ComparisonResult("finite-contract-v1", "insufficient", ("measurement_missing",), None, (), (), {}, {}, ("no candidates were observed",))
    ids = [r.get("id") for r in rows]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) for i in ids):
        return ComparisonResult("finite-contract-v1", "refused", ("duplicate_or_invalid_candidate",), None, (), (), {}, {}, ())
    incomplete = [r["id"] for r in rows if r.get("complete") is not True]
    if incomplete:
        return ComparisonResult("finite-contract-v1", "insufficient", ("measurement_incomplete",), None, (),
                                tuple({"id": i, "reason": "incomplete"} for i in incomplete), {}, {}, ("scheduled sample coverage not met",))
    for row in rows:
        if type(row.get("hard_pass")) is not bool or type(row.get("metric")) is not int:
            return ComparisonResult("finite-contract-v1", "refused", ("invalid_measurement",), None, (), (), {}, {}, ())
    rejected = tuple({"id": r["id"], "reason": "hard_constraint_failed"} for r in rows if not r["hard_pass"])
    eligible = [r for r in rows if r["hard_pass"]]
    if not eligible:
        return ComparisonResult("finite-contract-v1", "no_eligible", ("hard_constraint_failed",), None, (), rejected, {}, {}, ())
    best_metric = min(r["metric"] for r in eligible)
    best = [r["id"] for r in eligible if r["metric"] == best_metric]
    estimates = {r["id"]: {"metric": r["metric"]} for r in eligible}
    if len(best) == 1:
        return ComparisonResult("finite-contract-v1", "provisional", (), best[0], tuple(r["id"] for r in eligible), rejected, estimates, {},
                                ("selected among these measured candidates for this contract only",))
    selected = baseline_id if baseline_id in best else None
    return ComparisonResult("finite-contract-v1", "tie", (), selected, tuple(r["id"] for r in eligible), rejected, estimates, {},
                            ("no ordered objective separates the eligible candidates",))


def assess_paired(protocol: EvaluationProtocol, differences: Iterable[Any], *, metric: str, candidate_id: str,
                  baseline_id: str) -> ComparisonResult:
    """paired-fixed-v1: one frozen finalist against the unchanged baseline on fresh units.

    `differences` are per-unit oriented improvements (baseline minus candidate for minimise,
    reversed for maximise). Reports raw differences, count, median and the exact sign test;
    `supported` needs both the registered alpha and the preregistered practical threshold on
    the median; otherwise `inconclusive`, never equivalence.
    """
    if protocol.adapter != "paired-fixed-v1" or protocol.alpha is None:
        raise ProtocolRefused("paired assessment needs a frozen paired-fixed-v1 protocol")
    values = list(differences)
    if len(values) != protocol.repetitions:
        return ComparisonResult(protocol.adapter, "insufficient", ("scheduled_units_incomplete",), None, (), (),
                                {"observed_units": len(values), "scheduled_units": protocol.repetitions}, {}, ())
    test = paired_sign_test(values)
    if test["status"] != "computed":
        return ComparisonResult(protocol.adapter, "insufficient" if test["status"] == "insufficient" else "refused",
                                tuple(test["reason_codes"]), None, (), (), {}, {}, ())
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) // 2
    threshold = protocol.practical_effect_thresholds[metric]
    divisor = max(1, len(protocol.objective_vector))  # Bonferroni at minimum across endpoints
    alpha_n, alpha_d = protocol.alpha
    significant = rational_at_most(test["numerator"], test["denominator"], alpha_n, alpha_d * divisor)
    practical = median >= threshold
    estimates = {"metric": metric, "raw_differences": values, "n": test["n"], "k": test["k"], "ties": test["ties"], "median": median}
    uncertainty = {"statistic": protocol.statistic_id, "p_numerator": test["numerator"], "p_denominator": test["denominator"],
                   "alpha": [alpha_n, alpha_d], "bonferroni_divisor": divisor, "practical_threshold": threshold}
    limitations = ("directional improvement under independent paired units only; no magnitude or generality claim",
                   "repeated executions on one input are not independent units")
    if significant and practical:
        return ComparisonResult(protocol.adapter, "supported", (), candidate_id, (candidate_id, baseline_id), (), estimates, uncertainty, limitations)
    reasons = tuple(sorted(({"not_significant"} if not significant else set()) | ({"below_practical_threshold"} if not practical else set())))
    return ComparisonResult(protocol.adapter, "inconclusive", reasons, None, (candidate_id, baseline_id), (), estimates, uncertainty,
                            limitations + ("failure to detect improvement is not equivalence",))


# ---------- exposure ----------

@dataclass(frozen=True)
class ExposureReceipt:
    lineage_key: str
    protocol_digest: str
    release: str
    remaining_confirmations: int
    consumed: int

    def to_dict(self) -> dict[str, Any]:
        return {"lineage_key": self.lineage_key, "protocol_digest": self.protocol_digest, "release": self.release,
                "remaining_confirmations": self.remaining_confirmations, "consumed": self.consumed}


def record_exposure(protocol: EvaluationProtocol, release: str, journal: dict[str, dict[str, Any]], *, lineage_key: str,
                    maximum_confirmations: int) -> ExposureReceipt:
    """Reserve one confirmation exposure durably in `journal` (keyed by lineage, not study ID)
    before any result is revealed. Abandoned or uncertain exposures stay consumed. A retry with
    a new protocol digest does not reset the lineage's count."""
    if release not in {"reserve", "reveal", "abandon"}:
        raise ProtocolRefused("exposure release must be reserve, reveal or abandon")
    if type(maximum_confirmations) is not int or maximum_confirmations < 1:
        raise ProtocolRefused("maximum confirmations must be a positive integer from the protocol registry")
    entry = journal.setdefault(lineage_key, {"consumed": 0, "protocols": [], "maximum": maximum_confirmations})
    if entry["maximum"] != maximum_confirmations:
        raise ProtocolRefused("exposure maximum cannot change for an existing lineage")
    if release == "reserve":
        if entry["consumed"] >= entry["maximum"]:
            raise ProtocolRefused("confirmation exposure exhausted for this lineage")
        entry["consumed"] += 1
        entry["protocols"].append(protocol.digest())
    elif protocol.digest() not in entry["protocols"]:
        raise ProtocolRefused("reveal/abandon without a prior reservation for this protocol")
    return ExposureReceipt(lineage_key, protocol.digest(), release, entry["maximum"] - entry["consumed"], entry["consumed"])
