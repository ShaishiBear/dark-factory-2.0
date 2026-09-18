"""Calibration of frozen forecasts against their matched outcomes (SPECIFICATION 11.1, C13, WP11).

This module joins the forecasts the exploration ledger froze before each result with the
outcomes it recorded, and reports what the join supports: interval coverage and absolute
error for measured criteria, Brier scores for probability forecasts of binary outcomes (none
are recorded by the ledger today, and the report says so), failed-experiment counts and
unknown-outcome rates (refusal and repair counts are not recorded by the ledger, and the report
says so), grouped by task family, method version and model, with sample counts and missingness
kept. It decides nothing: the report is `report-only`, it never admits a lesson, ranks a
candidate or turns an opinion into a measurement. `lessons.evaluate` may read the report's
`admission_observations`; the protected policy decides what they mean.

Strictness the join keeps:

- A forecast is joined only if it was in force when the observation was recorded (the events
  before that observation reconstruct it exactly); an outcome naming any other forecast is
  reported as `unjoined-forecast`, never scored. A later assessment cannot leak into an earlier
  outcome, and a tampered outcome cannot borrow a forecast.
- One trajectory counts once: the same (session, receipt, candidate, criterion) recorded twice
  is one sample, and the copy is counted as dropped.
- Missing cost stays unknown: an observation whose cost was not reported and whose reservation
  did make paid calls contributes to `unknown_count`, never to the known sum as zero.
- An unbuilt or unmeasured candidate has no actual: it appears under `unmeasured_candidates`
  and in no metric.
- Evaluation cohorts are preregistered configuration (`harness/experiments/learning_protocol.json`),
  separated by project (and session) rather than invented here; a trajectory that appears in
  two cohorts marks the split contaminated, and negative results are retained, not filtered.

Arithmetic is exact (fractions) and rendered as `numerator/denominator` strings plus a decimal
approximation, so no rounding step can be mistaken for a significance judgement.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .canonical import canonical_bytes, sha256_value
from .exploration_records import OPERATION, projection
from .frontdoor_intent import IntentStore
from .predictions import OUTCOMES, frozen_predictions

SCHEMA = "dark-factory/calibration-report"
SCHEMA_VERSION = "1.0"
PROTOCOL_SCHEMA = "dark-factory/learning-protocol"
PROTOCOL_SCHEMA_VERSION = "1.0"
GROUPING_FIELDS = ("project", "task_family", "method_version", "model")
PROTOCOL_FIELDS = {"schema", "schema_version", "protocol_id", "version", "cohorts", "grouping", "note"}
COHORT_FIELDS = {"projects", "sessions"}
MAX_EVENTS = 10000
UNRECORDED_MODEL = "unrecorded"  # the ledger records no model name for an assessment or registration


class CalibrationRefused(ValueError):
    pass


# ---- preregistered protocol -------------------------------------------------------------------


def parse_protocol(raw: Any) -> dict:
    """The preregistered evaluation configuration, validated for shape only. Cohorts name
    projects (and optionally sessions); they must not overlap. Nothing here is evidence."""
    if not isinstance(raw, Mapping) or set(raw) != PROTOCOL_FIELDS:
        raise CalibrationRefused("learning protocol must carry exactly the protocol fields")
    if raw["schema"] != PROTOCOL_SCHEMA or raw["schema_version"] != PROTOCOL_SCHEMA_VERSION:
        raise CalibrationRefused("unknown learning protocol schema")
    for key in ("protocol_id", "version", "note"):
        if not isinstance(raw[key], str) or not raw[key]:
            raise CalibrationRefused(f"learning protocol {key} must be a non-empty string")
    grouping = raw["grouping"]
    if not isinstance(grouping, list) or not grouping or len(set(grouping)) != len(grouping) \
            or not set(grouping) <= set(GROUPING_FIELDS):
        raise CalibrationRefused("grouping must list distinct fields from " + ", ".join(GROUPING_FIELDS))
    cohorts = raw["cohorts"]
    if not isinstance(cohorts, Mapping) or not cohorts:
        raise CalibrationRefused("learning protocol needs at least one cohort")
    seen_projects: dict[str, str] = {}
    seen_sessions: dict[tuple[str, str], str] = {}
    for name, cohort in cohorts.items():
        if not isinstance(name, str) or not name or not isinstance(cohort, Mapping) or not set(cohort) <= COHORT_FIELDS:
            raise CalibrationRefused("a cohort names projects and optionally sessions, nothing else")
        projects = cohort.get("projects", [])
        sessions = cohort.get("sessions", [])
        if not isinstance(projects, list) or not all(isinstance(p, str) and p for p in projects):
            raise CalibrationRefused("cohort projects must be project ids")
        if not isinstance(sessions, list) or not all(isinstance(s, list) and len(s) == 2 and all(isinstance(x, str) and x for x in s)
                                                       for s in sessions):
            raise CalibrationRefused("cohort sessions must be [project, session_id] pairs")
        for project in projects:
            if project in seen_projects and seen_projects[project] != name:
                raise CalibrationRefused(f"project {project} is assigned to two cohorts")
            seen_projects[project] = name
        for project, session in sessions:
            key = (project, session)
            if key in seen_sessions and seen_sessions[key] != name:
                raise CalibrationRefused(f"session {project}/{session} is assigned to two cohorts")
            seen_sessions[key] = name
    # Checked after every cohort is collected, so the verdict does not depend on the order the
    # cohorts are written in: a session cannot sit in one cohort while its project sits in another.
    for (project, session), name in seen_sessions.items():
        if project in seen_projects and seen_projects[project] != name:
            raise CalibrationRefused(f"session {project}/{session} lies in a project assigned to another cohort")
    record = {k: raw[k] for k in sorted(PROTOCOL_FIELDS)}
    record["cohort_digest"] = sha256_value({"cohorts": raw["cohorts"], "grouping": grouping, "protocol_id": raw["protocol_id"],
                                            "version": raw["version"]})
    return record


def load_protocol(path: str | Path) -> dict:
    try:
        return parse_protocol(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeError) as exc:
        raise CalibrationRefused("learning protocol cannot be read") from exc


def cohort_of(protocol: Mapping[str, Any], project: str, session_id: str) -> str | None:
    """The one cohort a session belongs to: an explicit session assignment first, else its
    project's assignment, else none (an unassigned trajectory is reported, never scored)."""
    for name, cohort in protocol["cohorts"].items():
        if [project, session_id] in cohort.get("sessions", []):
            return name
    for name, cohort in protocol["cohorts"].items():
        if project in cohort.get("projects", []):
            return name
    return None


# ---- the strict join ----------------------------------------------------------------------------


def _cost(reservation: Mapping[str, Any], observation: Mapping[str, Any]) -> int | float | None:
    """Known only when the observation reported it, or when the reservation made no paid call
    (a local runner with `calls == 0` and `usd == 0` recorded). Anything else is unknown."""
    reported = observation.get("reported_usd")
    if isinstance(reported, (int, float)) and not isinstance(reported, bool):
        return reported
    if reservation.get("calls") == 0 and reservation.get("usd") == 0:
        return 0
    return None


def join_outcomes(events: Sequence[Mapping[str, Any]], *, project: str) -> dict:
    """Every recorded prediction outcome joined to the forecast in force when it was recorded.
    Returns the joined rows plus what could not be joined, deduplicated, and the candidates
    that were never measured."""
    if not isinstance(events, Sequence) or len(events) > MAX_EVENTS:
        raise CalibrationRefused("events must be a bounded sequence")
    rows: list[dict] = []
    unjoined: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    duplicates = 0
    for index, event in enumerate(events):
        payload = event["command"]["payload"]
        if event["command"]["operation"] != OPERATION or payload["kind"] != "observed":
            continue
        # The forecasts in force at this moment: the projection of exactly the events before
        # this observation. A forecast made later does not exist here, so it cannot be joined.
        state = projection(list(events[:index]))
        session_id = payload["session_id"]
        session = state["sessions"][session_id]
        in_force = {record["identity"]: record for record in frozen_predictions(session)}
        data = payload["data"]
        reservation = session["reservations"].get(data["reservation_id"], {})
        method_version = str(reservation.get("request", {}).get("probe", {}).get("kind", "unknown"))
        cost = _cost(reservation, data)
        for outcome in data.get("prediction_outcomes", []):
            subject = outcome["subject"]
            key = (session_id, str(data.get("receipt_sha256")), subject["candidate_id"], subject["criterion_id"])
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            candidate = session["candidates"].get(subject["candidate_id"], {})
            row = {"project": project, "session_id": session_id, "round": data["round"], "event_index": index,
                   "recorded_at": event.get("created_at"), "receipt_sha256": data.get("receipt_sha256"),
                   "candidate_id": subject["candidate_id"], "criterion_id": subject["criterion_id"],
                   "task_family": str(candidate.get("family", "unknown")), "method_version": method_version,
                   "model": UNRECORDED_MODEL, "status": data.get("status"), "failure": data.get("failure"),
                   "outcome": outcome["outcome"], "distance": outcome.get("distance"),
                   "actual": outcome.get("actual"), "cost_usd": cost}
            identity = outcome.get("prediction_identity")
            if identity is None:
                # An actual nothing forecast: the ledger recorded it as unknown; keep it as such.
                rows.append({**row, "forecast": None})
                continue
            record = in_force.get(identity)
            if record is None:
                unjoined.append({**row, "prediction_identity": identity, "reason": "unjoined-forecast",
                                 "detail": "no forecast with this identity was in force when the outcome was recorded"})
                continue
            rows.append({**row, "forecast": {"identity": identity, "kind": record["kind"], "source": record["source"],
                                             "round": record["round"], "low": record.get("low"), "high": record.get("high"),
                                             "data_cutoff": record["data_cutoff"]}})
    final = projection(list(events))
    measured = {(r["session_id"], r["candidate_id"]) for r in rows} | {(u["session_id"], u["candidate_id"]) for u in unjoined}
    unmeasured = sorted([{"session_id": sid, "candidate_id": cid} for sid, session in final["sessions"].items()
                         for cid in session["candidates"] if (sid, cid) not in measured],
                        key=lambda r: (r["session_id"], r["candidate_id"]))
    failed = [{"session_id": sid, "reservation_id": rid, "status": obs.get("status"), "failure": obs.get("failure")}
              for sid, session in final["sessions"].items() for obs in session["observations"]
              for rid in [obs.get("reservation_id")] if obs.get("status") != "complete"]
    return {"rows": rows, "unjoined": unjoined, "duplicates_dropped": duplicates, "unmeasured_candidates": unmeasured,
            "failed_observations": failed}


# ---- exact arithmetic ---------------------------------------------------------------------------


def _ratio(numerator: int | Fraction, denominator: int) -> dict | None:
    if denominator == 0:
        return None
    value = Fraction(numerator) / Fraction(denominator)
    return {"exact": f"{value.numerator}/{value.denominator}", "decimal": float(value)}


def brier_score(pairs: Sequence[tuple[Any, Any]]) -> dict | None:
    """Mean squared error of probability forecasts against binary outcomes, exact. None when
    there are no pairs. A probability outside [0, 1] or a non-binary outcome is refused."""
    total = Fraction(0)
    count = 0
    for probability, outcome in pairs:
        if isinstance(probability, bool) or not isinstance(probability, (int, float, Fraction)) or not 0 <= probability <= 1:
            raise CalibrationRefused("a forecast probability must lie in [0, 1]")
        if isinstance(outcome, bool) or outcome not in (0, 1):
            raise CalibrationRefused("a binary outcome must be 0 or 1")
        total += (Fraction(probability) - int(outcome)) ** 2
        count += 1
    if count == 0:
        return None
    return {"samples": count, "brier": _ratio(total, count)}


def binary_forecasts(rows: Sequence[Mapping[str, Any]]) -> list[tuple[Any, Any]]:
    """Probability forecasts of binary outcomes. The exploration ledger freezes intervals and
    judgments only, so this is empty until a family records probabilities; it is derived, not
    invented."""
    pairs = []
    for row in rows:
        forecast = row.get("forecast") or {}
        if forecast.get("kind") == "probability" and row.get("actual", {}).get("value") in (0, 1):
            pairs.append((forecast["probability"], row["actual"]["value"]))
    return pairs


def interval_metrics(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Coverage over compared intervals (within + outside) and absolute error over the same
    rows (within contributes 0). Not-comparable, inapplicable and unknown are counted, not scored."""
    counts = Counter(row["outcome"] for row in rows)
    compared = counts["within"] + counts["outside"]
    distances = [Fraction(row["distance"]) for row in rows if row["outcome"] in ("within", "outside")
                 and isinstance(row.get("distance"), (int, float)) and not isinstance(row["distance"], bool)]
    return {"compared": compared, "within": counts["within"], "outside": counts["outside"],
            "coverage": _ratio(counts["within"], compared),
            "absolute_error": {"samples": len(distances),
                               "mean": _ratio(sum(distances, Fraction(0)), len(distances)) if distances else None,
                               "max": float(max(distances)) if distances else None}}


def _group_key(row: Mapping[str, Any], grouping: Sequence[str]) -> tuple:
    return tuple(str(row.get(field, "unknown")) for field in grouping)


def _group_report(rows: Sequence[Mapping[str, Any]]) -> dict:
    counts = Counter(row["outcome"] for row in rows)
    known = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    return {"samples": len(rows), "outcomes": {name: counts.get(name, 0) for name in OUTCOMES},
            "unknown_outcome_rate": _ratio(counts.get("unknown", 0), len(rows)),
            "interval": interval_metrics(rows), "binary": brier_score(binary_forecasts(rows)),
            "cost_usd": {"known_samples": len(known), "known_sum": float(sum(Fraction(str(c)) for c in known)) if known else 0.0,
                         "unknown_count": len(rows) - len(known)},
            "sessions": sorted({row["session_id"] for row in rows}),
            "failed_observations": sum(1 for row in rows if row["status"] != "complete")}


# ---- the report ---------------------------------------------------------------------------------


def calibration_report(joins: Mapping[str, Mapping[str, Any]], *, protocol: Mapping[str, Any]) -> dict:
    """One report over the joins of one or more projects (`{project: join_outcomes(...)}`),
    grouped as the protocol preregistered and split into its cohorts. Retains every negative
    result and every unjoined or unmeasured item, and exposes the coverage observations a
    lesson-admission policy can decide on."""
    grouping = list(protocol["grouping"])
    rows: list[dict] = []
    unjoined: list[dict] = []
    duplicates = 0
    unmeasured: list[dict] = []
    failed: list[dict] = []
    for project, join in sorted(joins.items()):
        for row in join["rows"]:
            rows.append({**row, "cohort": cohort_of(protocol, project, row["session_id"])})
        unjoined.extend({**u, "project": project} for u in join["unjoined"])
        duplicates += join["duplicates_dropped"]
        unmeasured.extend({**u, "project": project} for u in join["unmeasured_candidates"])
        failed.extend({**f, "project": project} for f in join["failed_observations"])
    groups = {}
    for row in rows:
        groups.setdefault(_group_key(row, grouping), []).append(row)
    group_reports = [{"key": dict(zip(grouping, key)), **_group_report(members)} for key, members in sorted(groups.items())]
    cohorts = {}
    receipts_by_cohort: dict[str, set] = {}
    for name in protocol["cohorts"]:
        members = [row for row in rows if row["cohort"] == name]
        receipts_by_cohort[name] = {row["receipt_sha256"] for row in members}
        cohorts[name] = {**_group_report(members), "families_covered": len({row["task_family"] for row in members}),
                         "projects": sorted({row["project"] for row in members})}
    # A copy of the same trajectory (same receipt) inside two cohorts is contamination: the
    # split cannot separate development from evaluation.
    names = list(receipts_by_cohort)
    contaminated = any(receipts_by_cohort[a] & receipts_by_cohort[b] for i, a in enumerate(names) for b in names[i + 1:])
    unassigned = sorted({(row["project"], row["session_id"]) for row in rows if row["cohort"] is None})
    evaluation = cohorts.get("evaluation")
    report = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "authority": "report-only",
        "protocol": {"protocol_id": protocol["protocol_id"], "version": protocol["version"], "cohort_digest": protocol["cohort_digest"],
                     "grouping": grouping},
        "samples": len(rows), "duplicates_dropped": duplicates, "unjoined_forecasts": unjoined,
        "unmeasured_candidates": unmeasured, "failed_observations": failed,
        "unassigned_sessions": [{"project": p, "session_id": s} for p, s in unassigned],
        "groups": group_reports, "cohorts": cohorts, "contaminated": contaminated,
        "binary_note": "the exploration ledger freezes intervals and judgments; no probability forecast is recorded, so Brier scores have no samples",
        "refusals": "not-recorded-in-the-exploration-ledger",
        "repairs": "not-recorded-in-the-exploration-ledger",
        "admission_observations": {
            "cohort_digest": protocol["cohort_digest"], "contaminated": contaminated,
            "families_covered": evaluation["families_covered"] if evaluation else 0,
            "samples": evaluation["samples"] if evaluation else 0,
            "note": "coverage of the evaluation cohort only; the protected policy decides whether it is adequate",
        },
        "limitations": [
            "observational: the joined outcomes describe the search policy that ran, not a treatment effect",
            "no model name is recorded for a forecast; the model group is 'unrecorded'",
            "cohorts separate by project and session, not by wall-clock time",
            "a retained negative result is a sample, never a filter",
        ],
    }
    report["report_sha256"] = sha256_value({k: v for k, v in report.items()})
    return report


def markdown(report: Mapping[str, Any]) -> str:
    lines = [f"# Calibration report ({report['authority']})", "",
             f"Protocol `{report['protocol']['protocol_id']}` v{report['protocol']['version']}, cohort digest `{report['protocol']['cohort_digest'][:12]}`.",
             f"Samples {report['samples']}, duplicates dropped {report['duplicates_dropped']}, unjoined forecasts {len(report['unjoined_forecasts'])}, "
             f"unmeasured candidates {len(report['unmeasured_candidates'])}, contaminated: {report['contaminated']}.", ""]
    for group in report["groups"]:
        key = ", ".join(f"{k}={v}" for k, v in group["key"].items())
        coverage = group["interval"]["coverage"]
        lines.append(f"- {key}: samples {group['samples']}, compared {group['interval']['compared']}, coverage "
                     f"{coverage['exact'] if coverage else 'n/a'}, unknown outcomes {group['outcomes']['unknown']}, "
                     f"cost unknown {group['cost_usd']['unknown_count']}")
    lines.append("")
    for name, cohort in report["cohorts"].items():
        lines.append(f"- cohort {name}: samples {cohort['samples']}, families {cohort['families_covered']}, projects {', '.join(cohort['projects']) or 'none'}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibration report over an intent store's exploration ledger (report-only).")
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    try:
        protocol = load_protocol(args.protocol)
        if not args.store.is_dir():
            raise CalibrationRefused("store directory does not exist")
        store = IntentStore(args.store, repository=args.repository, owner="calibration-report")
        joins = {}
        for project in args.project:
            with store._locked(project) as path:
                joins[project] = join_outcomes(store._read(path), project=project)
        report = calibration_report(joins, protocol=protocol)
        sys.stdout.write(canonical_bytes(report).decode("utf-8") if args.format == "json" else markdown(report))
        return 0
    except (CalibrationRefused, ValueError, OSError, KeyError, TypeError) as exc:
        sys.stderr.write(f"CALIBRATION_REFUSED: {type(exc).__name__}\n")
        return 2


__all__ = ["CalibrationRefused", "GROUPING_FIELDS", "PROTOCOL_SCHEMA", "SCHEMA", "SCHEMA_VERSION", "binary_forecasts",
           "brier_score", "calibration_report", "cohort_of", "interval_metrics", "join_outcomes", "load_protocol", "main",
           "markdown", "parse_protocol"]


if __name__ == "__main__":
    raise SystemExit(main())
