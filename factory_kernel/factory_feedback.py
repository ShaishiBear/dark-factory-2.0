"""Owner-scoped import of authenticated outcomes into the existing exploration log."""
from copy import deepcopy

from .canonical import sha256_value
from .exploration_records import OPERATION, projection
from .feedback_observation import observe_feedback
from .frontdoor_intent import IntentRefused
from .programme import compile_programme
from .programme_strategy import strategy_from_session


def recorded_handoff(events, programme, session_id):
    """A platform receipt must still belong to this project's recorded exploration."""
    source = programme.strategy["source"]
    if source["session_id"] != session_id:
        raise IntentRefused("feedback belongs to another exploration session")
    for index, event in enumerate(events):
        command = event["command"]
        if command["operation"] != OPERATION:
            continue
        payload = command["payload"]
        if (payload["session_id"] != session_id or payload["kind"] != "handoff"
                or payload["data"]["recommendation_sha256"] != source["recommendation_sha256"]):
            continue
        historical = projection(events[:index + 1])
        session = historical["sessions"][session_id]
        candidate = compile_programme({**deepcopy(payload["data"]["input"]), "version": "1.1",
            "strategy": strategy_from_session(historical, session)}, repository=programme.spec["repository"])
        if candidate.sha256 == programme.sha256:
            return sha256_value(payload["data"])
    raise IntentRefused("feedback programme has no exact recorded exploration handoff")


class FactoryFeedback:
    def __init__(self, exploration, github):
        if exploration.records.store.repository != github.repository:
            raise IntentRefused("feedback repository differs from the configured project")
        self.engine, self.github = exploration, github

    def import_outcome(self, project, command, *, principal):
        engine, store = self.engine, self.engine.records.store
        state, approval = engine.records.read(project, principal)
        engine._session(state, approval, command["session_id"], allow_stale=True)
        engine.check_stop()
        observation = observe_feedback(self.github, command["request"])
        programme = compile_programme(observation.pop("programme_input"), repository=store.repository)
        with store._locked(project) as path:
            handoff = recorded_handoff(store._read(path), programme, command["session_id"])
        receipt = observation["receipt"]
        identity = {k: receipt[k] for k in ("repository", "run_id", "run_attempt", "pr")}
        data = {"session_id": command["session_id"], "run_identity": identity,
                "handoff_sha256": handoff, "observation": observation}
        data["id"] = sha256_value(data)

        def transition(state, approval):
            engine._session(state, approval, command["session_id"], allow_stale=True)
            if programme.spec != approval["spec"]:
                raise IntentRefused("feedback scope no longer matches current approval")
            if len(state["factory_outcomes"]) >= 80:
                raise IntentRefused("factory outcome capacity reached")
            if any(row["run_identity"] == identity for row in state["factory_outcomes"].values()):
                raise IntentRefused("factory run attempt already imported")
            return "factory-outcome-imported", data

        def replay_guard(state, approval, payload):
            engine._session(state, approval, command["session_id"], allow_stale=True)
            if programme.spec != approval["spec"] or payload["data"] != data:
                raise IntentRefused("authenticated outcome changed since import")

        engine.check_stop()
        # The shared writer checks owner identity, CAS, exact replay and payload size under
        # its lock. No invalidation, budget reset, programme activation or proof reuse occurs.
        state, _, _ = engine.records.append(project, principal, command, transition, replay_guard)
        return {"project_version": state["project_version"], "outcome": deepcopy(state["factory_outcomes"][data["id"]])}
