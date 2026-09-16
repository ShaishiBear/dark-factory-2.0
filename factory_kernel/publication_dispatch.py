"""Owner Front Door dispatch reservation. One encrypted POST; observation never retries it."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import subprocess

from .canonical import sha256_value
from .frontdoor_hosted import MAX_CIPHERTEXT
from .frontdoor_intent import IntentRefused
from .frontdoor_prepare import PreparationRecords
from .programme import parse_json
from .publication_request import PublicationRequests
from .publication_source import observe_publication_source
from .publication_observation import _complete
from . import publication_policy as policy


class PublicationDispatches(PreparationRecords):
    def __init__(self, store, github, cipher, *, app_login, requests=None, observe=observe_publication_source):
        if (store.repository != policy.REPOSITORY or store.owner != policy.OWNER
                or app_login != policy.APP_LOGIN or github.repository != policy.REPOSITORY):
            raise IntentRefused("host publication destination differs from protected policy")
        super().__init__(store, None, None, directory="publication-dispatches")
        self.github, self.cipher, self.observe = github, cipher, observe
        self.requests = requests or PublicationRequests(store, app_login=app_login)
        self.app_login = app_login

    def _project_owner(self, project, principal):
        self.requests._owner(principal)
        if project != policy.PROJECT:
            raise IntentRefused("project differs from protected publication policy")

    def preview_strategy(self, project, request, *, principal):
        self._project_owner(project, principal)
        if self.requests.strategy_reviews is None:
            raise IntentRefused("strategy publication is not enabled")
        if not isinstance(request, dict) or set(request) != {"session_id", "expected_project_version"}:
            raise IntentRefused("strategy preview must name a stored session and current project version")
        reference = self.requests.strategy_reviews.reference(project, request["session_id"],
            expected_project_version=request["expected_project_version"], principal=principal)
        return self.preview(project, reference, principal=principal)

    def preview(self, project, review_request, *, principal):
        self._project_owner(project, principal)
        review = self.requests.review(project, review_request, principal=principal)
        observation = self.observe(self.github)
        self.requests._observation(observation)
        active = observation["active_input"]
        state = "ready-for-consent" if active is None else (
            "already-active" if sha256_value(active["spec"]) == review["approval"]["spec_sha256"]
            else "requires-governed-replacement")
        result = {"state": state, "project_version": review["project_version"],
                "review": deepcopy(review_request), "input": review["input"],
                "input_sha256": review["input_sha256"], "programme_sha256": review["programme_sha256"],
                "destination": {"repository": self.store.repository, "visibility": observation["visibility"],
                                "input_sha256": review["input_sha256"]},
                "source_sha": observation["main_sha"]}
        previous = self._latest_request(project)
        if previous is not None and previous["project_version"] == review["project_version"]:
            command = previous["identity"]["command"]
            if command["review"] != review_request or command["destination"] != result["destination"]:
                result["state"] = "requires-reconciliation"
            elif previous["state"] == "reserved":
                try:
                    self.requests.current(project, command["request_id"], principal=principal, observation=observation)
                    result["request_id"] = command["request_id"]
                except IntentRefused:
                    result["state"] = "requires-reconciliation"
        return result

    def publish(self, project, command, *, principal):
        self._project_owner(project, principal)
        record = self.requests.reserve(project, command, principal=principal, observation=self.observe(self.github))
        summary = {key: record[key] for key in ("state", "project_version", "input_sha256", "programme_sha256")}
        summary["request_id"] = command["request_id"]
        if record["state"] != "reserved":
            return summary  # Same approved scope and governed replacement never dispatch.
        path = self.directory / f"{project}-{record['project_version']}.json"
        # A retry returns the historical dispatch, including uncertainty. It never repeats POST.
        with self.store._locked(project):
            if path.exists():
                return self._same_record(path, record)
        current = self.requests.current(project, command["request_id"], principal=principal,
                                        observation=self.observe(self.github))
        workflow = self.github.json(["api", f"repos/{policy.REPOSITORY}/actions/workflows/{policy.WORKFLOW}"])
        if (workflow.get("path") != policy.WORKFLOW_PATH or workflow.get("state") != "active"
                or type(workflow.get("id")) is not int or workflow["id"] <= 0):
            raise IntentRefused("protected publication workflow is not active")
        payload = {"schema": "dark-factory/publication-dispatch-v1", "request_id": command["request_id"],
                   "request_sha256": sha256_value(record), "repository": policy.REPOSITORY, "project": project,
                   "source_sha": current["main_sha"], "input": record["input"],
                   "input_sha256": record["input_sha256"], "programme_sha256": record["programme_sha256"],
                   "issued_at": int(datetime.fromisoformat(record["created_at"]).timestamp())}
        ciphertext = self.cipher.encrypt(payload)
        if not isinstance(ciphertext, str) or not ciphertext or len(ciphertext) > MAX_CIPHERTEXT:
            raise IntentRefused("approved publication exceeds its encrypted transport bound")
        self.requests.current(project, command["request_id"], principal=principal,
                              observation=self.observe(self.github))
        dispatch = {**summary, "schema": "dark-factory/host-publication-dispatch", "schema_version": "1.0",
                    "request_sha256": sha256_value(record), "source_sha": current["main_sha"],
                    "created_at": record["created_at"], "expires_at": record["expires_at"],
                    "state": "dispatch-pending", "workflow_id": workflow["id"], "run_id": None}
        with self.store._locked(project) as intent_path:
            if path.exists():
                return self._same_record(path, record)
            events = self.store._read(intent_path)
            if len(events) != record["project_version"] or sha256_value(events[-1]) != record["intent_head_sha256"]:
                raise IntentRefused("owner decisions changed before publication dispatch")
            self._save(path, dispatch)  # Durable reservation before POST, separate from immutable consent.
        try:
            self.github.run(["workflow", "run", policy.WORKFLOW, "-R", policy.REPOSITORY, "--ref", "main",
                             "-f", f"request_id={command['request_id']}", "-f", f"ciphertext={ciphertext}"])
            dispatch["state"] = "dispatch-submitted"
        except (RuntimeError, OSError, subprocess.SubprocessError):
            dispatch["state"] = "dispatch-uncertain"
        self._save(path, dispatch)
        return dispatch

    def _same_record(self, path, request):
        if path.is_symlink() or path.stat().st_size > 1000000:
            raise IntentRefused("publication dispatch record is invalid")
        prior = parse_json(path.read_text())
        if prior["request_sha256"] != sha256_value(request):
            raise IntentRefused("publication version already has a different dispatch")
        return prior

    def _latest_request(self, project):
        rows = [self.requests._read(path) for path in self.requests.directory.glob(f"{project}-*.json")]
        return max(rows, key=lambda row: row["project_version"]) if rows else None

    def latest(self, project):
        if project != policy.PROJECT:
            raise IntentRefused("project differs from protected publication policy")
        record = super().latest(project)
        request = self._latest_request(project)
        if request is not None and (record is None or request["project_version"] > record["project_version"]):
            return {key: request[key] for key in ("state", "project_version", "input_sha256", "programme_sha256")}
        if record is None:
            return None
        result = {**record, "workflow_observation": "unavailable", "observed_at": None}
        try:
            since = datetime.fromisoformat(record["created_at"]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = _complete(self.github.json(["api", f"repos/{policy.REPOSITORY}/actions/workflows/{policy.WORKFLOW}/runs"
                                               f"?event=workflow_dispatch&per_page=100&created=>={since}"]), "workflow_runs")
            matches = [row for row in rows if row.get("display_title") == "programme-" + record["request_id"]]
            if not matches:
                return {**result, "workflow_observation": "not-observed"}
            if len(matches) != 1:
                raise IntentRefused("publication run is ambiguous")
            run = matches[0]
            if (type(run.get("id")) is not int or run["id"] <= 0 or run.get("run_attempt") != 1
                    or run.get("event") != "workflow_dispatch" or run.get("path") != policy.WORKFLOW_PATH
                    or run.get("workflow_id") != record["workflow_id"] or run.get("head_branch") != "main"
                    or run.get("head_sha") != record["source_sha"]
                    or run.get("repository", {}).get("full_name") != policy.REPOSITORY
                    or run.get("head_repository", {}).get("full_name") != policy.REPOSITORY
                    or any(run.get(role, {}).get("login") != policy.OWNER or run[role].get("type") != "User"
                           for role in ("actor", "triggering_actor"))):
                raise IntentRefused("publication workflow provenance differs")
            result.update(workflow_observation="observed", run_id=run["id"],
                          workflow_status=run["status"], workflow_conclusion=run["conclusion"],
                          observed_at=datetime.now(timezone.utc).isoformat())
            # A successful workflow is not a product completion receipt. Active programme
            # and independently verified product progress remain separate snapshot fields.
            return result
        except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return result
