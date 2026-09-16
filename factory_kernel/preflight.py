"""Frozen comparison policy and conservative strategy selection. Never qualification."""
from __future__ import annotations

from copy import deepcopy
import re

from .canonical import canonical_bytes, sha256_value
from .frontdoor_intent import IntentRefused, _shape, _text, _texts
from .programme import _id, parse_json

SIGNALS = frozenset({"architecture-boundary", "multiple-modules", "new-dependency", "public-api",
                     "performance", "multiple-strategies", "security", "irreversible-migration",
                     "distributed-state", "difficult-rollback"})
DEEP_SIGNALS = frozenset({"security", "irreversible-migration", "distributed-state", "difficult-rollback"})
PLAN_METRICS = frozenset({"planned-files", "new-dependencies", "planned-top-level-areas"})
RATINGS = {"favourable": 0, "mixed": 1, "adverse": 2, "unknown": None}


def bounded(value, limit=100000):
    raw = canonical_bytes(value)
    if len(raw) > limit:
        raise IntentRefused("Preflight input exceeds its bound")
    return parse_json(raw.decode())


def paths(value):
    names = _texts(value)
    if len(set(names)) != len(names):
        raise IntentRefused("duplicate planned path")
    for name in names:
        if (not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,200}", name)
                or name.startswith("/") or any(part in {"", ".", "..", ".git"} for part in name.split("/"))):
            raise IntentRefused("planned paths must be safe repository-relative names")
    return sorted(names)


def compile_policy(raw):
    value = bounded(raw)
    _shape(value, {"question_id", "question", "signals", "criteria", "priorities", "tie_break",
                   "max_candidates", "budget", "direct_reason"})
    _id(value["question_id"])
    _text(value["question"], 2000)
    signals = _texts(value["signals"])
    if len(set(signals)) != len(signals) or not set(signals) <= SIGNALS:
        raise IntentRefused("unknown or duplicate exploration signal")
    criteria = value["criteria"]
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 12:
        raise IntentRefused("Preflight needs 1–12 decision criteria")
    seen = set()
    for criterion in criteria:
        _shape(criterion, {"id", "kind", "question"})
        key = _id(criterion["id"])
        if key in seen or criterion["kind"] not in PLAN_METRICS | {"judgment"}:
            raise IntentRefused("duplicate or unknown criterion")
        seen.add(key)
        _text(criterion["question"], 2000)
    priorities = _texts(value["priorities"])
    if len(set(priorities)) != len(priorities) or not set(priorities) <= seen:
        raise IntentRefused("priority ordering must name distinct registered criteria")
    if value["tie_break"] not in {"retain-tradeoff", "prefer-baseline-if-equal"}:
        raise IntentRefused("unknown tie-break policy")
    if type(value["max_candidates"]) is not int or not 2 <= value["max_candidates"] <= 4:
        raise IntentRefused("light Preflight supports 2–4 candidate slots")
    budget = value["budget"]
    _shape(budget, {"max_calls", "max_usd", "wall_seconds"})
    if (type(budget["max_calls"]) is not int or budget["max_calls"] != 2
            or type(budget["max_usd"]) not in {int, float} or not 0 < budget["max_usd"] <= 2
            or type(budget["wall_seconds"]) is not int or not 1 <= budget["wall_seconds"] <= 676):
        raise IntentRefused("reasoning exploration must fit the two-call, $2, 676-second ceiling")
    if value["direct_reason"] is not None:
        _text(value["direct_reason"], 2000)
    return value


def compile_candidate(raw, *, origin):
    value = bounded(raw)
    _shape(value, {"id", "family", "mechanism", "is_baseline", "planned_files", "new_dependencies",
                   "assumptions", "risks"})
    for key in ("id", "family"):
        _id(value[key])
    _text(value["mechanism"], 2000)
    if type(value["is_baseline"]) is not bool:
        raise IntentRefused("baseline must be explicit")
    value["planned_files"] = paths(value["planned_files"])
    if not value["planned_files"]:
        raise IntentRefused("candidate needs a bounded implementation surface")
    dependencies = _texts(value["new_dependencies"])
    if len(set(dependencies)) != len(dependencies):
        raise IntentRefused("duplicate proposed dependency")
    _texts(value["risks"])
    assumptions = value["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > 20:
        raise IntentRefused("too many candidate assumptions")
    for assumption in assumptions:
        _shape(assumption, {"statement", "revisit_when"})
        _text(assumption["statement"], 2000)
        _text(assumption["revisit_when"], 2000)
    return {**value, "origin": origin}


def compile_pool(generated, users, *, policy):
    if not isinstance(generated, list) or not isinstance(users, list):
        raise IntentRefused("candidates must be lists")
    pool = [compile_candidate(row, origin="user") for row in users]
    pool += [compile_candidate(row, origin="system") for row in generated]
    if not 2 <= len(pool) <= policy["max_candidates"]:
        raise IntentRefused("light Preflight requires its registered candidate bound")
    if len({row["id"] for row in pool}) != len(pool) or sum(row["is_baseline"] for row in pool) != 1:
        raise IntentRefused("candidate identities must be unique with exactly one minimal-change baseline")
    signatures = {sha256_value({"mechanism": " ".join(row["mechanism"].casefold().split()),
                               "paths": row["planned_files"], "dependencies": sorted(row["new_dependencies"])})
                  for row in pool}
    if len(signatures) != len(pool):
        raise IntentRefused("renamed copies do not count as alternative strategies")
    return pool


def plan_facts(candidate, tracked_files):
    """Count declared plan properties; these do not measure future implementation cost."""
    planned = candidate["planned_files"]
    return {"planned-files": len(planned), "new-dependencies": len(candidate["new_dependencies"]),
            "planned-top-level-areas": len({name.split("/")[0] for name in planned}),
            "existing_paths": sorted(set(planned) & set(tracked_files)),
            "proposed_new_paths": sorted(set(planned) - set(tracked_files))}


def plan_signals(pool):
    """Conservative deterministic triggers over declared plans, not a completeness claim."""
    signals = set()
    for candidate in pool:
        if candidate["new_dependencies"]:
            signals.add("new-dependency")
        if len({name.rsplit("/", 1)[0] for name in candidate["planned_files"]}) > 1:
            signals.add("multiple-modules")
        for name in candidate["planned_files"]:
            parts = name.casefold().split("/")
            if parts[0] in {"factory_kernel", "harness", "scripts", ".factory", ".github"} or any(
                    part.startswith(("auth", "security")) for part in parts):
                signals.add("security")
            if any(part.startswith("migration") for part in parts):
                signals.add("irreversible-migration")
            if any(part in {"api", "routes"} for part in parts):
                signals.add("public-api")
    return sorted(signals)


def compile_challenge(raw, *, registration, pool):
    value = bounded(raw)
    _shape(value, {"registration_sha256", "candidates_sha256", "family_groups", "assessments"})
    if value["registration_sha256"] != sha256_value(registration) or value["candidates_sha256"] != sha256_value(pool):
        raise IntentRefused("challenge must bind the frozen policy and exact candidate pool")
    ids = {candidate["id"] for candidate in pool}
    groups = value["family_groups"]
    if not isinstance(groups, list) or not 1 <= len(groups) <= len(pool):
        raise IntentRefused("candidate families must be explicit")
    grouped = []
    for group in groups:
        members = _texts(group)
        if not members:
            raise IntentRefused("empty strategy family")
        grouped.extend(members)
    if set(grouped) != ids or len(grouped) != len(ids):
        raise IntentRefused("each candidate must have exactly one semantic family")
    assessments = value["assessments"]
    if not isinstance(assessments, list) or len(assessments) != len(pool):
        raise IntentRefused("all candidates require equal evaluation")
    constraints = {row["id"] for row in registration["constraints"]}
    judgments = {row["id"] for row in registration["policy"]["criteria"] if row["kind"] == "judgment"}
    seen = set()
    for row in assessments:
        _shape(row, {"candidate_id", "constraints", "judgments", "uncertainties"})
        if row["candidate_id"] not in ids or row["candidate_id"] in seen:
            raise IntentRefused("unknown or duplicate candidate assessment")
        seen.add(row["candidate_id"])
        for field, expected, statuses in (("constraints", constraints, {"appears-met", "conflict", "unknown"}),
                                          ("judgments", judgments, set(RATINGS))):
            entries = row[field]
            if not isinstance(entries, list) or len(entries) != len(expected):
                raise IntentRefused("assessment omitted a frozen obligation")
            names = []
            for entry in entries:
                _shape(entry, {"id", "status", "basis"})
                names.append(entry["id"])
                if entry["status"] not in statuses:
                    raise IntentRefused("unknown reasoning status")
                _text(entry["basis"], 2000)
            if set(names) != expected or len(set(names)) != len(names):
                raise IntentRefused("assessment changes the registered criteria")
        uncertainties = row["uncertainties"]
        if not isinstance(uncertainties, list) or len(uncertainties) > 20:
            raise IntentRefused("uncertainty registry exceeds bound")
        for uncertainty in uncertainties:
            _shape(uncertainty, {"question", "would_change_decision_if", "resolution"})
            for text in uncertainty.values():
                _text(text, 2000)
    return value


def compare(*, registration, pool, challenge, tracked_files):
    """Use only registered criteria. Never turn opinions, unknowns or a tie into proof."""
    challenge = compile_challenge(challenge, registration=registration, pool=pool)
    policy = registration["policy"]
    assessments = {row["candidate_id"]: row for row in challenge["assessments"]}
    rows, vectors, blocked = {}, {}, False
    for candidate in pool:
        key = candidate["id"]
        assessment = assessments[key]
        facts = plan_facts(candidate, tracked_files)
        judgments = {row["id"]: row for row in assessment["judgments"]}
        vector = {criterion["id"]: (RATINGS[judgments[criterion["id"]]["status"]]
                  if criterion["kind"] == "judgment" else facts[criterion["kind"]]) for criterion in policy["criteria"]}
        conflicts = [row["id"] for row in assessment["constraints"] if row["status"] == "conflict"]
        uncertain = (bool(assessment["uncertainties"]) or None in vector.values()
                     or any(row["status"] == "unknown" for row in assessment["constraints"]))
        status = "screened-out" if conflicts else "finalist"
        rows[key] = {"candidate": deepcopy(candidate), "status": status, "plan_observations": facts,
                     "reasoning": deepcopy(assessment), "conflicts": conflicts, "dominated_by": [],
                     "comparison_uncertain": uncertain}
        if not conflicts:
            vectors[key] = vector
            blocked |= uncertain
    has_judgment = any(row["kind"] == "judgment" for row in policy["criteria"])
    # Opinions can inform a provisional preference, but cannot establish Pareto dominance.
    if not blocked and not has_judgment:
        for loser, values in vectors.items():
            for winner, other in vectors.items():
                if all(other[key] <= values[key] for key in values) and any(other[key] < values[key] for key in values):
                    rows[loser]["dominated_by"].append(winner)
            if rows[loser]["dominated_by"]:
                rows[loser]["status"] = "dominated-on-declared-plan"
    frontier = [key for key in vectors if rows[key]["status"] == "finalist"]
    selected, trace = None, []
    status = "needs-evidence" if blocked else "needs-priority"
    if not frontier:
        status = "no-viable-candidate"
    elif len(challenge["family_groups"]) < 2:
        status = "needs-diversity"
    elif not blocked:
        remaining = list(frontier)
        for criterion in policy["priorities"]:
            best = min(vectors[key][criterion] for key in remaining)
            kept = [key for key in remaining if vectors[key][criterion] == best]
            set_aside = [key for key in remaining if key not in kept]
            trace.append({"criterion_id": criterion, "retained": kept, "set_aside": set_aside})
            for key in set_aside:
                rows[key]["not_selected_because"] = {"reason": "registered-priority", "criterion_id": criterion,
                                                    "preferred_candidates": kept}
            remaining = kept
        if len(remaining) == 1:
            selected = remaining[0]
        elif (policy["tie_break"] == "prefer-baseline-if-equal"
              and all(vectors[key] == vectors[remaining[0]] for key in remaining)):
            selected = next((key for key in remaining if rows[key]["candidate"]["is_baseline"]), None)
            if selected:
                for key in remaining:
                    if key != selected:
                        rows[key]["not_selected_because"] = {"reason": "registered-baseline-tie-break"}
        if selected:
            status = "provisional-recommendation"
            rows[selected]["status"] = "recommended-unproven"
    return {"status": status, "selected_candidate": selected, "retained_alternatives": list(rows.values()),
            "pareto_scope": "declared-plan-properties-only", "frontier": frontier,
            "selection_basis": {"priorities": policy["priorities"], "tie_break": policy["tie_break"], "trace": trace},
            "qualification_status": "UNPROVEN", "proof_reuse_allowed": False,
            "search_coverage": "bounded-candidate-pool", "global_optimality": "not-established"}
