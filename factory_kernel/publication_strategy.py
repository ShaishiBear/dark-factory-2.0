"""Regenerate publication input from canonical private exploration, never caller advice."""
from __future__ import annotations

from .canonical import sha256_value
from .exploration import Exploration
from .exploration_records import ExplorationRecords
from .exploration_repository import inspect_protected_repository
from .frontdoor_control import stop_status
from .frontdoor_intent import IntentRefused, _shape


class StoredStrategyReviews:
    def __init__(self, store, github, *, app_login, inspect=inspect_protected_repository):
        if github.repository != store.repository:
            raise IntentRefused("strategy context repository differs from owner store")
        self.store, self.github, self.app_login, self.inspect = store, github, app_login, inspect
        self.records = ExplorationRecords(store)

    def _stop(self):
        if stop_status(self.github) != {"state": "clear", "issues": []}:
            raise IntentRefused("strategy publication requires observed clear stop")

    def _prepare(self, project, session_id, version, principal):
        if not isinstance(session_id, str):
            raise IntentRefused("invalid stored strategy session")
        state, _approval = self.records.read(project, principal)
        if type(version) is not int or version != state["project_version"]:
            raise IntentRefused("strategy review no longer names current owner decisions")
        session = state["sessions"].get(session_id)
        if session is None:
            raise IntentRefused("unknown stored strategy session")
        # Paths come from the canonical session, not a request-selected URL or local file.
        paths = sorted(session["context"]["files"])
        def context():
            return self.inspect(self.github, paths, check_stop=self._stop)
        engine = Exploration(self.store, context, check_stop=self._stop, app_login=self.app_login)
        return engine.prepare_handoff(project, session_id, expected_project_version=version,
                                      principal=principal, include_strategy=True)

    def reference(self, project, session_id, *, expected_project_version, principal):
        review = self._prepare(project, session_id, expected_project_version, principal)
        selected = review["exploration"]
        return {"expected_project_version": review["project_version"],
                "approval_version": review["approval"]["version"], "spec_sha256": review["approval"]["spec_sha256"],
                "exploration": {key: selected[key] for key in
                                ("session_id", "recommendation_sha256", "handoff_sha256")}}

    def resolve(self, project, request, *, principal):
        _shape(request, {"expected_project_version", "approval_version", "spec_sha256", "exploration"})
        reference = request["exploration"]
        _shape(reference, {"session_id", "recommendation_sha256", "handoff_sha256"})
        if not isinstance(reference["session_id"], str):
            raise IntentRefused("invalid stored strategy session")
        review = self._prepare(project, reference["session_id"], request["expected_project_version"], principal)
        expected = {key: review["exploration"][key] for key in reference}
        if (reference != expected or request["approval_version"] != review["approval"]["version"]
                or request["spec_sha256"] != review["approval"]["spec_sha256"]):
            raise IntentRefused("strategy publication reference changed or belongs to another approval")
        if (review["input"]["version"] != "1.1" or review["input_sha256"] != sha256_value(review["input"])
                or review["exploration"]["qualification_status"] != "UNPROVEN"
                or review["exploration"]["proof_reuse_allowed"] is not False):
            raise IntentRefused("stored strategy review cannot grant proof authority")
        return review

    def choices(self, project, *, principal):
        """Private historical choices for display; preview must still revalidate currency."""
        state, approval = self.records.read(project, principal)
        rows = []
        for session in state["sessions"].values():
            if (session["status"] == "recommended" and session["handoffs"]
                    and session["binding"]["approval_version"] == approval["project_version"]
                    and session["binding"]["spec_sha256"] == approval["spec_sha256"]):
                candidate = session["candidates"][session["recommendations"][-1]["candidate_id"]]
                rows.append({"session_id": session["id"], "candidate_id": candidate["id"],
                             "mechanism": candidate["mechanism"], "project_version": state["project_version"],
                             "qualification_status": "UNPROVEN"})
        return rows
