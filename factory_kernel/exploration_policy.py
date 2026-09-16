"""Validate exploratory claims and compare explicit evidence vectors, never proof."""
from __future__ import annotations

import math

from .frontdoor_intent import IntentRefused, _shape, _text, _texts
from .programme import _id
from .exploration_records import affected_claims

JUDGMENTS = {"favourable": 0, "mixed": 1, "adverse": 2, "unknown": None}


def number(value):
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1000000000:
        raise IntentRefused("expected a bounded finite nonnegative number")
    return value


def validate_policy(policy):
    _shape(policy, {"criteria", "priorities", "budget", "max_candidates", "max_rounds"})
    criteria = policy["criteria"]
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 10:
        raise IntentRefused("register 1–10 criteria before exploration")
    ids = []
    for criterion in criteria:
        _shape(criterion, {"id", "question", "kind", "unit", "ceiling"})
        if criterion["kind"] not in {"measurement", "judgment"}:
            raise IntentRefused("criterion must distinguish measurement from judgment")
        ids.append(_id(criterion["id"]))
        _text(criterion["question"], 2000)
        _text(criterion["unit"], 100)
        if criterion["ceiling"] is not None:
            number(criterion["ceiling"])
            if criterion["kind"] == "judgment":
                raise IntentRefused("a judgment cannot supply a measured constraint ceiling")
    if len(ids) != len(set(ids)) or sorted(policy["priorities"]) != sorted(ids):
        raise IntentRefused("priorities must order every distinct registered criterion")
    budget = policy["budget"]
    _shape(budget, {"calls", "usd", "probe_units"})
    for name, maximum in (("calls", 8), ("usd", 8), ("probe_units", 10000000)):
        if number(budget[name]) > maximum:
            raise IntentRefused("exploration budget exceeds service bound")
    if type(budget["calls"]) is not int or type(budget["probe_units"]) is not int:
        raise IntentRefused("work and call bounds must be integers")
    for name, maximum in (("max_candidates", 12), ("max_rounds", 8)):
        if type(policy[name]) is not int or not 1 <= policy[name] <= maximum:
            raise IntentRefused("invalid search bound")


def validate_addition(data, state, session, spec):
    _shape(data, {"claims", "candidates"})
    claims, candidates = data["claims"], data["candidates"]
    if not isinstance(claims, list) or len(claims) > 40 or len(state["claims"]) + len(claims) > 400:
        raise IntentRefused("claim graph capacity reached")
    known = {key for key, claim in state["claims"].items() if claim["spec_sha256"] == session["binding"]["spec_sha256"]}
    acceptance = {ac["id"] for req in spec["requirements"] for ac in req["acceptance"]}
    for claim in claims:
        _shape(claim, {"id", "statement", "kind", "depends_on", "acceptance", "revisit_when"})
        key = _id(claim["id"])
        if key in state["claims"] or key in known or claim["kind"] not in {"assumption", "strategy"}:
            raise IntentRefused("duplicate claim or unknown exploratory claim kind")
        for field in ("statement", "revisit_when"):
            _text(claim[field], 2000)
        dependencies = _texts(claim["depends_on"])
        acs = _texts(claim["acceptance"])
        if (len(dependencies) != len(set(dependencies)) or not set(dependencies) <= known
                or not acs or len(acs) != len(set(acs)) or not set(acs) <= acceptance):
            raise IntentRefused("claims require acyclic existing dependencies and approved acceptance links")
        known.add(key)
    if (not isinstance(candidates, list) or not candidates
            or len(candidates) + len(session["candidates"]) > session["policy"]["max_candidates"]):
        raise IntentRefused("candidate bound reached")
    ids = set(session["candidates"])
    criteria = {row["id"]: row for row in session["policy"]["criteria"]}
    for candidate in candidates:
        _shape(candidate, {"id", "family", "mechanism", "baseline", "claim_ids", "trajectory",
                           "predictions", "probe_strategy", "origin"})
        key = _id(candidate["id"])
        if key in ids or type(candidate["baseline"]) is not bool:
            raise IntentRefused("duplicate candidate or invalid baseline")
        ids.add(key)
        _id(candidate["family"])
        _text(candidate["mechanism"], 4000)
        if candidate["origin"] not in {"user", "system"}:
            raise IntentRefused("invalid candidate origin")
        claim_ids = _texts(candidate["claim_ids"])
        if not claim_ids or len(set(claim_ids)) != len(claim_ids) or not set(claim_ids) <= known:
            raise IntentRefused("candidate requires recorded causal claims")
        _shape(candidate["trajectory"], {"implementation", "validation", "failure_repair", "migration_reversal"})
        for text in candidate["trajectory"].values():
            _text(text, 4000)
        _shape(candidate["predictions"], criteria)
        validate_predictions(candidate["predictions"], criteria)
        if candidate["probe_strategy"] not in {None, "linear", "binary", "hash"}:
            raise IntentRefused("unknown registered probe strategy")
    pool = list(session["candidates"].values()) + candidates
    if sum(row["baseline"] for row in pool) != 1:
        raise IntentRefused("retain exactly one current minimal-change baseline")


def validate_predictions(predictions, criteria):
    _shape(predictions, criteria)
    for criterion, prediction in predictions.items():
        if criteria[criterion]["kind"] == "judgment":
            _shape(prediction, {"assessment", "basis"})
            if prediction["assessment"] not in JUDGMENTS:
                raise IntentRefused("unknown qualitative assessment")
        else:
            _shape(prediction, {"low", "high", "basis"})
            if number(prediction["low"]) > number(prediction["high"]):
                raise IntentRefused("prediction interval is reversed")
        _text(prediction["basis"], 2000)


def comparison(state, session):
    invalid = affected_claims(state["claims"], {key for key, row in state["claims"].items()
                                               if row["status"] == "invalidated"})
    challenged = affected_claims(state["claims"], {key for key, row in state["claims"].items()
                                                  if row["status"] == "challenged"})
    criteria = session["policy"]["criteria"]
    rows = {}
    for key, candidate in session["candidates"].items():
        predictions = candidate["predictions"]
        stale_prediction = candidate["predictions_context_identity"] != session["context"]["identity"]
        for assessment in session["assessments"]:
            if assessment["round"] == session["round"] and key in assessment["candidates"]:
                predictions = assessment["candidates"][key]
                stale_prediction = False
        values = {}
        for name, prediction in predictions.items():
            if "assessment" in prediction:
                rank = JUDGMENTS[prediction["assessment"]]
                values[name] = {**prediction, "kind": "judgment", "low": 0 if rank is None else rank,
                                "high": 2 if rank is None else rank}
            else:
                values[name] = {**prediction, "kind": "predicted"}
            if stale_prediction:
                values[name] = {"kind": "stale-prediction", "low": 0, "high": 1000000000,
                                "basis": "Reassess the retained strategy against the changed repository."}
        for observation in session["observations"]:
            if (observation["status"] != "complete" or observation["round"] != session["round"]
                    or observation["context_identity"] != session["context"]["identity"]):
                continue
            for measurement in observation.get("measurements", []):
                if measurement["candidate_id"] == key:
                    values[measurement["criterion_id"]] = {"low": measurement["value"], "high": measurement["value"],
                        "kind": "measured", "basis": observation["receipt_sha256"]}
        rejected = bool(invalid.intersection(candidate["claim_ids"]))
        conflicts = [row["id"] for row in criteria if row["ceiling"] is not None
                     and values[row["id"]]["low"] > row["ceiling"]]
        uncertain = [row["id"] for row in criteria if row["ceiling"] is not None
                     and values[row["id"]]["low"] <= row["ceiling"] < values[row["id"]]["high"]]
        rows[key] = {"values": values, "status": "rejected-assumption" if rejected else
                     "screened-out" if conflicts else "viable", "conflicts": conflicts,
                     "uncertain_constraints": uncertain,
                     "challenged_claims": sorted(challenged.intersection(candidate["claim_ids"]))}
    viable = [key for key, row in rows.items() if row["status"] == "viable"]
    priorities = session["policy"]["priorities"]
    # Midpoints establish only a tentative investigation order, not a dominance claim.
    ordered = sorted(viable, key=lambda key: tuple((rows[key]["values"][name]["low"] +
                                                   rows[key]["values"][name]["high"]) / 2 for name in priorities))
    preferred = ordered[0] if ordered else None
    separated = bool(preferred)
    if preferred:
        for other in ordered[1:]:
            distinguishes = False
            for criterion in priorities:
                left, right = rows[preferred]["values"][criterion], rows[other]["values"][criterion]
                if left["high"] < right["low"]:
                    distinguishes = True
                    break
                if not (left["low"] == left["high"] == right["low"] == right["high"]):
                    break
            separated &= distinguishes
    supported = bool(preferred and separated and not rows[preferred]["uncertain_constraints"]
                     and not rows[preferred]["challenged_claims"])
    if any(row["kind"] == "judgment" for row in criteria):
        supported = False  # Policy may choose a bounded preference; judgments never establish separation as evidence.
    return {"candidates": rows, "preferred": preferred, "sufficient_support": supported,
            "selection_method": "registered-lexicographic-intervals", "qualification_status": "UNPROVEN",
            "global_optimality": "not-established", "unobserved_alternative_outcomes": "unknown"}
