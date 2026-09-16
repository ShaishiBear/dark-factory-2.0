"""Independently collect and verify publication provenance from GitHub, without effects."""
from __future__ import annotations

import hashlib
from io import BytesIO
import re
import stat
import subprocess
from zipfile import BadZipFile, ZipFile

from .credential_env import scoped_environment
from .frontdoor_intent import IntentRefused
from .programme import parse_json
from .publication_admission import admit_publication, validate_manifest
from . import publication_policy as policy

MARKER = re.compile(r"^<!-- dark-factory-publication:([1-9][0-9]{0,19}):([a-f0-9]{32}) -->$", re.MULTILINE)


def read_manifest_archive(raw, artifact):
    """Verify the platform digest, then read one bounded file in memory; never extract paths."""
    if (not isinstance(raw, bytes) or not 0 < len(raw) <= 1000000
            or artifact.get("digest") != "sha256:" + hashlib.sha256(raw).hexdigest()):
        raise IntentRefused("publication archive digest or size refused")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (len(entries) != 1 or entries[0].filename != "manifest.json"
                    or not 0 < entries[0].file_size <= 250000
                    or stat.S_ISLNK(entries[0].external_attr >> 16)
                    or entries[0].flag_bits & 1):
                raise IntentRefused("publication archive must contain only the bounded regular manifest")
            manifest = parse_json(archive.read(entries[0]).decode("utf-8"))
    except (BadZipFile, RuntimeError, UnicodeError, ValueError) as exc:
        raise IntentRefused("publication archive cannot be verified") from exc
    return manifest, hashlib.sha256(raw).hexdigest()


def download_archive(github, artifact_id):
    if type(artifact_id) is not int or artifact_id <= 0:
        raise IntentRefused("invalid publication artifact identity")
    result = subprocess.run(["gh", "api", f"repos/{github.repository}/actions/artifacts/{artifact_id}/zip"],
                            cwd=github.cwd, env=scoped_environment(scope="github"),
                            capture_output=True, timeout=60, check=False)
    if result.returncode or len(result.stdout) > 1000000:
        raise IntentRefused("publication artifact download unavailable")
    return result.stdout


def _complete(payload, key):
    if (not isinstance(payload, dict) or type(payload.get("total_count")) is not int
            or not isinstance(payload.get(key), list) or not 0 <= payload["total_count"] <= 100
            or len(payload[key]) != payload["total_count"]):
        raise IntentRefused("publication provenance inventory is incomplete or exceeds its bound")
    return payload[key]


def observe_publication(github, *, pr_number, base_sha, head_sha, changed_files, file_mode,
                        base_input, head_input, download=download_archive, current=None, now=None):
    """PR text locates evidence only. All returned authority is independently re-derived.

    current, when supplied by the protected-base caller, is the authenticated host exchange.
    The head quick gate uses public provenance only and must never receive the age identity.
    """
    if github.repository != policy.REPOSITORY or type(pr_number) is not int or pr_number <= 0:
        raise IntentRefused("publication repository or PR identity refused")
    prefix = f"repos/{policy.REPOSITORY}"
    pr = github.json(["api", f"{prefix}/pulls/{pr_number}"])
    body = pr.get("body")
    if not isinstance(body, str) or len(body) > 100000:
        return None
    matches = MARKER.findall(body)
    if not matches:
        return None
    if len(matches) != 1:
        raise IntentRefused("ambiguous publication run locator")
    run_id, request_id = int(matches[0][0]), matches[0][1]
    run = github.json(["api", f"{prefix}/actions/runs/{run_id}"])
    jobs = _complete(github.json(["api", f"{prefix}/actions/runs/{run_id}/attempts/1/jobs?per_page=100"]), "jobs")
    artifacts = _complete(github.json(["api", f"{prefix}/actions/runs/{run_id}/artifacts?per_page=100"]), "artifacts")
    selected = [row for row in artifacts if row.get("name") == policy.ARTIFACT]
    if len(selected) != 1 or selected[0].get("expired") is not False:
        raise IntentRefused("publication artifact is missing, ambiguous or expired")
    artifact = selected[0]
    if type(artifact.get("size_in_bytes")) is not int or not 0 < artifact["size_in_bytes"] <= 1000000:
        raise IntentRefused("publication artifact exceeds its bound")
    manifest, digest = read_manifest_archive(download(github, artifact["id"]), artifact)
    validate_manifest(manifest)
    if manifest.get("run_id") != run_id or manifest.get("request_id") != request_id:
        raise IntentRefused("publication locator and validated artifact differ")
    raw_commits = github.json(["api", f"{prefix}/pulls/{pr_number}/commits?per_page=100"])
    if not isinstance(raw_commits, list) or pr.get("commits") != len(raw_commits) or len(raw_commits) != 1:
        raise IntentRefused("publication commit inventory is not exactly one complete commit")
    commits = [{"sha": row.get("sha"), "author": row.get("author"), "committer": row.get("committer"),
                "parents": [{"sha": parent.get("sha")} for parent in row.get("parents", [])]}
               for row in raw_commits]
    result = admit_publication(manifest=manifest, pr=pr, commits=commits, run=run, jobs=jobs,
                               artifact=artifact, artifact_sha256=digest, changed_files=changed_files,
                               file_mode=file_mode, base_input=base_input, head_input=head_input,
                               base_sha=base_sha, head_sha=head_sha, now=now)
    if current is not None:
        currency = current(manifest, "merge")
        if (currency.get("input_sha256") != manifest["input_sha256"]
                or currency.get("programme_sha256") != manifest["programme_sha256"]
                or currency.get("request_sha256") != manifest["request_sha256"]
                or currency.get("main_sha") != base_sha or currency.get("decision") != "current-owner-request"):
            raise IntentRefused("publication provenance no longer has current exact owner consent")
        result["owner_currency"] = "observed-current"
    else:
        result["owner_currency"] = "not-assessed-by-head-check"
    return result
