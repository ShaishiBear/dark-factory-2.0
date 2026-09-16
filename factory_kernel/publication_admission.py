"""Judge exact programme-data publication provenance, never a generic App exception.

Platform observations must be collected independently by protected-base code. PR text only
locates a run; it is not evidence. This pure predicate does not fetch or authenticate a caller,
mint a capability, relax human-maintainer identity or authorize product completion.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from .canonical import sha256_value
from .frontdoor_intent import IntentRefused, _shape
from .programme import ACTIVE_PATH, compile_programme
from . import publication_policy as policy


def validate_manifest(value):
    _shape(value, {"schema", "schema_version", "repository", "project", "request_id", "request_sha256",
                   "source_sha", "run_id", "run_attempt", "input", "input_sha256", "programme_sha256"})
    if (value["schema"] != "dark-factory/validated-publication" or value["schema_version"] != "1.0"
            or value["repository"] != policy.REPOSITORY or value["project"] != policy.PROJECT
            or type(value["run_id"]) is not int or value["run_id"] <= 0
            or type(value["run_attempt"]) is not int or value["run_attempt"] != 1):
        raise IntentRefused("publication manifest identity refused")
    for field, size in (("request_id", 32), ("request_sha256", 64), ("source_sha", 40),
                        ("input_sha256", 64), ("programme_sha256", 64)):
        if not isinstance(value[field], str) or not re.fullmatch(r"[a-f0-9]{%d}" % size, value[field]):
            raise IntentRefused("publication manifest hash refused")
    programme = compile_programme(value["input"], repository=policy.REPOSITORY)
    if (programme.app_login != policy.APP_LOGIN or sha256_value(value["input"]) != value["input_sha256"]
            or programme.sha256 != value["programme_sha256"]):
        raise IntentRefused("publication manifest payload refused")
    return programme


def _time(value):
    if not isinstance(value, str):
        raise IntentRefused("missing publication timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise IntentRefused("publication timestamp has no timezone")
    return result


def _admit_publication(*, manifest, pr, commits, run, jobs, artifact, artifact_sha256,
                       changed_files, file_mode, base_input, head_input, base_sha, head_sha, now=None):
    """Read-only old-base decision over complete, source-bound platform facts.

    The adapter checks the archive digest before decoding its sole manifest. The validation
    job must have succeeded before the App creates the PR; the overall workflow may still be
    running because later jobs wait for this check. Fresh owner currency and stop remain
    separate required observations, immediately before effects and exact-head merge.
    """
    validate_manifest(manifest)
    if (not isinstance(base_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", base_sha)
            or not isinstance(head_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", head_sha)
            or base_sha == head_sha or base_sha != manifest["source_sha"]
            or base_input is not None or changed_files != [ACTIVE_PATH] or file_mode != "100644"
            or head_input != manifest["input"]):
        raise IntentRefused("publication must change exactly the approved regular data file from its source")
    app = {"login": policy.APP_LOGIN, "type": "Bot"}
    if (pr.get("state") != "open" or pr.get("draft") is not False
            or type(pr.get("number")) is not int or pr["number"] <= 0
            or any(pr.get("user", {}).get(key) != value for key, value in app.items())
            or pr.get("base", {}).get("ref") != "main" or pr["base"].get("sha") != base_sha
            or pr.get("head", {}).get("sha") != head_sha
            or pr["head"].get("ref") != policy.BRANCH_PREFIX + manifest["request_id"]
            or any(pr[side].get("repo", {}).get("full_name") != policy.REPOSITORY for side in ("base", "head"))
            or type(pr.get("commits")) is not int or pr["commits"] != 1
            or not isinstance(commits, list) or len(commits) != 1):
        raise IntentRefused("publication PR platform identity or complete commit inventory refused")
    commit = commits[0]
    if (commit.get("sha") != head_sha or commit.get("parents") != [{"sha": base_sha}]
            or any(any(commit.get(role, {}).get(key) != value for key, value in app.items())
                   for role in ("author", "committer"))):
        raise IntentRefused("publication commit must resolve to the App with exactly the approved parent")
    if (type(run.get("id")) is not int or run["id"] != manifest["run_id"]
            or type(run.get("run_attempt")) is not int or run["run_attempt"] != 1
            or run.get("event") != "workflow_dispatch" or run.get("path") != policy.WORKFLOW_PATH
            or run.get("head_branch") != "main" or run.get("head_sha") != base_sha
            or run.get("repository", {}).get("full_name") != policy.REPOSITORY
            or run.get("head_repository", {}).get("full_name") != policy.REPOSITORY
            or any(run.get(role, {}).get("login") != policy.OWNER or run[role].get("type") != "User"
                   for role in ("actor", "triggering_actor"))
            or run.get("status") not in {"in_progress", "completed"}
            or (run["status"] == "in_progress" and run.get("conclusion") is not None)
            or (run["status"] == "completed" and run.get("conclusion") != "success")):
        raise IntentRefused("publication source workflow is not the first owner attempt from protected main")
    matches = [job for job in jobs if job.get("name") == "validate-publication"]
    if (len(matches) != 1 or matches[0].get("run_id") != manifest["run_id"]
            or matches[0].get("head_sha") != base_sha or matches[0].get("status") != "completed"
            or matches[0].get("conclusion") != "success"):
        raise IntentRefused("publication validation job did not complete successfully for this source")
    job = matches[0]
    instant = now or datetime.now(timezone.utc)
    if not (_time(run.get("created_at")) <= _time(artifact.get("created_at"))
            <= _time(job.get("completed_at")) <= _time(pr.get("created_at")) <= instant
            < _time(artifact.get("expires_at"))):
        raise IntentRefused("publication provenance ordering or artifact expiry refused")
    workflow = artifact.get("workflow_run", {})
    if (type(artifact.get("id")) is not int or artifact["id"] <= 0
            or artifact.get("name") != policy.ARTIFACT or artifact.get("expired") is not False
            or type(artifact.get("size_in_bytes")) is not int or not 0 < artifact["size_in_bytes"] <= 1000000
            or not isinstance(artifact_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", artifact_sha256)
            or artifact.get("digest") != "sha256:" + artifact_sha256
            or workflow.get("id") != manifest["run_id"] or workflow.get("head_sha") != base_sha
            or workflow.get("head_branch") != "main"
            or workflow.get("repository_id") != run["repository"].get("id")
            or workflow.get("head_repository_id") != run["repository"].get("id")
            or type(run["repository"].get("id")) is not int or run["repository"]["id"] <= 0
            or run["head_repository"].get("id") != run["repository"]["id"]):
        raise IntentRefused("publication artifact provenance or digest refused")
    if any(pr[side]["repo"].get("id") != run["repository"]["id"] for side in ("base", "head")):
        raise IntentRefused("publication PR repository identity differs from its validated source")
    return {"lane": "programme-publication", "manifest_sha256": sha256_value(manifest),
            "request_id": manifest["request_id"], "request_sha256": manifest["request_sha256"],
            "input_sha256": manifest["input_sha256"], "programme_sha256": manifest["programme_sha256"],
            "source_run_id": manifest["run_id"], "artifact_id": artifact["id"],
            "base_sha": base_sha, "head_sha": head_sha, "unattended_merge_eligible": False}


def admit_publication(**facts):
    """Malformed/missing platform facts refuse; they never imply permissive defaults."""
    try:
        return _admit_publication(**facts)
    except IntentRefused:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise IntentRefused("publication provenance is malformed or incomplete") from exc
