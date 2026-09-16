"""Protected Actions entry point. All public output is fixed status text or ciphertext."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time

from .canonical import sha256_value
from .config import load_config
from .frontdoor_hosted import AgeCipher, MAX_CIPHERTEXT, WORKFLOW, WORKFLOW_PATH, _hex, validate_payload, verify_run
from .frontdoor_intent import IntentRefused, IntentStore
from .frontdoor_prepare import PreparationRecords, api_provider
from .github_cli import GitHubClient
from .frontdoor_control import stop_status
from .execution_fence import require_execution_open
from .hosted_exploration_call import SCHEMA as EXPLORATION_SCHEMA, execute as explore


def execute_call(store, github, payload, provider):
    """Called after job authentication; recheck controls immediately before paid work."""
    def check_stop():
        if stop_status(github) != {"state": "clear", "issues": []}:
            raise IntentRefused("hosted preparation requires clear stop")
        require_execution_open(github)

    check_stop()
    if payload["schema"] == EXPLORATION_SCHEMA:
        return explore(payload, provider, check_stop)
    return PreparationRecords(store, provider, None)._call(payload["role"], payload["prompt"])


def authorize_job(environ, event, github):
    repository = "ShaishiBear/dark-factory-2.0"
    owner = "ShaishiBear"
    if (environ.get("GITHUB_REPOSITORY") != repository or environ.get("GITHUB_REPOSITORY_OWNER") != owner
            or environ.get("GITHUB_ACTOR") != owner or environ.get("GITHUB_TRIGGERING_ACTOR") != owner
            or environ.get("GITHUB_REF") != "refs/heads/main"
            or environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or environ.get("GITHUB_WORKFLOW_REF") != f"{repository}/{WORKFLOW_PATH}@refs/heads/main"):
        raise IntentRefused("only a first owner dispatch on protected main may prepare intent")
    request_id = _hex(event["inputs"]["request_id"], 32)
    head = _hex(environ.get("GITHUB_SHA"), 40)
    run_id = int(environ["GITHUB_RUN_ID"])
    run = github.json(["api", f"repos/{repository}/actions/runs/{run_id}"])
    workflow = github.json(["api", f"repos/{repository}/actions/workflows/{WORKFLOW}"])
    verify_run(run, repository=repository, owner=owner, request_id=request_id, head=head, workflow_id=workflow["id"])
    return repository, owner, request_id, head, run_id


def refuse_replay(github, payload, run_id, *, now=None):
    now = int(time.time()) if now is None else now
    if not 0 <= now - payload["issued_at"] <= 900:
        raise IntentRefused("hosted preparation request expired")
    since = datetime.fromtimestamp(payload["issued_at"] - 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = github.json(["api", f"repos/{payload['repository']}/actions/workflows/{WORKFLOW}/runs"
                          f"?event=workflow_dispatch&per_page=100&created=>={since}"])
    if type(result.get("total_count")) is not int or result["total_count"] >= 100:
        raise IntentRefused("cannot prove a unique bounded preparation dispatch")
    matches = [row for row in result["workflow_runs"]
               if row.get("display_title") == "frontdoor-" + payload["request_id"]]
    if len(matches) != 1 or matches[0].get("id") != run_id or matches[0].get("run_attempt") != 1:
        raise IntentRefused("duplicate preparation dispatch refused")


def main():
    try:
        config = load_config(Path.cwd() / ".factory/kernel.json")
        github = GitHubClient(config.repository, cwd=Path.cwd())
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        repository, owner, request_id, head, run_id = authorize_job(os.environ, event, github)
        with tempfile.TemporaryDirectory(prefix="frontdoor-private-") as directory:
            private = Path(directory)
            identity = private / "identity.age"
            identity.touch(mode=0o600)
            identity.write_text(os.environ.pop("FRONTDOOR_AGE_IDENTITY"))
            cipher = AgeCipher(identity)
            payload = cipher.decrypt(event["inputs"]["ciphertext"], limit=MAX_CIPHERTEXT)
            validate_payload(payload, repository=repository, request_id=request_id, head=head)
            refuse_replay(github, payload, run_id)
            # No caller-supplied environment, tools or model. Exploration may narrow its
            # spending cap; both paths construct requests under protected role policy.
            store = IntentStore(private / "records", repository=repository, owner=owner)
            output, telemetry = execute_call(store, github, payload, api_provider(config.provider))
            result = {"request_sha256": sha256_value(payload), "run_id": run_id, "head": head,
                      "output": output, "telemetry": telemetry}
            encrypted = cipher.encrypt(result)
            target = Path(os.environ["RUNNER_TEMP"]) / "frontdoor-result"
            target.mkdir(mode=0o700)
            (target / "result.age.b64").write_text(encrypted)
        print("FRONTDOOR_ENCRYPTED_RESULT_READY")
        return 0
    except Exception:
        # CLI/provider exceptions may contain private prompt or credential-bearing argv.
        # Never print them, a traceback, raw model output, or a plaintext result artifact.
        print("FRONTDOOR_PREPARATION_REFUSED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
