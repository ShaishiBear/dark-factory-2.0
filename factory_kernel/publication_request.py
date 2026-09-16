"""Private owner publication requests and fresh approval checks, without GitHub effects.

These records are not a queue and do not authorize a merge. A protected publisher must
independently verify its source, current main, stop state and required checks. It must ask
this store again before each effect; an exported request is never a lasting capability.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import tempfile

from .canonical import canonical_bytes, sha256_value
from .frontdoor_intent import IntentRefused, _shape
from .frontdoor_programme import prepare_programme
from .programme import compile_programme, parse_json

REQUEST_LIFETIME = timedelta(hours=1)


class PublicationRequests:
    def __init__(self, store, *, app_login, clock=None, strategy_reviews=None):
        self.store, self.app_login = store, app_login
        self.strategy_reviews = strategy_reviews
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.directory = store.directory / "publication-requests"
        if self.directory.is_symlink():
            raise IntentRefused("publication directory cannot be a symlink")
        self.directory.mkdir(mode=0o700, exist_ok=True)

    def _owner(self, principal):
        self.store._authorize(principal)
        if principal.role != "owner":
            raise IntentRefused("only the authenticated owner may request publication")

    def review(self, project, request, *, principal):
        self._owner(principal)
        if isinstance(request, dict) and "exploration" in request:
            if self.strategy_reviews is None:
                raise IntentRefused("strategy publication is not enabled")
            return self.strategy_reviews.resolve(project, request, principal=principal)
        return prepare_programme(self.store, project, request, principal=principal, app_login=self.app_login)

    def _path(self, project, request_id):
        if not isinstance(project, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", project):
            raise IntentRefused("invalid project ID")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise IntentRefused("invalid publication request identity")
        return self.directory / f"{project}-{request_id}.json"

    def _read(self, path):
        if not path.exists() or path.is_symlink() or path.stat().st_size > 250000:
            raise IntentRefused("publication request missing or invalid")
        return parse_json(path.read_text(encoding="utf-8"))

    def _save(self, path, record):
        raw = canonical_bytes(record)
        if len(raw) > 250000:
            raise IntentRefused("publication request too large")
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, path)
            if os.name != "nt":
                fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)

    def reserve(self, project, command, *, principal, observation):
        """Record exact public payload consent, not private intake wording.

        observation is supplied by a trusted adapter, never copied from command JSON. It
        names a protected-main read with exact repository visibility and active input. The
        later publisher has to re-observe these facts; this method performs no remote effect.
        """
        self._owner(principal)
        _shape(command, {"request_id", "review", "destination"})
        path = self._path(project, command["request_id"])
        review = self.review(project, command["review"], principal=principal)
        destination = command["destination"]
        _shape(destination, {"repository", "visibility", "input_sha256"})
        self._observation(observation)
        if destination != {"repository": self.store.repository, "visibility": observation["visibility"],
                           "input_sha256": review["input_sha256"]}:
            raise IntentRefused("owner consent must name the exact payload, destination and visibility")
        identity = {"project": project, "owner": principal.identity, "command": deepcopy(command)}
        with self.store._locked(project) as intent_path:
            events = self.store._read(intent_path)
            if len(events) != review["project_version"]:
                raise IntentRefused("scope changed while reserving publication")
            if path.exists():
                record = self._read(path)
                if record["identity"] != identity:
                    raise IntentRefused("publication request identity reused with different content")
                return record  # Observed historical outcome only; callers must still check currency.
            # One request per project version, including expired or uncertain requests. An
            # operator must reconcile any earlier effect before a replacement can be added.
            for prior in self.directory.glob(f"{project}-*.json"):
                previous = self._read(prior)
                if (previous["identity"]["project"] == project
                        and previous["project_version"] == review["project_version"]):
                    raise IntentRefused("this project version already has a publication request")
            active = observation["active_input"]
            state = "reserved"
            if active is not None:
                current = compile_programme(active, repository=self.store.repository)
                state = ("already-active" if sha256_value(current.spec) == review["approval"]["spec_sha256"]
                         else "requires-governed-replacement")
            now = self.clock()
            if now.tzinfo is None:
                raise IntentRefused("publication clock must have a timezone")
            record = {"schema": "dark-factory/publication-request", "schema_version": "1.0",
                      "identity": identity, "project_version": review["project_version"],
                      "intent_head_sha256": sha256_value(events[-1]), "state": state,
                      "created_at": now.isoformat(), "expires_at": (now + REQUEST_LIFETIME).isoformat(),
                      "input": review["input"], "input_sha256": review["input_sha256"],
                      "programme_sha256": review["programme_sha256"],
                      "observation": deepcopy(observation)}
            self._save(path, record)
            return record

    def _observation(self, observation):
        _shape(observation, {"repository", "visibility", "main_sha", "protected", "active_input", "stop"})
        if (observation["repository"] != self.store.repository
                or observation["visibility"] not in {"public", "private"}
                or observation["protected"] is not True
                or not isinstance(observation["main_sha"], str)
                or not re.fullmatch(r"[a-f0-9]{40}", observation["main_sha"])
                or observation["stop"] != {"state": "clear", "issues": []}):
            raise IntentRefused("publication requires exact protected main and observed clear stop")
        if observation["active_input"] is not None:
            compile_programme(observation["active_input"], repository=self.store.repository)

    def current(self, project, request_id, *, principal, observation):
        """Fresh read-only decision, with no scope wording in its response.

        The authenticated caller must bind this response to a fresh nonce and exact source
        workflow before using it. This local method does not authenticate a remote caller.
        """
        self._owner(principal)
        self._observation(observation)
        with self.store._locked(project) as path:
            record = self._read(self._path(project, request_id))
            if record["identity"]["project"] != project or record["identity"]["owner"] != principal.identity:
                raise IntentRefused("publication request belongs to another owner or project")
            events = self.store._read(path)
            if (len(events) != record["project_version"] or not events
                    or sha256_value(events[-1]) != record["intent_head_sha256"]):
                raise IntentRefused("publication request no longer names current owner decisions")
            now = self.clock()
            if not datetime.fromisoformat(record["created_at"]) <= now < datetime.fromisoformat(record["expires_at"]):
                raise IntentRefused("publication request expired or clock moved backwards")
            if record["state"] != "reserved":
                raise IntentRefused("publication request does not permit a new programme")
            if observation != record["observation"]:
                raise IntentRefused("main, visibility, active programme or stop changed")
            compiled = compile_programme(record["input"], repository=self.store.repository)
            if (sha256_value(record["input"]) != record["input_sha256"]
                    or compiled.sha256 != record["programme_sha256"]):
                raise IntentRefused("publication payload changed")
            result = {"request_id": request_id, "request_sha256": sha256_value(record),
                    "repository": self.store.repository, "project": project,
                    "project_version": record["project_version"], "main_sha": observation["main_sha"],
                    "input_sha256": record["input_sha256"], "programme_sha256": compiled.sha256,
                    "expires_at": record["expires_at"], "decision": "current-owner-request"}
        # Regeneration may perform remote reads and acquire the intent lock. Keep it
        # outside this lock, then fence the owner state and lifetime again before returning.
        review = self.review(project, record["identity"]["command"]["review"], principal=principal)
        if (review["input_sha256"] != record["input_sha256"]
                or review["programme_sha256"] != record["programme_sha256"]
                or review["input"] != record["input"]):
            raise IntentRefused("current canonical review differs from the consented publication")
        with self.store._locked(project) as path:
            after = self.store._read(path)
            if (len(after) != record["project_version"] or not after
                    or sha256_value(after[-1]) != record["intent_head_sha256"]):
                raise IntentRefused("owner decisions changed during publication regeneration")
            if not datetime.fromisoformat(record["created_at"]) <= self.clock() < datetime.fromisoformat(record["expires_at"]):
                raise IntentRefused("publication request expired during regeneration")
        return result
