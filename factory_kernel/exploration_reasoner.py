"""Bounded adaptive reasoning over canonical exploration state; models propose only."""
from __future__ import annotations

import json
import tempfile
from dataclasses import replace

from .agents import AgentRequest
from .canonical import canonical_bytes, sha256_value
from .frontdoor_intent import IntentRefused, _shape, _text
from .programme import parse_json
from .exploration_policy import comparison, number
from .worker_policy import allowed_tools, effort, max_turns, max_budget_usd, stage_timeout_seconds

ACTION_SHAPES = {
    "assess": {"candidates": {"every-existing-candidate-id": {
        "every-registered-criterion-id": {"low": 0, "high": 1, "basis": "revised reasoning"}}},
        "basis": "Challenge all strategies against the same constraints and repository observations."},
    "add-candidates": {
        "claims": [{"id": "unique-claim", "statement": "falsifiable assumption", "kind": "assumption",
                    "depends_on": [], "acceptance": ["existing-acceptance-id"], "revisit_when": "contrary observation"}],
        "candidates": [{"id": "unique-candidate", "family": "causal-family", "mechanism": "causal approach",
            "baseline": True, "claim_ids": ["unique-claim"], "origin": "system", "probe_strategy": None,
            "trajectory": {"implementation": "steps and interfaces", "validation": "proof obligations",
                "failure_repair": "likely failures and repairs", "migration_reversal": "longer-term consequences"},
            "predictions": {"registered-criterion-id": {"low": 0, "high": 1, "basis": "reasoning, not measurement"}}}]},
    "experiment": {"probe": {"kind": "lookup-workload-v1", "strategies": ["linear", "binary", "hash"],
                            "keys": [1, 2], "queries": [1, 3]},
        "targets": [{"candidate_id": "existing-candidate", "criterion_id": "existing-criterion", "metric": "comparisons",
                     "falsifies_claim": "claim-refuted-if-the-registered-ceiling-is-exceeded"}],
        "question": "decision-changing unknown", "would_change_decision_if": "predeclared interpretation",
        "claim_ids": ["existing-claim"]},
    "recommend": {"stop_reason": "sufficient-support", "rationale": "evidence and tradeoffs",
        "remaining_uncertainty": ["unmeasured properties"], "next_useful_experiment": "what further spend could establish"},
    "handoff": {"proposal": {"spec_sha256": "exact approved spec hash",
        "items": [{"id": "bounded-item", "acceptance": ["existing-acceptance-id"], "blocked_by": []}]}},
    "stop": {"reason": "No available useful bounded action; explain missing evidence or genuine owner tradeoff."},
}


class ExplorationReasoner:
    def __init__(self, exploration, provider):
        self.engine, self.provider = exploration, provider
        config = getattr(provider, "config", None)
        if config is not None and config.transient_retries != 0:
            raise IntentRefused("adaptive exploration requires automatic provider retries disabled")

    def advance(self, project, command, *, principal):
        """One durable paid reservation, at most one bounded call, then one checked action.

        Replay returns state without calling or applying an action again. A crash after
        reservation cannot be automatically retried. All child questions share this budget.
        """
        _shape(command["request"], {"purpose", "max_usd"})
        _text(command["request"]["purpose"], 4000)
        cap = number(command["request"]["max_usd"])
        if not 0 < cap <= 1:
            raise IntentRefused("reasoning invocation must reserve at most one dollar")
        engine = self.engine

        def reserve(state, approval):
            session = engine._session(state, approval, command["session_id"])
            if session["status"] not in {"exploring", "recommended"}:
                raise IntentRefused("resolve reconsideration before reasoning")
            if (session["handoffs"] and session["recommendations"] and
                    session["handoffs"][-1]["recommendation_sha256"] == sha256_value(session["recommendations"][-1])):
                raise IntentRefused("this recommendation already has a programme handoff")
            if any(row["status"] == "pending" for row in session["reservations"].values()):
                raise IntentRefused("reconcile uncertain work before another reasoning call")
            budget = state["budgets"][approval["spec_sha256"]]
            if budget["uncertain"]:
                raise IntentRefused("reported spend is uncertain; no further paid work")
            if budget["calls"] + 1 > budget["limits"]["calls"] or budget["usd"] + cap > budget["limits"]["usd"]:
                raise IntentRefused("shared cumulative reasoning budget exhausted")
            identity = sha256_value({"session": session["id"], "command": command})
            # Original private intent wording and unrelated sessions never enter a worker prompt.
            # Never recursively embed old reservations (which contain their own prompt
            # snapshots). Keep the current decision view plus bounded recent evidence.
            view = {name: session[name] for name in ("id", "question", "parent_session", "policy",
                                                     "binding", "candidates", "round", "status")}
            context = session["context"]
            view["context"] = {name: context[name] for name in ("commit", "identity", "files", "coverage")}
            view["context"]["policies"] = {name: {"sha256": row["sha256"], "excerpt": row["text"][:12000],
                "coverage": "complete" if len(row["text"]) <= 12000 else "prefix-only"}
                for name, row in context["policies"].items()}
            view["observations"] = [{key: value for key, value in row.items() if key not in {"proposal"}}
                                    for row in session["observations"][-12:]]
            view["assessments"] = session["assessments"][-1:]
            view["recommendations"] = session["recommendations"][-1:]
            needed = {key for candidate in session["candidates"].values() for key in candidate["claim_ids"]}
            frontier = list(needed)
            while frontier:
                key = frontier.pop()
                for dependency in state["claims"][key]["depends_on"]:
                    if dependency not in needed:
                        needed.add(dependency)
                        frontier.append(dependency)
            payload = {"spec": approval["spec"], "spec_sha256": approval["spec_sha256"],
                       "session": view, "comparison": comparison(state, session),
                       "claims": {key: state["claims"][key] for key in sorted(needed)}, "budget": budget}
            if len(canonical_bytes(payload)) > 100000:
                raise IntentRefused("reasoning context exceeds bound")
            return "reserved", {"id": identity, "calls": 1, "usd": cap, "probe_units": 0,
                "round": session["round"], "context_identity": session["context"]["identity"], "payload": payload}

        state, event, created = engine._append(project, principal, command, "reason", reserve)
        if not created:
            return {"state": state, "status": "already-recorded"}
        reservation = event["data"]
        output, cost, failure = None, None, None
        try:
            engine.check_stop()
            prompt = ("Choose the next useful investigation, not just the prettiest plan. Return exactly "
                "{action, request, reason}. All output is UNPROVEN. Preserve approved constraints and non-goals. "
                "Use genuinely different causal families, exactly one baseline, explicit assumptions and "
                "implementation/validation/repair/migration trajectories. Predictions are uncalibrated intervals. "
                "Criteria and cumulative budgets are frozen. A judgment criterion uses {assessment,basis}, "
                "where assessment is favourable, mixed, adverse or unknown; it is never measurement or proof. "
                "Policy excerpts may be incomplete; source identities bind the full retained policies. "
                "Do not assume a missing excerpt grants permission; report a material policy evidence gap. "
                "Do not invent measurements. Investigate only "
                "uncertainties that could change the decision. The only executable experiment currently available "
                "is lookup-workload-v1 on integer data, measuring comparisons, build_items, retained_items, matches. "
                "Do not use it to claim production latency or probe unrelated architectures. If it cannot answer "
                "the material uncertainty, stop and state the missing capability. Units must match criteria. "
                "All candidates must meet approved scope through later independent factory qualification. "
                "A bounded-decision stop may retain uncertainty with explicit rationale; it cannot waive proof. "
                "After a current recommendation, partition approved acceptance into a programme via handoff. "
                "No approval, qualification, policy change, shell command or external effect is an available action.\n"
                + json.dumps({"action_shapes": ACTION_SHAPES, "context": reservation["payload"]}))
            with tempfile.TemporaryDirectory(prefix="factory-exploration-reasoner-") as directory:
                role = ("preflight-challenger" if reservation["payload"]["session"]["candidates"]
                        and reservation["payload"]["session"]["status"] == "exploring" else "preflight-proposer")
                request = AgentRequest(role=role, prompt=prompt, cwd=directory, allowed_tools=allowed_tools(role),
                    environment={}, effort=effort(role), max_turns=max_turns(role),
                    max_budget_usd=max_budget_usd(role), timeout_seconds=stage_timeout_seconds(role))
                request = replace(request, max_turns=min(5, request.max_turns),
                                  max_budget_usd=min(cap, request.max_budget_usd),
                                  timeout_seconds=min(338, request.timeout_seconds))
                result = self.provider.run(request)
            cost = number(result.cost_usd)
            if cost > cap:
                raise IntentRefused("provider exceeded the reserved cost")
            raw = canonical_bytes(result.structured_output) if result.structured_output is not None else result.content.encode()
            if len(raw) > 50000:
                raise IntentRefused("reasoning output exceeds bound")
            output = parse_json(raw.decode())
            _shape(output, {"action", "request", "reason"})
            if output["action"] not in ACTION_SHAPES:
                raise IntentRefused("model requested an unavailable action")
            _text(output["reason"], 4000)
            if output["action"] == "add-candidates" and any(
                    row.get("origin") != "system" for row in output["request"].get("candidates", [])):
                raise IntentRefused("a model cannot attribute its candidate to the owner")
        except Exception as exc:
            failure = type(exc).__name__
        completion = {"idempotency_key": "reason-result-" + reservation["id"],
            "expected_project_version": state["project_version"], "session_id": command["session_id"],
            "request": {"reservation": reservation["id"]}}

        def finish(current, approval):
            session = engine._session(current, approval, command["session_id"])
            if session["reservations"][reservation["id"]]["status"] != "pending":
                raise IntentRefused("reasoning result already recorded")
            return "observed", {"reservation_id": reservation["id"], "round": session["round"],
                "context_identity": session["context"]["identity"], "status": "failed" if failure else "complete",
                "failure": failure, "reported_usd": cost, "proposal": output,
                "receipt_sha256": sha256_value(output), "measurements": []}

        state = engine._append(project, principal, completion, "reason-result", finish)[0]
        if failure:
            return {"state": state, "status": "reasoning-failed", "failure": failure}
        if output["action"] == "stop":
            _shape(output["request"], {"reason"})
            _text(output["request"]["reason"], 4000)
            return {"state": state, "status": "needs-evidence-or-owner-tradeoff", "reason": output["request"]["reason"]}
        action = {"idempotency_key": "reason-action-" + reservation["id"],
            "expected_project_version": state["project_version"], "session_id": command["session_id"],
            "request": output["request"]}
        methods = {"add-candidates": engine.add_candidates, "assess": engine.assess, "experiment": engine.experiment,
                   "recommend": engine.recommend, "handoff": engine.handoff}
        try:
            state = methods[output["action"]](project, action, principal=principal)
        except (IntentRefused, ValueError, TypeError, KeyError) as exc:
            return {"state": state, "status": "proposal-refused", "failure": str(exc)}
        return {"state": state, "status": "advanced", "action": output["action"]}

    def run(self, project, session_id, *, principal, max_steps=4, max_usd_per_call=1):
        if type(max_steps) is not int or not 1 <= max_steps <= 8:
            raise IntentRefused("adaptive loop requires an explicit bounded step count")
        result = None
        for _ in range(max_steps):
            state, _approval = self.engine.records.read(project, principal)
            command = {"idempotency_key": f"adaptive-{session_id}-{state['project_version']}",
                "expected_project_version": state["project_version"], "session_id": session_id,
                "request": {"purpose": "Resolve the next decision-changing uncertainty within budget.",
                            "max_usd": max_usd_per_call}}
            result = self.advance(project, command, principal=principal)
            if result["status"] != "advanced" or result.get("action") == "handoff":
                break
        return result
