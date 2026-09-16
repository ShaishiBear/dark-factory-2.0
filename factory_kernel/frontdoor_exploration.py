"""Single-host bounded adaptive jobs over the canonical exploration event log."""
from __future__ import annotations

from copy import deepcopy
import threading

from .canonical import sha256_value
from .exploration import Exploration
from .exploration_policy import comparison, number
from .exploration_reasoner import ExplorationReasoner
from .exploration_records import approved_scope, projection
from .exploration_repository import inspect_protected_repository
from .frontdoor_control import stop_status
from .frontdoor_intent import IntentRefused, _shape

DEFAULT_POLICY = {
    "criteria": [
        {"id": "fit", "question": "How well does this meet approved outcomes and future needs?",
         "kind": "judgment", "unit": "qualitative", "ceiling": None},
        {"id": "maintenance", "question": "How simple is it to maintain, change and reverse?",
         "kind": "judgment", "unit": "qualitative", "ceiling": None},
        {"id": "delivery", "question": "How much implementation and validation risk remains?",
         "kind": "judgment", "unit": "qualitative", "ceiling": None}],
    "priorities": ["fit", "maintenance", "delivery"],
    "budget": {"calls": 8, "usd": 8, "probe_units": 1000000},
    "max_candidates": 8, "max_rounds": 4,
}


def require_clear_stop(github):
    if stop_status(github) != {"state": "clear", "issues": []}:
        raise IntentRefused("adaptive exploration requires an observed clear stop")


class FrontDoorExploration:
    def __init__(self, store, provider, context, *, check_stop, app_login):
        self.engine = Exploration(store, context, check_stop=check_stop, app_login=app_login)
        self.provider = provider
        self._lock = threading.RLock()
        self._threads = {}

    @classmethod
    def protected(cls, store, provider, github, paths, *, app_login):
        if github.repository != store.repository or not paths or len(paths) > 40:
            raise IntentRefused("hosted exploration needs bounded source paths in its configured repository")
        selected = tuple(paths)
        stop = lambda: require_clear_stop(github)
        return cls(store, provider, lambda: inspect_protected_repository(github, list(selected), check_stop=stop),
                   check_stop=stop, app_login=app_login)

    def snapshot(self, project, *, principal):
        records = self.engine.records
        records._owner(principal)
        with records.store._locked(project) as path:
            events = records.store._read(path)
            state = projection(events)
            try:
                approval = approved_scope(records.store, events)
            except IntentRefused:
                approval = None
        sessions = [{**deepcopy(row), "comparison": comparison(state, row),
                     "handoff_current": bool(row["status"] == "recommended" and row["handoffs"]
                         and row["handoffs"][-1]["recommendation_sha256"] == sha256_value(row["recommendations"][-1]))}
                    for row in state["sessions"].values()
                    if approval and row["binding"]["approval_version"] == approval["project_version"]]
        with self._lock:
            runs = [{**deepcopy(row), "observation": "running" if key in self._threads else "interrupted"
                     if row["status"] == "running" else row["status"]}
                    for key, row in state["adaptive_runs"].items()]
        if approval is None:
            return {"ready": False, "project_version": state["project_version"], "sessions": [], "runs": runs,
                    "reason": "Approve the current scope before exploring strategies."}
        budget = state["budgets"].get(approval["spec_sha256"])
        policy = deepcopy(DEFAULT_POLICY)
        if budget:
            policy["budget"] = deepcopy(budget["limits"])
        return {"ready": True, "project_version": state["project_version"], "sessions": sessions,
                "runs": runs, "budget": deepcopy(budget), "default_policy": policy,
                "factory_outcomes": list(state["factory_outcomes"].values()),
                "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}

    def open(self, project, command, *, principal):
        # The owner reviews the complete frozen policy; no model can change it after results.
        self.engine.open(project, command, principal=principal)
        return self.snapshot(project, principal=principal)

    def start(self, project, command, *, principal):
        request = deepcopy(command["request"])
        _shape(request, {"max_steps", "max_usd_per_call"})
        if type(request["max_steps"]) is not int or not 1 <= request["max_steps"] <= 8:
            raise IntentRefused("adaptive job requires 1–8 bounded steps")
        if not 0 < number(request["max_usd_per_call"]) <= 1:
            raise IntentRefused("adaptive job may reserve at most one dollar per call")
        run_id = "adaptive-" + sha256_value({"project": project, "command": command})[:32]

        def reserve(state, approval):
            session = self.engine._session(state, approval, command["session_id"])
            if any(row["status"] == "running" for row in state["adaptive_runs"].values()):
                raise IntentRefused("reconcile the existing adaptive job before starting another")
            if len(state["adaptive_runs"]) >= 80:
                raise IntentRefused("adaptive job history capacity reached")
            if session["status"] not in {"exploring", "recommended"}:
                raise IntentRefused("reopen the affected question before reasoning")
            if (session["status"] == "recommended" and session["handoffs"]
                    and session["handoffs"][-1]["recommendation_sha256"] == sha256_value(session["recommendations"][-1])):
                raise IntentRefused("strategy is already ready for publication review")
            if any(row["status"] == "pending" for row in session["reservations"].values()):
                raise IntentRefused("reconcile pending work before starting another job")
            budget = state["budgets"][approval["spec_sha256"]]
            if (budget["uncertain"] or budget["calls"] >= budget["limits"]["calls"]
                    or budget["usd"] + request["max_usd_per_call"] > budget["limits"]["usd"]):
                raise IntentRefused("shared reasoning budget is exhausted or uncertain")
            return "adaptive-run-started", {"id": run_id, "session_id": session["id"],
                "binding": deepcopy(session["binding"]), **request, "status": "running"}

        with self._lock:
            state, _, created = self.engine._append(project, principal, command, "start-adaptive-run", reserve)
            if created:
                thread = threading.Thread(target=self._run, args=(project, run_id, principal), daemon=True)
                self._threads[run_id] = thread
                try:
                    thread.start()
                except RuntimeError:
                    self._threads.pop(run_id, None)  # Canonical reservation remains visibly interrupted.
                    raise IntentRefused("adaptive job could not start; reconcile its recorded reservation") from None
        return {"run_id": run_id, "project_version": state["project_version"], "state": "recorded"}

    def _current_run(self, project, run_id, principal):
        state, approval = self.engine.records.read(project, principal)
        run = state["adaptive_runs"][run_id]
        if run["status"] != "running" or run["binding"]["approval_version"] != approval["project_version"]:
            raise IntentRefused("adaptive job was closed or its approval changed")
        return state, run

    def _run(self, project, run_id, principal):
        completed, outcome, reason = 0, "failed", "Adaptive work stopped before producing a usable result."
        service = self
        class GuardedProvider:
            # A reservation is charged before this final read; closing a job cannot refund it.
            config = getattr(service.provider, "config", None)

            def run(self, request):
                service._current_run(project, run_id, principal)
                return service.provider.run(request)

        try:
            reasoner = ExplorationReasoner(self.engine, GuardedProvider())
            _, run = self._current_run(project, run_id, principal)
            for step in range(run["max_steps"]):
                state, run = self._current_run(project, run_id, principal)
                result = reasoner.advance(project, {"idempotency_key": f"{run_id}-step-{step}",
                    "expected_project_version": state["project_version"], "session_id": run["session_id"],
                    "request": {"purpose": "Investigate the next decision-changing uncertainty.",
                                "max_usd": run["max_usd_per_call"]}}, principal=principal)
                completed += 1
                outcome = result["status"]
                reason = result.get("reason", result.get("failure", "Bounded adaptive step completed."))
                if outcome != "advanced" or result.get("action") == "handoff":
                    if result.get("action") == "handoff":
                        outcome, reason = "handoff-ready", "Unproven strategy is ready for separate publication review."
                    break
            else:
                outcome, reason = "step-limit", "Requested steps complete; remaining shared budget was preserved."
        except Exception as exc:
            outcome = "failed"
            reason = "Adaptive job stopped: " + type(exc).__name__
        finally:
            try:
                self._finish(project, run_id, principal, outcome, reason, completed)
            except Exception:
                pass  # Preserve the durable interrupted record; never retry provider work.
            finally:
                with self._lock:
                    self._threads.pop(run_id, None)

    def _finish(self, project, run_id, principal, outcome, reason, completed):
        # Closing an operational job must remain possible after scope/stop changes. This
        # transition cannot mutate claims, release spend, dispatch work or approve anything.
        records = self.engine.records
        for _ in range(8):
            with records.store._locked(project) as path:
                state = projection(records.store._read(path))
            run = state["adaptive_runs"][run_id]
            if run["status"] != "running":
                return
            command = {"idempotency_key": run_id + "-finish", "expected_project_version": state["project_version"],
                       "session_id": run["session_id"], "request": {"run_id": run_id}}
            def transition(current, approval):
                if current["adaptive_runs"][run_id]["status"] != "running":
                    raise IntentRefused("adaptive job already closed")
                return "adaptive-run-finished", {"id": run_id, "status": outcome,
                    "reason": str(reason)[:4000], "steps_completed": completed}
            try:
                records.append(project, principal, command, transition, lambda *_: None)
                return
            except IntentRefused:
                continue
        # Remains visible as interrupted if approval/store concurrency prevents terminal append.

    def recover(self, project, command, *, principal):
        _shape(command["request"], {"run_id"})
        run_id = command["request"]["run_id"]
        if not isinstance(run_id, str):
            raise IntentRefused("adaptive job identifier must be text")
        with self._lock:
            self.engine.records._owner(principal)
            if run_id in self._threads:
                raise IntentRefused("adaptive job is still running; use factory stop to halt further work")
            def transition(state, approval):
                run = state["adaptive_runs"].get(run_id)
                if run is None or run["session_id"] != command["session_id"] or run["status"] != "running":
                    raise IntentRefused("no interrupted adaptive job for this question")
                return "adaptive-run-finished", {"id": run_id, "status": "abandoned",
                    "reason": "Owner closed an interrupted job without retry, refund or fabricated results.",
                    "steps_completed": None}
            self.engine.records.append(project, principal, command, transition, lambda *_: None)
        return self.snapshot(project, principal=principal)

    def command(self, project, operation, command, *, principal):
        with self._lock:
            self.engine.records._owner(principal)
            if self._threads:
                raise IntentRefused("wait for running adaptive work to finish before recovery")
            if operation == "reopen":
                self.engine.reopen(project, command, principal=principal)
            elif operation == "abandon":
                self.engine.abandon_pending(project, command, principal=principal)
            else:
                raise IntentRefused("unknown adaptive recovery operation")
        return self.snapshot(project, principal=principal)
