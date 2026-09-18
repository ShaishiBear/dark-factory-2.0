"""Predictions frozen before results (SPECIFICATION 8.3, WP08).

A prediction is what a candidate's reasoning said a criterion would come to, recorded with the
data cutoff it was made under (the repository context identity), its source (the candidate's
registration or a round's reasoned assessment), the measurement contract it can be matched
against (the criterion's unit) and the horizon it applies to. It is uncalibrated and says so.
Later actuals are matched by exact subject (session, candidate, criterion) and by measurement
contract; a judgment is never compared to a number, a different unit is never compared, a
changed context makes the prediction inapplicable, and a missing actual stays unknown. Nothing
here scores a model, ranks a candidate or converts an opinion into a measurement: it keeps the
forecast and the outcome side by side so calibration can be studied later (WP11).
"""
from __future__ import annotations

from typing import Any, Mapping

from .canonical import sha256_value
from .frontdoor_intent import IntentRefused

SCHEMA = "dark-factory/prediction"
SCHEMA_VERSION = "1.0"
OUTCOMES = ("within", "outside", "not-comparable", "inapplicable", "unknown")


def freeze_prediction(*, session_id: str, round_number: int, candidate_id: str, criterion: Mapping[str, Any],
                      prediction: Mapping[str, Any], context_identity: str, source: str, applicable: bool = True,
                      inapplicable_reason: str | None = None) -> dict:
    """One frozen forecast. `criterion` is the registered criterion row; `prediction` is the
    candidate's per-criterion prediction (`{low, high, basis}` or `{assessment, basis}`)."""
    if not isinstance(criterion, Mapping) or not isinstance(prediction, Mapping):
        raise IntentRefused("a prediction freezes a registered criterion and a candidate's forecast")
    if source not in ("candidate-registration", "round-assessment"):
        raise IntentRefused("unknown prediction source")
    if "assessment" in prediction:
        kind = "judgment"
        body = {"assessment": prediction["assessment"]}
    elif "low" in prediction and "high" in prediction:
        kind = "interval"
        body = {"low": prediction["low"], "high": prediction["high"]}
    else:
        raise IntentRefused("a prediction is an interval or a judgment")
    record = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "subject": {"session_id": str(session_id), "candidate_id": str(candidate_id), "criterion_id": str(criterion["id"])},
        "kind": kind, **body, "basis": str(prediction.get("basis", "")),
        "measurement_contract": {"criterion_kind": criterion.get("kind"), "unit": criterion.get("unit"), "ceiling": criterion.get("ceiling")},
        "data_cutoff": str(context_identity), "source": source, "round": int(round_number),
        "horizon": "measurements-of-this-session-under-the-same-context",
        "applicability": {"context_identity": str(context_identity), "applicable": bool(applicable), "reason": inapplicable_reason},
        "calibration": "uncalibrated-judgment" if kind == "judgment" else "uncalibrated-interval",
        "authority": "forecast-record-only",
    }
    record["identity"] = sha256_value({k: v for k, v in record.items()})
    return record


def frozen_predictions(session: Mapping[str, Any]) -> list[dict]:
    """The prediction in force for every candidate and criterion at the session's current round:
    the round's reasoned assessment when one exists, else the candidate's registration, which
    is inapplicable when the repository context changed since it was made (the same rule the
    comparison applies)."""
    criteria = {row["id"]: row for row in session["policy"]["criteria"]}
    current = session["context"]["identity"]
    records = []
    for key, candidate in session["candidates"].items():
        predictions = candidate["predictions"]
        source = "candidate-registration"
        # A registration's data cutoff is the context it was made under, which the records keep as
        # `predictions_context_identity`; a round's assessment is made under the current context.
        made_under = candidate.get("predictions_context_identity") or current
        for assessment in session.get("assessments", ()):
            if assessment["round"] == session["round"] and key in assessment["candidates"]:
                predictions = assessment["candidates"][key]
                source = "round-assessment"
                made_under = current
        stale = made_under != current
        for criterion_id, prediction in predictions.items():
            criterion = criteria.get(criterion_id)
            if criterion is None:
                continue
            records.append(freeze_prediction(session_id=session["id"], round_number=session["round"], candidate_id=key,
                                             criterion=criterion, prediction=prediction, context_identity=made_under,
                                             source=source, applicable=not stale,
                                             inapplicable_reason="repository-context-changed-since-the-forecast" if stale else None))
    return records


def match_actual(record: Mapping[str, Any], *, value: Any, unit: str, context_identity: str, receipt_sha256: str) -> dict:
    """Compare one frozen prediction with one measured actual. The outcome names what can be
    said and nothing more: `within` / `outside` for an interval under the same contract and
    context, `not-comparable` for a judgment or a different unit, `inapplicable` when the
    context changed, `unknown` when the value is missing."""
    if not isinstance(record, Mapping) or record.get("schema") != SCHEMA:
        raise IntentRefused("not a prediction record")
    actual = {"value": value, "unit": unit, "receipt_sha256": receipt_sha256, "context_identity": context_identity}
    base = {"prediction_identity": record["identity"], "subject": dict(record["subject"]), "actual": actual}
    if value is None:
        return {**base, "outcome": "unknown", "detail": "no measured value"}
    if not record["applicability"]["applicable"] or record["applicability"]["context_identity"] != context_identity:
        return {**base, "outcome": "inapplicable", "detail": record["applicability"].get("reason") or "context differs from the forecast's data cutoff"}
    if record["kind"] != "interval":
        return {**base, "outcome": "not-comparable", "detail": "a judgment is not a number"}
    if record["measurement_contract"]["unit"] != unit:
        return {**base, "outcome": "not-comparable", "detail": "measured unit differs from the forecast's contract"}
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return {**base, "outcome": "not-comparable", "detail": "actual is not a number"}
    low, high = record["low"], record["high"]
    if low <= value <= high:
        return {**base, "outcome": "within", "distance": 0}
    return {**base, "outcome": "outside", "distance": min(abs(value - low), abs(value - high))}


def outcomes_for(session: Mapping[str, Any], *, measurements: list, context_identity: str, receipt_sha256: str) -> list[dict]:
    """Match every measurement of one observation against the prediction in force for its
    candidate and criterion. A measurement with no frozen prediction is recorded as `unknown`
    with no prediction identity: it is an actual nothing forecast."""
    by_subject = {(r["subject"]["candidate_id"], r["subject"]["criterion_id"]): r for r in frozen_predictions(session)}
    criteria = {row["id"]: row for row in session["policy"]["criteria"]}
    outcomes = []
    for measurement in measurements:
        key = (measurement["candidate_id"], measurement["criterion_id"])
        record = by_subject.get(key)
        unit = criteria[measurement["criterion_id"]]["unit"] if measurement["criterion_id"] in criteria else measurement.get("metric")
        if record is None:
            outcomes.append({"prediction_identity": None, "subject": {"session_id": session["id"], "candidate_id": key[0], "criterion_id": key[1]},
                             "actual": {"value": measurement.get("value"), "unit": unit, "receipt_sha256": receipt_sha256,
                                        "context_identity": context_identity},
                             "outcome": "unknown", "detail": "no frozen prediction for this subject"})
            continue
        outcomes.append(match_actual(record, value=measurement.get("value"), unit=unit, context_identity=context_identity,
                                     receipt_sha256=receipt_sha256))
    return outcomes


__all__ = ["OUTCOMES", "SCHEMA", "SCHEMA_VERSION", "freeze_prediction", "frozen_predictions", "match_actual", "outcomes_for"]
