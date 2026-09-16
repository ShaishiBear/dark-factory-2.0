"""Adaptive, owner-scoped exploration before programme commitment. Never qualification."""
from __future__ import annotations

from copy import deepcopy

from .canonical import sha256_value
from .frontdoor_intent import IntentRefused, _shape, _text, _texts
from .frontdoor_programme import prepare_programme
from .programme import compile_programme
from .exploration_policy import comparison, validate_addition, validate_policy, validate_predictions
from .exploration_probe import METRICS, run_probe, validate_probe
from .exploration_records import ExplorationRecords


class Exploration:
    def __init__(self, store, context, *, check_stop, app_login):
        self.records = ExplorationRecords(store)
        self.context, self.check_stop, self.app_login = context, check_stop, app_login

    def _context(self):
        value = deepcopy(self.context())
        _shape(value, {"commit", "files", "policies", "coverage", "proof_status", "identity"})
        if value["identity"] != sha256_value({key: val for key, val in value.items() if key != "identity"}):
            raise IntentRefused("repository analysis identity mismatch")
        return value

    def _session(self, state, approval, key, *, allow_stale=False):
        session = state["sessions"].get(key)
        if session is None or session["binding"]["spec_sha256"] != approval["spec_sha256"]:
            raise IntentRefused("session does not bind current approved scope")
        if session["binding"]["approval_version"] != approval["project_version"]:
            raise IntentRefused("session approval has been superseded")
        if not allow_stale and session["context"] != self._context():
            raise IntentRefused("repository changed; reconsider before further work")
        return session

    def _append(self, project, principal, command, action, transition):
        self.check_stop()
        command = deepcopy(command)
        command["request"] = {"action": action, "data": command["request"]}
        def replay_guard(state, approval, recorded):
            session = self._session(state, approval, command["session_id"],
                          allow_stale=action in {"observe-claim", "reopen", "abandon-pending"})
            if action == "handoff" and (session["status"] != "recommended" or
                    recorded["data"]["recommendation_sha256"] != sha256_value(session["recommendations"][-1])):
                raise IntentRefused("recorded handoff no longer has a current recommendation")
        return self.records.append(project, principal, command, transition, replay_guard)

    def open(self, project, command, *, principal):
        request = deepcopy(command["request"])

        def transition(state, approval):
            _shape(request, {"question", "parent_session", "policy"})
            _text(request["question"], 4000)
            validate_policy(request["policy"])
            if command["session_id"] in state["sessions"] or len(state["sessions"]) >= 40:
                raise IntentRefused("session already exists or project search capacity reached")
            if request["parent_session"] is not None:
                self._session(state, approval, request["parent_session"])
            previous = state["budgets"].get(approval["spec_sha256"])
            if previous and previous["limits"] != request["policy"]["budget"]:
                raise IntentRefused("all questions share the frozen approved-scope budget")
            return "opened", {**request, "context": self._context(), "binding": {
                "spec_sha256": approval["spec_sha256"], "approval_version": approval["project_version"]}}

        return self._append(project, principal, command, "open", transition)[0]

    def add_candidates(self, project, command, *, principal):
        request = deepcopy(command["request"])

        def transition(state, approval):
            session = self._session(state, approval, command["session_id"])
            if session["status"] != "exploring":
                raise IntentRefused("reopen the question before adding alternatives")
            validate_addition(request, state, session, approval["spec"])
            return "candidates-added", request

        return self._append(project, principal, command, "add-candidates", transition)[0]

    def inspect(self, project, session_id, *, principal):
        state, approval = self.records.read(project, principal)
        session = self._session(state, approval, session_id)
        result = comparison(state, session)
        budget = state["budgets"][approval["spec_sha256"]]
        return {"project_version": state["project_version"], "session": deepcopy(session),
                "comparison": result, "budget": budget,
                "next_action": "recommend-or-investigate" if result["sufficient_support"] else
                               "investigate-decision-changing-uncertainty" if result["preferred"] else
                               "generate-different-strategy"}

    def assess(self, project, command, *, principal):
        """Retain revised predictions/judgments; they can never overwrite measurements."""
        request = deepcopy(command["request"])

        def transition(state, approval):
            session = self._session(state, approval, command["session_id"])
            _shape(request, {"candidates", "basis"})
            _text(request["basis"], 4000)
            if session["status"] != "exploring" or not session["candidates"]:
                raise IntentRefused("assess an existing open candidate pool")
            _shape(request["candidates"], session["candidates"])
            criteria = {row["id"]: row for row in session["policy"]["criteria"]}
            for predictions in request["candidates"].values():
                validate_predictions(predictions, criteria)
            return "assessed", {**request, "round": session["round"], "kind": "reasoned-assessment",
                "context_identity": session["context"]["identity"], "qualification_status": "UNPROVEN"}

        return self._append(project, principal, command, "assess", transition)[0]

    def experiment(self, project, command, *, principal):
        request = deepcopy(command["request"])

        def reserve(state, approval):
            session = self._session(state, approval, command["session_id"])
            if session["status"] != "exploring":
                raise IntentRefused("experiments require an open question")
            _shape(request, {"probe", "targets", "question", "would_change_decision_if", "claim_ids"})
            for field in ("question", "would_change_decision_if"):
                _text(request[field], 4000)
            claims = _texts(request["claim_ids"])
            if not claims or not set(claims) <= state["claims"].keys():
                raise IntentRefused("experiment must investigate recorded claims")
            units = validate_probe(request["probe"])
            criteria = {row["id"]: row for row in session["policy"]["criteria"]}
            if not isinstance(request["targets"], list) or not 1 <= len(request["targets"]) <= 30:
                raise IntentRefused("experiment needs bounded pre-registered measurement targets")
            seen = set()
            for target in request["targets"]:
                _shape(target, {"candidate_id", "criterion_id", "metric", "falsifies_claim"})
                candidate = session["candidates"].get(target["candidate_id"])
                criterion = criteria.get(target["criterion_id"])
                identity = (target["candidate_id"], target["criterion_id"])
                if (candidate is None or criterion is None or criterion["kind"] != "measurement" or target["metric"] not in METRICS
                        or criterion["unit"] != target["metric"] or identity in seen
                        or candidate["probe_strategy"] not in request["probe"]["strategies"]
                        or not set(candidate["claim_ids"]).intersection(claims)
                        or (target["falsifies_claim"] is not None and (criterion["ceiling"] is None
                            or target["falsifies_claim"] not in candidate["claim_ids"]
                            or target["falsifies_claim"] not in claims))):
                    raise IntentRefused("measurement target or unit is not bound to this experiment")
                seen.add(identity)
            budget = state["budgets"][approval["spec_sha256"]]
            if budget["probe_units"] + units > budget["limits"]["probe_units"]:
                raise IntentRefused("shared cumulative experiment budget exhausted")
            identity = sha256_value({"session": session["id"], "round": session["round"], "request": request})
            if identity in session["reservations"]:
                raise IntentRefused("this experiment was already reserved; do not repeat uncertain work")
            return "reserved", {"id": identity, "calls": 0, "usd": 0, "probe_units": units,
                "request": request, "round": session["round"], "context_identity": session["context"]["identity"]}

        state, event, created = self._append(project, principal, command, "experiment", reserve)
        if not created:
            return state
        reservation = event["data"]
        # No model-supplied runner or callback is accepted. Only the fixed data-only runner.
        try:
            receipt = run_probe(request["probe"], check_stop=self.check_stop)
            status, failure = "complete", None
        except Exception as exc:
            receipt, status, failure = None, "failed", type(exc).__name__
        completion = {"idempotency_key": "probe-result-" + reservation["id"],
                      "expected_project_version": state["project_version"],
                      "session_id": command["session_id"], "request": {"reservation": reservation["id"]}}

        def finish(current, approval):
            session = self._session(current, approval, command["session_id"])
            pending = session["reservations"][reservation["id"]]
            if pending["status"] != "pending" or pending["round"] != session["round"]:
                raise IntentRefused("experiment result is stale or already recorded")
            measurements = []
            claim_observations = []
            if receipt is not None:
                ceilings = {row["id"]: row["ceiling"] for row in session["policy"]["criteria"]}
                for target in request["targets"]:
                    candidate = session["candidates"][target["candidate_id"]]
                    value = receipt["results"][candidate["probe_strategy"]][target["metric"]]
                    measurements.append({**target, "value": value})
                    if target["falsifies_claim"] is not None:
                        claim_observations.append({"claim_id": target["falsifies_claim"],
                            "outcome": "contradicted" if value > ceilings[target["criterion_id"]] else "supported-in-probe",
                            "receipt_sha256": sha256_value(receipt), "context_identity": session["context"]["identity"],
                            "qualification_status": "UNPROVEN", "scope": "finite-declared-workload"})
            return "observed", {"reservation_id": reservation["id"], "round": session["round"],
                "context_identity": session["context"]["identity"], "status": status, "failure": failure,
                "receipt": receipt, "receipt_sha256": sha256_value(receipt), "measurements": measurements,
                "claim_observations": claim_observations}

        # A stopped/stale/concurrent completion remains pending and charged. Never rerun it.
        return self._append(project, principal, completion, "experiment-result", finish)[0]

    def recommend(self, project, command, *, principal):
        request = deepcopy(command["request"])

        def transition(state, approval):
            session = self._session(state, approval, command["session_id"])
            _shape(request, {"stop_reason", "rationale", "remaining_uncertainty", "next_useful_experiment"})
            _text(request["rationale"], 4000)
            _texts(request["remaining_uncertainty"])
            _text(request["next_useful_experiment"], 4000)
            if session["status"] != "exploring" or len({c["family"] for c in session["candidates"].values()}) < 2:
                raise IntentRefused("recommendation requires an open question and different strategy families")
            if any(row["status"] == "pending" for row in session["reservations"].values()):
                raise IntentRefused("uncertain reserved work cannot be hidden by a recommendation")
            result = comparison(state, session)
            if result["preferred"] is None:
                raise IntentRefused("no viable strategy; reconsider the candidate pool")
            if request["stop_reason"] == "sufficient-support":
                if not result["sufficient_support"]:
                    raise IntentRefused("evidence intervals still leave a decision-changing tradeoff")
            elif request["stop_reason"] == "bounded-decision":
                if not request["remaining_uncertainty"]:
                    raise IntentRefused("bounded stopping must disclose remaining uncertainty")
            else:
                raise IntentRefused("unknown search stopping rule")
            candidate = session["candidates"][result["preferred"]]
            return "recommended", {**request, "candidate_id": candidate["id"], "claim_ids": candidate["claim_ids"],
                "round": session["round"], "comparison": result, "context_identity": session["context"]["identity"],
                "candidate_sha256": sha256_value(candidate), "proof_reuse_allowed": False}

        return self._append(project, principal, command, "recommend", transition)[0]

    def abandon_pending(self, project, command, *, principal):
        """Close uncertain work without retrying it or releasing any reserved budget."""
        request = deepcopy(command["request"])

        def transition(state, approval):
            session = self._session(state, approval, command["session_id"], allow_stale=True)
            _shape(request, {"reservation_id", "reason"})
            _text(request["reason"], 4000)
            reservation = session["reservations"].get(request["reservation_id"])
            if reservation is None or reservation["status"] != "pending":
                raise IntentRefused("only pending work can be abandoned")
            return "observed", {"reservation_id": request["reservation_id"], "status": "abandoned",
                "round": reservation["round"], "context_identity": reservation["context_identity"],
                "reason": request["reason"], "reported_usd": None, "measurements": [],
                "receipt_sha256": sha256_value(request), "qualification_status": "UNPROVEN"}

        return self._append(project, principal, command, "abandon-pending", transition)[0]

    def observe_claim(self, project, command, *, principal):
        request = deepcopy(command["request"])

        def transition(state, approval):
            self._session(state, approval, command["session_id"], allow_stale=True)
            _shape(request, {"claim_id", "status", "observation", "source"})
            if request["claim_id"] not in state["claims"] or request["status"] not in {"challenged", "invalidated"}:
                raise IntentRefused("unknown claim or invalid observation status")
            if state["claims"][request["claim_id"]]["status"] == "invalidated":
                raise IntentRefused("invalidated claims are immutable; record a revised claim")
            for field in ("observation", "source"):
                _text(request[field], 4000)
            return "claim-observed", {**request, "evidence_class": "owner-reported", "qualification_status": "UNPROVEN"}

        return self._append(project, principal, command, "observe-claim", transition)[0]

    def reopen(self, project, command, *, principal):
        request = deepcopy(command["request"])

        def transition(state, approval):
            session = self._session(state, approval, command["session_id"], allow_stale=True)
            _shape(request, {"reason"})
            _text(request["reason"], 4000)
            if session["round"] >= session["policy"]["max_rounds"]:
                raise IntentRefused("reconsideration round budget exhausted")
            if any(row["status"] == "pending" for row in session["reservations"].values()):
                raise IntentRefused("pending effects require reconciliation before reconsideration")
            return "reopened", {**request, "context": self._context()}

        return self._append(project, principal, command, "reopen", transition)[0]

    def handoff(self, project, command, *, principal):
        request = deepcopy(command["request"])

        def transition(state, approval):
            session = self._session(state, approval, command["session_id"])
            _shape(request, {"proposal"})
            if session["status"] != "recommended":
                raise IntentRefused("a current recommendation is required")
            recommendation = session["recommendations"][-1]
            value = {"version": "1.0", "spec": deepcopy(approval["spec"]),
                     "proposal": request["proposal"], "app_login": self.app_login}
            programme = compile_programme(value, repository=self.records.store.repository)
            return "handoff", {"schema": "dark-factory/exploration-handoff", "schema_version": "1.0",
                "input": value, "input_sha256": sha256_value(value), "programme_sha256": programme.sha256,
                "recommendation_sha256": sha256_value(recommendation), "candidate_id": recommendation["candidate_id"],
                "strategy": deepcopy(session["candidates"][recommendation["candidate_id"]]),
                "claim_ids": recommendation["claim_ids"], "context_identity": session["context"]["identity"],
                "binding": session["binding"], "qualification_status": "UNPROVEN", "proof_reuse_allowed": False,
                "activation": "requires-normal-publication-admission-and-fresh-qualification"}

        return self._append(project, principal, command, "handoff", transition)[0]

    def prepare_handoff(self, project, session_id, *, expected_project_version, principal):
        """Revalidate stored recommendation and use the existing review/admission input seam.

        This exports a proposal, never a publication capability. The publisher must still
        obtain its normal exact-input owner consent and recheck currency before effects.
        """
        self.check_stop()
        state, approval = self.records.read(project, principal)
        if type(expected_project_version) is not int or expected_project_version != state["project_version"]:
            raise IntentRefused("stale project version before handoff preparation")
        session = self._session(state, approval, session_id)
        if session["status"] != "recommended" or not session["handoffs"]:
            raise IntentRefused("no current stored exploration handoff")
        handoff = session["handoffs"][-1]
        if handoff["recommendation_sha256"] != sha256_value(session["recommendations"][-1]):
            raise IntentRefused("handoff recommendation was superseded")
        review = prepare_programme(self.records.store, project, {
            "expected_project_version": expected_project_version, "approval_version": approval["project_version"],
            "spec_sha256": approval["spec_sha256"], "proposal": deepcopy(handoff["input"]["proposal"])},
            principal=principal, app_login=self.app_login)
        if review["input_sha256"] != handoff["input_sha256"] or review["programme_sha256"] != handoff["programme_sha256"]:
            raise IntentRefused("stored handoff does not match the current programme compiler")
        self.check_stop()
        if self._context() != session["context"]:
            raise IntentRefused("repository changed during handoff preparation")
        return {**review, "exploration": {"session_id": session_id, "handoff_sha256": sha256_value(handoff),
            "recommendation_sha256": handoff["recommendation_sha256"], "strategy": deepcopy(handoff["strategy"]),
            "claim_ids": handoff["claim_ids"], "qualification_status": "UNPROVEN", "proof_reuse_allowed": False,
            "strategy_enforcement": "advisory-sidecar-not-consumed-by-current-factory-workers"}}
