"""Protected-main publication jobs; no candidate code or model is executed."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .canonical import canonical_bytes
from .frontdoor_control import request_stop
from .frontdoor_hosted import AgeCipher, MAX_CIPHERTEXT, _hex
from .frontdoor_intent import IntentRefused, _shape
from .github_cli import GitHubClient
from .publication_admission import validate_manifest
from .publication_effects import EffectJournal, ProgrammePublisher
from .publication_observation import _complete, download_archive, read_manifest_archive
from . import publication_policy as policy


def authorize_job(environ, event, github):
    expected = {"GITHUB_REPOSITORY": policy.REPOSITORY, "GITHUB_REPOSITORY_OWNER": policy.OWNER,
                "GITHUB_ACTOR": policy.OWNER, "GITHUB_TRIGGERING_ACTOR": policy.OWNER,
                "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_WORKFLOW_REF": f"{policy.REPOSITORY}/{policy.WORKFLOW_PATH}@refs/heads/main"}
    if any(environ.get(key) != value for key, value in expected.items()):
        raise IntentRefused("first owner dispatch of protected publication workflow required")
    _shape(event["inputs"], {"request_id", "ciphertext"})
    request_id = _hex(event["inputs"]["request_id"], 32)
    source = _hex(environ.get("GITHUB_SHA"), 40)
    run_id = int(environ["GITHUB_RUN_ID"])
    if run_id <= 0:
        raise IntentRefused("invalid publication run")
    run = github.json(["api", f"repos/{policy.REPOSITORY}/actions/runs/{run_id}"])
    if (run.get("id") != run_id or run.get("run_attempt") != 1 or run.get("event") != "workflow_dispatch"
            or run.get("path") != policy.WORKFLOW_PATH or run.get("head_branch") != "main"
            or run.get("head_sha") != source or run.get("repository", {}).get("full_name") != policy.REPOSITORY
            or run.get("head_repository", {}).get("full_name") != policy.REPOSITORY
            or run.get("display_title") != "programme-" + request_id
            or any(run.get(role, {}).get("login") != policy.OWNER or run[role].get("type") != "User"
                   for role in ("actor", "triggering_actor"))):
        raise IntentRefused("publication platform provenance refused")
    return request_id, source, run_id


def refuse_replay(github, request_id, run_id, issued_at, *, now=None):
    now = int(time.time()) if now is None else now
    if type(issued_at) is not int or not 0 <= now - issued_at < 3600:
        raise IntentRefused("publication dispatch expired")
    # Search the entire possible owner-request lifetime, not a caller-controlled
    # shorter interval that could conceal an earlier dispatch of the same identity.
    since = datetime.fromtimestamp(now - 3660, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = github.json(["api", f"repos/{policy.REPOSITORY}/actions/workflows/{policy.WORKFLOW}/runs"
                                 f"?event=workflow_dispatch&per_page=100&created=>={since}"])
    rows = _complete(payload, "workflow_runs")
    matches = [row for row in rows if row.get("display_title") == "programme-" + request_id]
    if len(matches) != 1 or matches[0].get("id") != run_id or matches[0].get("run_attempt") != 1:
        raise IntentRefused("publication dispatch is duplicated or cannot be proven unique")


def compile_payload(payload, request_id, source, run_id):
    _shape(payload, {"schema", "request_id", "request_sha256", "repository", "project", "source_sha",
                     "input", "input_sha256", "programme_sha256", "issued_at"})
    if (payload["schema"] != "dark-factory/publication-dispatch-v1"
            or payload["request_id"] != request_id or payload["source_sha"] != source):
        raise IntentRefused("publication encrypted payload binding refused")
    manifest = {key: payload[key] for key in ("request_id", "request_sha256", "repository", "project",
                                             "source_sha", "input", "input_sha256", "programme_sha256")}
    manifest.update(schema="dark-factory/validated-publication", schema_version="1.0", run_id=run_id, run_attempt=1)
    validate_manifest(manifest)
    # Approved content can still contain a pasted secret. Refuse before any public artifact.
    from scripts.factory_security import secret_findings
    if secret_findings([("approved-programme", canonical_bytes(manifest).decode("utf-8"))]):
        raise IntentRefused("approved publication contains a high-confidence secret")
    return manifest


def validated_manifest(github, request_id, source, run_id):
    prefix = f"repos/{policy.REPOSITORY}/actions/runs/{run_id}"
    rows = _complete(github.json(["api", prefix + "/artifacts?per_page=100"]), "artifacts")
    selected = [row for row in rows if row.get("name") == policy.ARTIFACT]
    if (len(selected) != 1 or selected[0].get("expired") is not False
            or type(selected[0].get("size_in_bytes")) is not int
            or not 0 < selected[0]["size_in_bytes"] <= 1000000):
        raise IntentRefused("validated publication artifact unavailable")
    artifact = selected[0]
    if (artifact.get("workflow_run", {}).get("id") != run_id
            or artifact["workflow_run"].get("head_sha") != source
            or artifact["workflow_run"].get("head_branch") != "main"):
        raise IntentRefused("validated publication artifact source differs")
    jobs = _complete(github.json(["api", prefix + "/attempts/1/jobs?per_page=100"]), "jobs")
    completed = [job for job in jobs if job.get("name") == "validate-publication"]
    if (len(completed) != 1 or completed[0].get("head_sha") != source or completed[0].get("run_id") != run_id
            or completed[0].get("status") != "completed" or completed[0].get("conclusion") != "success"):
        raise IntentRefused("publication validation job has not succeeded")
    manifest, _digest = read_manifest_archive(download_archive(github, artifact["id"]), artifact)
    validate_manifest(manifest)
    if manifest["request_id"] != request_id or manifest["source_sha"] != source or manifest["run_id"] != run_id:
        raise IntentRefused("validated publication belongs to another dispatch")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("validate", "publish", "wait", "merge"))
    phase = parser.parse_args().phase
    publisher = None
    try:
        github = GitHubClient(policy.REPOSITORY, cwd=Path.cwd())
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        request_id, source, run_id = authorize_job(os.environ, event, github)
        checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if checkout != source:
            raise IntentRefused("publication checkout is not its protected workflow source")
        output = Path(os.environ["RUNNER_TEMP"]) / "publication-outcome"
        output.mkdir(mode=0o700, exist_ok=True)
        if phase == "validate":
            with tempfile.TemporaryDirectory(prefix="publication-private-") as directory:
                identity = Path(directory) / "identity.age"
                identity.touch(mode=0o600)
                identity.write_text(os.environ["FRONTDOOR_AGE_IDENTITY"])
                payload = AgeCipher(identity).decrypt(event["inputs"]["ciphertext"], limit=MAX_CIPHERTEXT)
            manifest = compile_payload(payload, request_id, source, run_id)
            refuse_replay(github, request_id, run_id, payload["issued_at"])
            publisher = ProgrammePublisher(github, manifest, EffectJournal(output / "validate.json", manifest))
            publisher.before("branch")
            target = Path(os.environ["RUNNER_TEMP"]) / "publication-validated"
            target.mkdir(mode=0o700)
            (target / "manifest.json").write_bytes(canonical_bytes(manifest))
        else:
            manifest = validated_manifest(github, request_id, source, run_id)
            # Recheck uniqueness using this platform run's creation time. The initial job
            # already bound the private issued_at; current owner currency still expires it.
            run = github.json(["api", f"repos/{policy.REPOSITORY}/actions/runs/{run_id}"])
            created = int(datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).timestamp())
            refuse_replay(github, request_id, run_id, created)
            if phase == "wait":
                number = int(os.environ["PUBLICATION_PR"])
                if number <= 0:
                    raise IntentRefused("invalid publication PR")
                github.run(["pr", "checks", str(number), "-R", policy.REPOSITORY,
                            "--required", "--watch", "--fail-fast", "--interval", "10"], timeout=1320)
            else:
                publisher = ProgrammePublisher(github, manifest, EffectJournal(output / (phase + ".json"), manifest))
                if phase == "publish":
                    result = publisher.publish()
                    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                        stream.write(f"pr={result['pr']}\nhead={result['head_sha']}\n")
                else:
                    number, head = int(os.environ["PUBLICATION_PR"]), _hex(os.environ["PUBLICATION_HEAD"], 40)
                    publisher.merge(number, head)
                    result = publisher.observe_merged(number, head)
                    (output / "receipt.json").write_bytes(canonical_bytes(result))
        print("PROGRAMME_PUBLICATION_PHASE_OBSERVED " + phase)
        return 0
    except Exception:
        # No exception text, plaintext input, secret or raw API argv reaches public logs.
        if phase == "merge" and publisher is not None and publisher.journal.record["effects"]:
            try:
                request_stop(publisher.github, request_id=publisher.manifest["request_id"],
                             reason=f"Automatic containment: programme publication run {publisher.manifest['run_id']} "
                                    "has an uncertain or unverified merge. Reconcile its immutable evidence before resuming.")
                print("PROGRAMME_PUBLICATION_CONTAINMENT_ACKNOWLEDGED")
            except Exception:
                print("PROGRAMME_PUBLICATION_CONTAINMENT_UNCONFIRMED")
        print("PROGRAMME_PUBLICATION_REFUSED " + phase)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
