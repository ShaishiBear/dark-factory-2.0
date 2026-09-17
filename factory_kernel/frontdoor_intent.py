"""Versioned intent history. No model, GitHub effect or execution authority lives here.

Callers must authenticate the principal outside this module; never accept its identity from
a command body. The store belongs to the trusted service account, not a worker sandbox.
Approval records user intent only. Protected-main programme admission remains a separate gate.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from .canonical import canonical_bytes, sha256_value
from .programme import compile_spec, parse_json

MAX_HISTORY_BYTES = 16 * 1024 * 1024


class IntentRefused(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    """An authenticated transport result, never a user/model-supplied JSON field."""
    identity: str
    role: str


def _shape(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise IntentRefused(f"expected exactly {sorted(fields)}")


def _text(value, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise IntentRefused(f"expected nonempty text of at most {limit} characters")
    return value  # preserve original wording, including whitespace and line breaks


def _texts(value):
    if not isinstance(value, list) or len(value) > 50:
        raise IntentRefused("expected at most 50 text entries")
    return [_text(item, 2000) for item in value]


def _intake_replay(old, new):
    return old == new


def intake_operations():
    """The four intake operations, statically registered with the roles `execute` always
    enforced. Built on call: project_events imports this module, so a module-level registry
    would be an import cycle. Same contents every time."""
    from .project_events import OperationSpec
    return {
        "record-intent": OperationSpec("record-intent", frozenset({"owner"}), _intake_replay),
        "add-exploration": OperationSpec("add-exploration", frozenset({"owner"}), _intake_replay),
        "propose-spec": OperationSpec("propose-spec", frozenset({"owner", "proposal"}), _intake_replay),
        "approve-spec": OperationSpec("approve-spec", frozenset({"owner"}), _intake_replay),
    }


class IntentStore:
    def __init__(self, directory: Path, *, repository: str, owner: str):
        self.directory = Path(directory)
        self.repository, self.owner = repository, _text(owner, 100)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._thread_lock = threading.RLock()

    def _authorize(self, principal):
        if not isinstance(principal, Principal) or not principal.identity:
            raise IntentRefused("authenticated principal required")
        if principal.role == "owner" and principal.identity != self.owner:
            raise IntentRefused("principal is not the configured owner")
        if principal.role not in {"owner", "proposal"}:
            raise IntentRefused("principal has no intake capability")

    @contextmanager
    def _locked(self, project):
        # Background exploration and HTTP observations share this store instance. Queue
        # short local transactions instead of treating a concurrent read as an uncertain
        # effect. Keep the existing nonblocking OS lock against another service process.
        if not self._thread_lock.acquire(timeout=5):
            raise IntentRefused("intent store is busy; refresh before another command")
        try:
            with self._file_locked(project) as path:
                yield path
        finally:
            self._thread_lock.release()

    @contextmanager
    def _file_locked(self, project):
        if not isinstance(project, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", project):
            raise IntentRefused("invalid project ID")
        lock = self.directory / f"{project}.lock"
        with lock.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield self.directory / f"{project}.json"
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self, path):
        if not path.exists():
            return []
        if path.is_symlink() or path.stat().st_size > MAX_HISTORY_BYTES:
            raise IntentRefused("invalid intent history file")
        try:
            events = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(events, list) or len(events) > 10000:
                raise ValueError("invalid event list")
            previous = None
            for version, event in enumerate(events, 1):
                _shape(event, {"schema", "schema_version", "project", "repository", "project_version",
                               "command", "actor", "created_at", "previous"})
                if (event["project_version"] != version or event["previous"] != previous
                        or event["project"] != path.stem or event["repository"] != self.repository
                        or event["schema"] != "dark-factory/intent-event" or event["schema_version"] != "1.0"):
                    raise ValueError("broken event chain")
                previous = sha256_value(event)
            return events
        except (ValueError, KeyError, TypeError) as exc:
            raise IntentRefused("intent history cannot be verified") from exc

    def _write(self, path, events):
        raw = canonical_bytes(events)
        if len(raw) > MAX_HISTORY_BYTES or len(events) > 10000:
            raise IntentRefused("intent history capacity reached")
        # One atomic replacement preserves the complete old history or the complete new one.
        # Events are append-only through this API. File hashes do not authenticate an attacker
        # who can write the service directory; that directory is part of the trust boundary.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _now():
        """The one clock every appended event is stamped with; tests pin it for byte identity."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _snapshot(events):
        draft, approvals, ledger = None, [], []
        for event in events:
            operation, payload = event["command"]["operation"], event["command"]["payload"]
            if operation in {"record-intent", "add-exploration"}:
                ledger.append({"version": event["project_version"], "actor": event["actor"],
                               "kind": operation, "wording": payload["wording"]})
                if operation == "record-intent":
                    draft = None
            elif operation == "propose-spec":
                draft = {**payload, "draft_version": event["project_version"],
                         "source_intent_version": next(row["version"] for row in reversed(ledger)
                                                       if row["kind"] == "record-intent"),
                         "spec_sha256": sha256_value(payload["spec"])}
            elif operation == "approve-spec":
                approvals.append({"spec": deepcopy(draft["spec"]), **payload,
                                  "source_intent_version": draft["source_intent_version"],
                                  "actor": event["actor"], "approved_at": event["created_at"],
                                  "project_version": event["project_version"]})
        return {"project_version": len(events), "ledger": ledger, "draft": draft,
                "approvals": approvals, "execution_status": "not-activated-by-intake"}

    def snapshot(self, project, *, principal: Principal):
        self._authorize(principal)
        with self._locked(project) as path:
            return self._snapshot(self._read(path))

    def execute(self, project, command, *, principal: Principal):
        self._authorize(principal)
        # Round-trip before validation to detach caller-owned mutable objects and refuse
        # duplicate keys/non-JSON input at the transport/parser boundary.
        command = parse_json(canonical_bytes(command).decode("utf-8"))
        _shape(command, {"idempotency_key", "expected_project_version", "operation", "payload"})
        _text(command["idempotency_key"], 100)
        expected = command["expected_project_version"]
        if type(expected) is not int or expected < 0:
            raise IntentRefused("expected project version must be a nonnegative integer")
        operation, payload = command["operation"], command["payload"]
        if principal.role != "owner" and operation != "propose-spec":
            raise IntentRefused("only the authenticated owner can record intent or approve scope")
        def validate_transition(events):
            state = self._snapshot(events)
            if operation in {"record-intent", "add-exploration"}:
                _shape(payload, {"wording"})
                _text(payload["wording"])
            elif operation == "propose-spec":
                _shape(payload, {"spec", "assumptions", "open_questions", "technical_questions"})
                if not any(row["kind"] == "record-intent" for row in state["ledger"]):
                    raise IntentRefused("record the owner's original intent before proposing scope")
                spec = compile_spec(payload["spec"], repository=self.repository)
                previous = state["approvals"][-1]["spec"] if state["approvals"] else None
                if spec["revision"] != (previous["revision"] + 1 if previous else 1):
                    raise IntentRefused("proposal must name the next specification revision")
                if previous and spec["id"] != previous["id"]:
                    raise IntentRefused("specification identity cannot change within a project")
                for key in ("assumptions", "open_questions", "technical_questions"):
                    _texts(payload[key])
            elif operation == "approve-spec":
                _shape(payload, {"draft_version", "spec_sha256", "wording"})
                _text(payload["wording"])
                draft = state["draft"]
                if (draft is None or type(payload["draft_version"]) is not int
                        or payload["draft_version"] != draft["draft_version"]
                        or payload["spec_sha256"] != draft["spec_sha256"]):
                    raise IntentRefused("approval does not match the current reviewed draft")
                if draft["open_questions"]:
                    raise IntentRefused("blocking product questions remain unresolved")
                if state["approvals"] and state["approvals"][-1]["draft_version"] == draft["draft_version"]:
                    raise IntentRefused("this draft was already approved")
            else:
                raise IntentRefused("unknown intake operation")
            return payload

        operations = intake_operations()
        if operation not in operations:
            raise IntentRefused("unknown intake operation")
        from .project_events import ProjectEvents
        result = ProjectEvents(self, operations).append(
            project=project, principal=principal, expected_version=expected,
            idempotency_key=command["idempotency_key"], operation=operation, payload=payload,
            validate_transition=validate_transition)
        with self._locked(project) as path:
            events = self._read(path)
        if result.replayed:
            return self._snapshot(events[:result.index + 1])
        return self._snapshot(events)
