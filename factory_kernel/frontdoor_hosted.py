"""Encrypted, owner-dispatched proposal calls. No API credential leaves GitHub Actions.

The caller reserves its intent version before this adapter runs. Each stage also records
its opaque dispatch identity before POST. An uncertain dispatch is observed, never repeated.
Returned JSON still passes the ordinary local intent audit/compiler and freshness checks.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
import zlib

from .agents import AgentResult
from .canonical import canonical_bytes, sha256_value
from .frontdoor_intent import IntentRefused, _shape, _text
from .frontdoor_prepare import PreparationRecords
from .programme import parse_json
from .worker_policy import INTAKE_ROLES, allowed_tools, effort, max_turns, max_budget_usd, stage_timeout_seconds

WORKFLOW = "dark-factory-frontdoor-prepare.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW
MAX_PLAINTEXT = 500000
MAX_CIPHERTEXT = 60000  # Leaves room in GitHub's 65,535-character dispatch input limit.
MAX_RESULT = 200000
WAIT_SECONDS = 600


def _hex(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % length, value):
        raise IntentRefused("invalid hosted preparation identity")
    return value


class AgeCipher:
    """Standard age X25519 identity, private on the host and in one Actions secret."""

    def __init__(self, identity):
        self.identity = Path(identity)
        if (self.identity.is_symlink() or not self.identity.is_file()
                or self.identity.stat().st_size > 1024
                or (os.name != "nt" and self.identity.stat().st_mode & 0o077)):
            raise IntentRefused("encryption identity must be a small private regular file")
        # Exclude plugin, SSH and passphrase identities; never invoke credential discovery.
        rows = [row for row in self.identity.read_text().splitlines() if row and not row.startswith("#")]
        if len(rows) != 1 or not re.fullmatch(r"AGE-SECRET-KEY-1[0-9A-Z]{58}", rows[0]):
            raise IntentRefused("one native age identity is required")

    def _run(self, operation, raw):
        result = subprocess.run(["age", operation, "-i", str(self.identity)], input=raw,
                                capture_output=True, timeout=15, check=False)
        if result.returncode:
            raise IntentRefused("encrypted preparation exchange refused")
        return result.stdout

    def encrypt(self, value):
        raw = canonical_bytes(value)
        if len(raw) > MAX_PLAINTEXT:
            raise IntentRefused("preparation input exceeds its private transport bound")
        return base64.b64encode(self._run("--encrypt", zlib.compress(raw))).decode("ascii")

    def decrypt(self, ciphertext, *, limit=MAX_RESULT):
        if not isinstance(ciphertext, str) or len(ciphertext) > limit:
            raise IntentRefused("encrypted preparation exceeds its transport bound")
        compressed = self._run("--decrypt", base64.b64decode(ciphertext, validate=True))
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_PLAINTEXT + 1)
        if len(raw) > MAX_PLAINTEXT or not decoder.eof or decoder.unused_data:
            raise IntentRefused("invalid bounded preparation payload")
        return parse_json(raw.decode("utf-8"))


def validate_payload(payload, *, repository, request_id, head):
    _shape(payload, {"schema", "request_id", "repository", "head", "role", "prompt", "issued_at"})
    if (payload["schema"] != "dark-factory/hosted-proposal-v1" or payload["repository"] != repository
            or payload["request_id"] != _hex(request_id, 32) or payload["head"] != _hex(head, 40)
            or payload["role"] not in INTAKE_ROLES):
        raise IntentRefused("hosted preparation payload binding refused")
    _text(payload["prompt"], 450000)
    if type(payload["issued_at"]) is not int:
        raise IntentRefused("hosted preparation needs a bounded dispatch time")
    return payload


def verify_run(run, *, repository, owner, request_id, head, workflow_id):
    if (run.get("repository", {}).get("full_name") != repository
            or run.get("actor", {}).get("login") != owner
            or run.get("triggering_actor", {}).get("login") != owner
            or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
            or run.get("head_sha") != head or run.get("run_attempt") != 1
            or run.get("workflow_id") != workflow_id or run.get("path") != WORKFLOW_PATH
            or run.get("display_title") != "frontdoor-" + request_id):
        raise IntentRefused("hosted preparation run provenance refused")
    if type(run.get("id")) is not int or run["id"] < 1:
        raise IntentRefused("invalid hosted preparation run ID")
    return run


class HostedPreparationProvider:
    """One fixed workflow POST per stage, with bounded read-only outcome reconciliation."""

    def __init__(self, store, github, cipher, *, clock=time.monotonic, sleep=time.sleep):
        self.store, self.github, self.cipher = store, github, cipher
        self.records = PreparationRecords(store, None, None, directory="hosted-calls")
        self.clock, self.sleep = clock, sleep

    def run(self, request):
        if (request.role not in INTAKE_ROLES or request.allowed_tools != allowed_tools(request.role)
                or request.environment or request.model is not None or request.structured_schema is not None
                or request.path_scope is not None or request.max_turns != max_turns(request.role)
                or request.max_budget_usd != max_budget_usd(request.role)
                or request.timeout_seconds != stage_timeout_seconds(request.role)
                or request.effort != effort(request.role)):
            raise IntentRefused("hosted proposal request exceeds the central role policy")
        repository, owner = self.store.repository, self.store.owner
        head = _hex(self.github.json(["api", f"repos/{repository}/git/ref/heads/main"])["object"]["sha"], 40)
        workflow = self.github.json(["api", f"repos/{repository}/actions/workflows/{WORKFLOW}"])
        if workflow.get("path") != WORKFLOW_PATH or workflow.get("state") != "active":
            raise IntentRefused("protected preparation workflow is not active")
        request_id = uuid.uuid4().hex
        payload = {"schema": "dark-factory/hosted-proposal-v1", "request_id": request_id,
                   "repository": repository, "head": head, "role": request.role, "prompt": request.prompt,
                   "issued_at": int(time.time())}
        validate_payload(payload, repository=repository, request_id=request_id, head=head)
        ciphertext = self.cipher.encrypt(payload)
        if len(ciphertext) > MAX_CIPHERTEXT:
            raise IntentRefused("private intent is too large for hosted dispatch; no content was truncated")
        path = self.records.directory / f"{request_id}.json"
        record = {"request_id": request_id, "request_sha256": sha256_value(payload), "head": head,
                  "role": request.role, "state": "dispatch-reserved", "run_id": None,
                  "created_at": datetime.now(timezone.utc).isoformat()}
        self.records._save(path, record)  # No POST before this durable reservation.
        deadline = self.clock() + WAIT_SECONDS
        try:
            try:
                self.github.run(["workflow", "run", WORKFLOW, "-R", repository, "--ref", "main",
                                 "-f", f"request_id={request_id}", "-f", f"ciphertext={ciphertext}"])
                record["state"] = "dispatched"
            except (RuntimeError, OSError, subprocess.SubprocessError):
                record["state"] = "dispatch-uncertain"  # Observe the same ID, never repeat POST.
            self.records._save(path, record)
            run = None
            while self.clock() < deadline:
                if record["run_id"] is None:
                    rows = self.github.json(["api", f"repos/{repository}/actions/workflows/{WORKFLOW}/runs"
                                             "?event=workflow_dispatch&branch=main&per_page=100"])["workflow_runs"]
                    matches = [row for row in rows if row.get("display_title") == "frontdoor-" + request_id]
                    if len(matches) > 1:
                        raise IntentRefused("duplicate hosted preparation runs refused")
                    if matches:
                        run = matches[0]
                        record["run_id"] = run["id"]
                        self.records._save(path, record)
                else:
                    run = self.github.json(["api", f"repos/{repository}/actions/runs/{record['run_id']}"])
                if run is not None:
                    verify_run(run, repository=repository, owner=owner, request_id=request_id,
                               head=head, workflow_id=workflow["id"])
                    if run["status"] == "completed":
                        if run["conclusion"] != "success":
                            raise IntentRefused("hosted preparation did not complete successfully")
                        result = self._result(run["id"], payload)
                        record.update(state="completed", result=result)
                        self.records._save(path, record)
                        return AgentResult(provider_id="github-actions-api", model=result["telemetry"]["model"],
                                           content="", structured_output=result["output"],
                                           cost_usd=result["telemetry"]["cost_usd"])
                self.sleep(15)
            raise IntentRefused("hosted preparation observation timed out; recorded call will not repeat")
        except Exception as exc:
            record.update(state="failed-or-uncertain", failure=type(exc).__name__)
            self.records._save(path, record)
            raise IntentRefused("hosted preparation unavailable; inspect its recorded run before recovery") from None

    def _result(self, run_id, payload):
        repository = self.store.repository
        rows = self.github.json(["api", f"repos/{repository}/actions/runs/{run_id}/artifacts"])["artifacts"]
        name = "frontdoor-" + payload["request_id"]
        matches = [row for row in rows if row.get("name") == name]
        if (len(matches) != 1 or matches[0].get("expired") is not False
                or type(matches[0].get("size_in_bytes")) is not int
                or not 0 < matches[0]["size_in_bytes"] <= MAX_RESULT):
            raise IntentRefused("missing or invalid encrypted preparation artifact")
        with tempfile.TemporaryDirectory(prefix="frontdoor-result-", dir=self.records.directory) as directory:
            self.github.run(["run", "download", str(run_id), "-R", repository, "-n", name, "-D", directory])
            path = Path(directory) / "result.age.b64"
            if (list(Path(directory).iterdir()) != [path] or path.is_symlink()
                    or not path.is_file() or path.stat().st_size > MAX_RESULT):
                raise IntentRefused("invalid encrypted result file")
            result = self.cipher.decrypt(path.read_text())
        _shape(result, {"request_sha256", "run_id", "head", "output", "telemetry"})
        if (result["request_sha256"] != sha256_value(payload) or result["run_id"] != run_id
                or result["head"] != payload["head"]):
            raise IntentRefused("hosted preparation result does not bind this request and run")
        _shape(result["telemetry"], {"model", "cost_usd"})
        _text(result["telemetry"]["model"], 200)
        return result
