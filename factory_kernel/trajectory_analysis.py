"""Offline arithmetic over observations; never a qualification or policy authority."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import math
import re
from typing import Any

from .canonical import canonical_bytes, sha256_value
from .trajectory import MAX_RECORD, MAX_STAGES, WORKER_PATH, oid, positive, timestamp

MAX_RECORDS = 10000
OUTCOMES = {"success", "failure", "cancelled", "timed_out", "startup_failure",
            "action_required", "skipped", "neutral", "stale"}
RESULTS = {"ok", "error", "failed", "refused", "timeout", "cap_reached"}
FLAGS = ("cap_reached", "draft_deadline_missed", "idle_killed")
GAP_REASONS = {"artifact-unavailable", "artifact-expired", "artifact-over-bound",
               "artifact-download-failed", "artifact-metadata-refused", "no-kernel-artifacts",
               "invalid-stage-record", "stage-timings-unavailable", "invalid-artifact-metadata",
               "unrecognized-kernel-attempt", "retention-metadata-over-bound"}


class AnalysisRefused(ValueError):
    """Input cannot support a bounded, unambiguous observation report."""


def repository_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", value):
        raise AnalysisRefused("invalid repository")
    return value


def _rows(value: Any, limit: int) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise AnalysisRefused("invalid bounded list")
    return value


def _measurement(value: Any, *, integer: bool = False) -> float | int | None:
    if value is not None and (type(value) not in ({int} if integer else {int, float})
                              or not math.isfinite(value) or not 0 <= value <= 100000000):
        raise AnalysisRefused("invalid measurement")
    return value


def _duration(start: str, end: str) -> float:
    first = datetime.fromisoformat(timestamp(start).replace("Z", "+00:00"))
    last = datetime.fromisoformat(timestamp(end).replace("Z", "+00:00"))
    seconds = (last - first).total_seconds()
    if seconds < 0:
        raise AnalysisRefused("reversed observation timestamps")
    return seconds


def _gaps(value: Any, scope: str, phase: str | None = None) -> list[dict]:
    result = []
    for row in _rows(value, MAX_STAGES + 50):
        if scope == "retention-index" and isinstance(row, dict) and "counts" in row:
            counts = row["counts"]
            if not isinstance(counts, dict) or set(counts) != {"absent", "invalid", "over-bound"}:
                raise AnalysisRefused("invalid retention gap counts")
            for reason, count in sorted(counts.items()):
                if _measurement(count, integer=True) is None:
                    raise AnalysisRefused("missing retention count")
                if count:
                    result.append({"scope": scope, "phase": phase,
                                   "reason": f"retained-files-{reason}", "count": count})
            continue
        if not isinstance(row, dict) or not isinstance(row.get("reason"), str):
            raise AnalysisRefused("invalid collection gap")
        gap_phase = row.get("phase", phase)
        if gap_phase not in {None, "dispatch", "merge"}:
            raise AnalysisRefused("invalid gap phase")
        reason = row["reason"] if row["reason"] in GAP_REASONS else "other-recorded-gap"
        result.append({"scope": scope, "phase": gap_phase, "reason": reason, "count": 1})
    return result


def _stage(row: dict, ordinal: int) -> dict:
    if (row["kind"] not in {"agent", "exec"} or row["result"] not in RESULTS
            or not isinstance(row["stage"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", row["stage"])
            or type(row["ordinal"]) is not int or row["ordinal"] != ordinal):
        raise AnalysisRefused("invalid stage identity")
    model = row["model"]
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", model)):
        raise AnalysisRefused("invalid model metadata")
    if row["effort"] not in {None, "low", "medium", "high", "xhigh", "max"}:
        raise AnalysisRefused("invalid effort")
    if row["cost"]["currency"] != "USD":
        raise AnalysisRefused("unsupported currency")
    _duration(row["started_at"], row["ended_at"])
    flags = row["termination_flags"]
    if not isinstance(flags, dict) or any(flags.get(key) is not None and type(flags[key]) is not bool for key in FLAGS):
        raise AnalysisRefused("invalid termination signal")
    attempts = [_measurement(row.get(key), integer=True) for key in ("attempt", "provider_attempts")]
    if any(value == 0 for value in attempts if value is not None):
        raise AnalysisRefused("invalid attempt count")
    return {"stage": row["stage"], "kind": row["kind"], "ordinal": ordinal,
            "model": model, "effort": row["effort"], "result": row["result"],
            "cost_usd": _measurement(row["cost"]["amount"]),
            "wall_seconds": _measurement(row["wall_seconds"]),
            "turns": _measurement(row["turns"], integer=True),
            "attempt": attempts[0], "provider_attempts": attempts[1],
            "signals": [key for key in FLAGS if flags.get(key) is True]}


def observation(value: dict, repository: str) -> dict:
    """Validate/project only analytics fields; proof contents never become decisions."""
    if (value["schema"] != "dark-factory/trajectory" or value["schema_version"] != "1.0"
            or value["repository"] != repository or value["authority"] != "observation-only"
            or value["learning_scope"] != "project-local" or value["source_workflow"] != WORKER_PATH
            or value["outcome"] not in OUTCOMES):
        raise AnalysisRefused("unsupported observation scope")
    run, attempt = positive(value["run_id"]), positive(value["run_attempt"])
    if value["trajectory_id"] != f"traj_{run}_{attempt}":
        raise AnalysisRefused("mismatched trajectory identity")
    gaps = _gaps(value["gaps"], "trajectory")
    stages, seen = [], set()
    repeats = 0
    for item in _rows(value["attempts"], 40):
        phase, kernel = item["phase"], item["kernel_run"]
        if phase not in {"dispatch", "merge"} or not isinstance(kernel, str) or not re.fullmatch(
                r"(?:issue-[0-9]+-a[0-9]+-[0-9a-f]{10}|(?:pr|merge|rehead|resume)-[0-9]+-[0-9a-f]{12})", kernel):
            raise AnalysisRefused("invalid kernel attempt")
        if (phase, kernel) in seen:
            raise AnalysisRefused("duplicate kernel attempt")
        seen.add((phase, kernel))
        seen_ordinals, counts = set(), Counter()
        for row in _rows(item["stages"], MAX_STAGES):
            ordinal = positive(row["ordinal"])
            if ordinal in seen_ordinals:
                raise AnalysisRefused("duplicate stage ordinal")
            seen_ordinals.add(ordinal)
            stage = _stage(row, ordinal)
            stages.append({**stage, "phase": phase, "kernel_run": kernel})
            counts[(stage["kind"], stage["stage"])] += 1
        repeats += sum(max(0, count - 1) for count in counts.values())
        gaps.extend(_gaps(item["gaps"], "kernel", phase))
    # Retention is historical metadata, not proof of availability or correctness.
    for retained in _rows(value.get("evidence_retention", []), 2):
        if (retained["authority"] != "observation-only" or retained["proof_reuse_allowed"] is not False
                or retained["phase"] not in {"dispatch", "merge"}):
            raise AnalysisRefused("invalid retention scope")
        gaps.extend(_gaps(retained["gaps"], "retention", retained["phase"]))
        if isinstance(retained.get("index"), dict):
            gaps.extend(_gaps(retained["index"].get("gaps", []), "retention-index", retained["phase"]))
    return {"run_id": run, "run_attempt": attempt, "source_revision": oid(value["source_revision"]),
            "url": f"https://github.com/{repository}/actions/runs/{run}/attempts/{attempt}",
            "workflow_outcome": value["outcome"], "started_at": timestamp(value["started_at"]),
            "ended_at": timestamp(value["ended_at"]),
            "workflow_elapsed_seconds": _duration(value["started_at"], value["ended_at"]),
            "issues": sorted({positive(x) for x in _rows(value["issues"], 100)}),
            "pull_requests": sorted({positive(x) for x in _rows(value["pull_requests"], 100)}),
            "stages": sorted(stages, key=lambda s: (s["phase"], s["kernel_run"], s["ordinal"])),
            "repeated_stage_observations": repeats, "gaps": gaps}


def distribution(values: list[float | int | None]) -> dict:
    known = sorted(value for value in values if value is not None)
    return {"observed": len(known), "missing": len(values) - len(known),
            "sum": round(math.fsum(known), 6) if known else None,
            "p50": known[math.ceil(len(known) * .5) - 1] if known else None,
            "p95": known[math.ceil(len(known) * .95) - 1] if known else None,
            "max": known[-1] if known else None}


def metrics(stages: list[dict]) -> dict:
    agents = [row for row in stages if row["kind"] == "agent"]
    provider = [row["provider_attempts"] for row in agents]
    return {"stage_observations": len(stages), "agent_observations": len(agents),
            "non_ok_observations": sum(row["result"] != "ok" for row in stages),
            "agent_cost_usd": distribution([row["cost_usd"] for row in agents]),
            "stage_seconds": distribution([row["wall_seconds"] for row in stages]),
            "agent_turns": distribution([row["turns"] for row in agents]),
            "repair_invocations": sum(row["stage"] == "repair" for row in agents),
            "additional_provider_attempts_recorded": sum(max(0, n - 1) for n in provider if n is not None),
            "provider_attempt_counts_missing": sum(n is None for n in provider)}


def build_report(records: list[dict], *, repository: str) -> dict:
    """Exclude conflicts and invalid records, preserving coverage and provenance."""
    repository_name(repository)
    _rows(records, MAX_RECORDS)
    candidates, inventory, rejected = defaultdict(dict), [], []
    identities = defaultdict(set)
    duplicates = 0
    for value in records:
        digest = None
        try:
            raw = canonical_bytes(value)
            digest = sha256_value(value)
            inventory.append(digest)
            if isinstance(value, dict) and type(value.get("run_id")) is int and type(value.get("run_attempt")) is int:
                identities[(value["run_id"], value["run_attempt"])].add(digest)
            if len(raw) > MAX_RECORD:
                raise AnalysisRefused("record too large")
            row = observation(value, repository)
            identity = (row["run_id"], row["run_attempt"])
            if digest in candidates[identity]:
                duplicates += 1
            candidates[identity][digest] = row
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            rejected.append({"reason": "invalid-observation", "sha256": digest})
    runs, conflicts = [], []
    for identity, versions in sorted(candidates.items()):
        if len(identities[identity]) != 1:
            conflicts.append({"run_id": identity[0], "run_attempt": identity[1], "sha256": sorted(identities[identity])})
            continue
        digest, row = next(iter(versions.items()))
        runs.append({**row, "input_sha256": digest})
    all_stages = [stage for row in runs for stage in row["stages"]]
    groups = defaultdict(list)
    failures, signals, gaps = [], Counter(), Counter()
    for run in runs:
        for gap in run["gaps"]:
            gaps[(gap["scope"], gap["phase"] or "unspecified", gap["reason"])] += gap["count"]
        for stage in run["stages"]:
            groups[(stage["kind"], stage["stage"], stage["model"] or "unknown", stage["effort"] or "unknown")].append(stage)
            for signal in stage["signals"]:
                signals[signal] += 1
            if stage["result"] != "ok":
                failures.append({"run_id": run["run_id"], "run_attempt": run["run_attempt"], "url": run["url"],
                                 "workflow_outcome": run["workflow_outcome"], **stage})
        run["metrics"] = metrics(run.pop("stages"))
    groups_list = [{"kind": key[0], "stage": key[1], "model": key[2], "effort": key[3], **metrics(rows)}
                   for key, rows in sorted(groups.items())]
    return {"schema": "dark-factory/trajectory-analysis", "schema_version": "1.0",
            "authority": "observation-only", "proof_reuse_allowed": False, "repository": repository,
            "input_set_sha256": sha256_value(sorted(set(inventory))),
            "coverage": {"input_records": len(records), "accepted_attempts": len(runs),
                         "identical_duplicates": duplicates, "rejected_records": len(rejected),
                         "conflicting_identities": len(conflicts),
                         "input_complete": bool(runs) and not rejected and not conflicts,
                         "runs_without_stages": sum(row["metrics"]["stage_observations"] == 0 for row in runs),
                         "runs_with_collection_gaps": sum(bool(row["gaps"]) for row in runs)},
            "workflow_outcomes": dict(sorted(Counter(row["workflow_outcome"] for row in runs).items())),
            "totals": metrics(all_stages),
            "workflow_elapsed_seconds": distribution([row["workflow_elapsed_seconds"] for row in runs]),
            "repeated_stage_observations": sum(row["repeated_stage_observations"] for row in runs),
            "termination_signals": dict(sorted(signals.items())),
            "collection_gaps": [{"scope": k[0], "phase": k[1], "reason": k[2], "count": n}
                                for k, n in sorted(gaps.items())],
            "by_stage_model": groups_list, "non_ok_stages": failures, "runs": runs,
            "rejected": sorted(rejected, key=lambda r: r["sha256"] or ""), "conflicts": conflicts,
            "verified_completions": None, "cost_per_verified_completion_usd": None,
            "human_interventions": None,
            "limitations": ["Input completeness covers supplied records only, not all historical runs.",
                            "Workflow success and historical artifact references do not establish verified completion.",
                            "Cost is recorded agent cost, not a reconciled bill; preflight and unrecorded calls are excluded.",
                            "Stage durations can overlap; workflow elapsed time includes setup and waiting.",
                            "Non-ok stages and termination flags are observations, not root-cause diagnoses.",
                            "Collection gaps can be expected for phases never entered; they are not product failures.",
                            "Completion receipts and operator-intervention events are not present in this input contract."]}
