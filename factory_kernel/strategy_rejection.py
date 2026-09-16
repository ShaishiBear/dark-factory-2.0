"""Automatic decisions from preregistered predicates and independently established facts."""
from copy import deepcopy
import subprocess

from .canonical import sha256_value
from .exploration_records import affected_claims
from .factory_feedback import recorded_handoff
from .feedback_observation import observe_feedback
from .frontdoor_intent import IntentRefused, _shape
from .programme import compile_programme
from .strategy_findings import establish


class StrategyRejection:
    def __init__(self, engine, github):
        if github.repository != engine.records.store.repository:
            raise IntentRefused("strategy assessment repository differs from owner scope")
        self.engine, self.github = engine, github

    def assess(self, project, command, *, principal):
        _shape(command["request"], {"outcome_id"})
        engine, store = self.engine, self.engine.records.store
        state, approval = engine.records.read(project, principal)
        session = engine._session(state, approval, command["session_id"], allow_stale=True)
        outcome = state["factory_outcomes"].get(command["request"]["outcome_id"])
        if outcome is None or outcome["session_id"] != session["id"]:
            raise IntentRefused("strategy assessment requires this session's imported outcome")
        receipt = outcome["observation"]["receipt"]
        engine.check_stop()
        fresh = observe_feedback(self.github, {"run_id": receipt["run_id"], "attempt": receipt["run_attempt"],
            "pr": receipt["pr"], "item_id": receipt["item_id"]})
        programme = compile_programme(fresh.pop("programme_input"), repository=store.repository)
        if fresh != outcome["observation"] or programme.spec != approval["spec"]:
            raise IntentRefused("factory outcome or approved scope changed before strategy assessment")
        with store._locked(project) as path:
            if recorded_handoff(store._read(path), programme, session["id"]) != outcome["handoff_sha256"]:
                raise IntentRefused("strategy assessment has no exact preregistered handoff")
        rules = programme.strategy.get("rejection_rules", [])
        # Reconstruct from the admitted historical strategy; current model text and caller
        # payloads cannot retrofit a predicate to an outcome that has already happened.
        if any(rule not in session["rejection_rules"] for rule in rules):
            raise IntentRefused("programme rejection rule was not registered before selection")
        report = None
        if rules:
            try:
                report = establish(self.github, rules, {c["id"]: c for c in programme.strategy["claims"]},
                    expected_revision=receipt["base_sha"], check_stop=engine.check_stop)
            except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError):
                # Missing authority/source evidence is explicitly unresolved; it is never
                # evidence that the strategy failed. Stop is rechecked before any append.
                report = None
        engine.check_stop()
        contradicted = sorted({row["claim_id"] for row in report["findings"]
                               if row["status"] == "contradicted"}) if report else []
        decision = "not-applicable" if not rules else "reconsider" if contradicted else "unresolved" if report is None or any(
            row["status"] == "unresolved" for row in report["findings"]) else "no-contradiction-established"

        def transition(current, current_approval):
            engine._session(current, current_approval, session["id"], allow_stale=True)
            if current_approval["spec"] != programme.spec:
                raise IntentRefused("strategy assessment approval changed")
            if len(current["strategy_assessments"]) >= 80:
                raise IntentRefused("strategy assessment capacity reached")
            previous = [row for row in current["strategy_assessments"].values() if row["outcome_id"] == outcome["id"]]
            if previous and previous[-1]["decision"] != "unresolved":
                raise IntentRefused("this outcome already has a resolved strategy assessment")
            if any(current["claims"][key]["status"] != "active" for key in contradicted):
                raise IntentRefused("assumption already challenged or invalidated; reconcile its recorded decision")
            affected = affected_claims(current["claims"], set(contradicted))
            frontier = [{"session_id": key, "recommendation_sha256": sha256_value(row["recommendations"][-1]),
                         "claim_ids": sorted(affected.intersection(row["recommendations"][-1]["claim_ids"]))}
                for key, row in sorted(current["sessions"].items()) if row["recommendations"]
                and affected.intersection(row["recommendations"][-1]["claim_ids"])]
            data = {"outcome_id": outcome["id"], "session_id": session["id"], "decision": decision,
                "programme_sha256": programme.sha256, "registered_rules": deepcopy(rules),
                "independent_findings": report, "invalidated_claim_ids": contradicted,
                "affected_claim_ids": sorted(affected), "frontier": frontier,
                "supersedes": previous[-1]["id"] if previous else None,
                "failure_cause": "unresolved", "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}
            return "strategy-assessed", {**data, "id": sha256_value(data)}

        def replay_guard(current, current_approval, payload):
            engine._session(current, current_approval, session["id"], allow_stale=True)
            if (current_approval["spec"] != programme.spec or payload["data"]["independent_findings"] != report
                    or payload["data"]["decision"] != decision):
                raise IntentRefused("strategy finding changed since recorded assessment")

        state, payload, _ = engine.records.append(project, principal, command, transition, replay_guard)
        return {"project_version": state["project_version"], "assessment": deepcopy(payload["data"])}


def after_import(engine, github, project, result, *, principal):
    """A crash between import and assessment leaves a visible, explicitly recoverable gap.

    No paid effect is retried. Resolved assessments are never inferred from a receipt alone.
    """
    state, _ = engine.records.read(project, principal)
    outcome = result["outcome"]
    previous = [row for row in state["strategy_assessments"].values() if row["outcome_id"] == outcome["id"]]
    if previous:
        return {**result, "assessment": deepcopy(previous[-1])}
    if not state["sessions"][outcome["session_id"]]["rejection_rules"]:
        return result
    try:
        assessed = StrategyRejection(engine, github).assess(project, {
            "idempotency_key": "strategy-assess-" + outcome["id"],
            "expected_project_version": state["project_version"], "session_id": outcome["session_id"],
            "request": {"outcome_id": outcome["id"]}}, principal=principal)
        return {**result, **assessed}
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        return {**result, "assessment": None, "assessment_status": "pending-explicit-recovery"}
