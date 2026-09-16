"""Authenticate a current kernel refusal from platform facts and digest-verified bytes.

This authenticates a reported outcome, not a causal finding or transferable qualification.
Requests provide locators only. No archive paths, credentials or claimed verdicts are input.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import re
import stat
import subprocess
from zipfile import BadZipFile, ZipFile

from .canonical import sha256_bytes, sha256_value
from .credential_env import scoped_environment
from .evidence_retention import MAX_BYTES, MAX_FILE, MAX_FILES, safe_index, source_binding
from .evidence_retention_archive import artifact_observation
from .feedback_receipt import FILE, marker
from .frontdoor_intent import IntentRefused, _shape
from .programme import parse_json
from .programme_runtime import ProgrammeQueue
from .publication_observation import _complete
from .refusal import AUTHORITY, scrub
from .trajectory import oid, positive, timestamp, validate_source

MAX_ARCHIVE = MAX_BYTES + 100000


def download_archive(github, artifact_id):
    identity = positive(artifact_id)
    result = subprocess.run(["gh", "api", f"repos/{github.repository}/actions/artifacts/{identity}/zip"],
        cwd=github.cwd, env=scoped_environment(scope="github"), capture_output=True, timeout=60, check=False)
    if result.returncode or len(result.stdout) > MAX_ARCHIVE:
        raise IntentRefused("feedback artifact download unavailable or over bound")
    return result.stdout


def read_archive(raw, artifact):
    """Never extract. Bound compressed bytes, members and cumulative expanded bytes."""
    if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ARCHIVE
            or artifact["digest"] != "sha256:" + sha256_bytes(raw)):
        raise IntentRefused("feedback archive digest mismatch or size exceeded")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not 0 < len(entries) <= MAX_FILES or sum(x.file_size for x in entries) > MAX_BYTES:
                raise IntentRefused("feedback archive expanded size exceeded")
            result = {}
            for entry in entries:
                parts = entry.filename.split("/")
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (entry.filename in result or any(x in {"", ".", ".."} for x in parts)
                        or "\\" in entry.filename or ":" in entry.filename
                        or mode not in {0, stat.S_IFREG} or entry.flag_bits & 1
                        or not 0 < entry.file_size <= MAX_FILE):
                    raise IntentRefused("feedback archive has an unsafe or duplicate member")
                result[entry.filename] = archive.read(entry)
            return result
    except (BadZipFile, RuntimeError, UnicodeError, ValueError, OSError) as exc:
        raise IntentRefused("feedback archive cannot be verified") from exc


def _refusal(raw, receipt):
    row = parse_json(raw.decode("utf-8"))
    _shape(row, {"version", "pr", "head", "base", "stage", "stage_context", "reason_code", "authority",
                 "tool", "phase", "rc", "exception", "detail", "timestamp"})
    if (row["version"] != "1.0" or positive(row["pr"]) != receipt["pr"]
            or row["head"] != receipt["head_sha"] or row["base"] != receipt["base_sha"]
            or row["reason_code"] not in AUTHORITY or row["authority"] != AUTHORITY[row["reason_code"]]
            or (row["rc"] is not None and type(row["rc"]) is not int)
            or any(not isinstance(row[key], str) or len(row[key]) > 2000 for key in row.keys() - {"pr", "rc"})):
        raise IntentRefused("feedback refusal identity is invalid")
    timestamp(row["timestamp"])
    return {**row, "detail": scrub(row["detail"])}


def observe_feedback(github, request, *, download=download_archive, now=None):
    """Fail closed on missing, expired, stale, ambiguous or racing evidence."""
    try:
        return _observe(github, request, download, now or datetime.now(timezone.utc))
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise IntentRefused("factory feedback provenance unavailable, stale or inconsistent") from exc


def _observe(github, request, download, now):
    _shape(request, {"run_id", "attempt", "pr", "item_id"})
    run_id, attempt, pr_number = (positive(request[k]) for k in ("run_id", "attempt", "pr"))
    prefix = f"repos/{github.repository}"
    def get(endpoint):
        return github.json(["api", f"{prefix}/{endpoint}"])

    run = get(f"actions/runs/{run_id}")
    validate_source(run, repository=github.repository, run_id=run_id, attempt=attempt)
    historical = get(f"actions/runs/{run_id}/attempts/{attempt}")
    # The attempt endpoint can omit unrelated fields. Compare execution identities.
    if any(historical.get(k) != run.get(k) for k in
           ("id", "run_attempt", "head_sha", "path", "event", "status", "conclusion")):
        raise IntentRefused("feedback attempt differs from current run")
    repository_id = positive(run["repository"]["id"])
    if (run["head_repository"]["id"] != repository_id
            or run["head_repository"]["full_name"] != github.repository):
        raise IntentRefused("feedback workflow originated outside the repository")
    revision = oid(run["head_sha"])
    branch = get("branches/main")
    if branch.get("protected") is not True or branch["commit"]["sha"] != revision:
        raise IntentRefused("feedback requires the current protected revision")
    queue = ProgrammeQueue(github, "main")
    programme = queue.current()
    if programme is None or programme.strategy is None:
        raise IntentRefused("feedback requires a current strategy programme")
    inventory = queue.inventory(programme)
    issue = inventory[request["item_id"]]
    pr = get(f"pulls/{pr_number}")
    head = oid(pr["head"]["sha"])
    if (pr.get("number") != pr_number or pr.get("state") != "open" or pr.get("merged") is not False
            or pr["base"]["sha"] != revision or pr["base"]["ref"] != "main"
            or any(pr[side]["repo"]["id"] != repository_id or
                   pr[side]["repo"]["full_name"] != github.repository for side in ("base", "head"))
            or pr["user"].get("type") != "Bot" or pr["user"].get("login") != programme.app_login
            or re.findall(r"(?im)^\s*Fixes\s+#([0-9]+)\b", pr.get("body") or "") != [str(issue["number"])]):
        raise IntentRefused("feedback PR is not the current programme member revision")
    jobs = _complete(get(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"), "jobs")
    dispatch = [job for job in jobs if job.get("name") == "dispatch"]
    required = {"Stage bounded dispatch evidence", "Retain dispatch evidence", "Retain dispatch evidence index"}
    if (len(dispatch) != 1 or dispatch[0].get("run_id") != run_id
            or dispatch[0].get("head_sha") != revision or dispatch[0].get("status") != "completed"
            or any(len([s for s in dispatch[0]["steps"] if s.get("name") == name
                        and s.get("status") == "completed" and s.get("conclusion") == "success"]) != 1
                   for name in required)):
        raise IntentRefused("feedback retention job did not complete")
    artifacts = _complete(get(f"actions/runs/{run_id}/artifacts?per_page=100"), "artifacts")
    packages, identities = {}, {}
    for kind in ("evidence-index", "evidence"):
        name = f"dark-factory-{kind}-dispatch-{run_id}-{attempt}"
        selected = [row for row in artifacts if row.get("name") == name]
        if len(selected) != 1:
            raise IntentRefused("feedback artifact missing or ambiguous")
        artifact = selected[0]
        observed = artifact_observation(artifact, source=run, name=name, limit=MAX_ARCHIVE)
        if (observed["expired_at_observation"] or datetime.fromisoformat(observed["expires_at"].replace("Z", "+00:00")) <= now
                or not run["created_at"] <= observed["created_at"] <= run["updated_at"]):
            raise IntentRefused("feedback artifact expired or outside run lifetime")
        packages[kind] = read_archive(download(github, observed["id"]), artifact)
        identities[kind] = observed
    if set(packages["evidence-index"]) != {"retention-index.json"}:
        raise IntentRefused("feedback index archive is ambiguous")
    binding = source_binding(repository=github.repository, run_id=run_id, attempt=attempt,
                             source_revision=revision, phase="dispatch")
    index = safe_index(packages["evidence-index"]["retention-index.json"], binding=binding)
    files = packages["evidence"]
    if (index["checkout_observation"]["checkout_revision"] != revision
            or set(files) != {row["path"] for row in index["files"]}
            or any(len(files[row["path"]]) != row["bytes"] or sha256_bytes(files[row["path"]]) != row["sha256"]
                   for row in index["files"])):
        raise IntentRefused("feedback evidence differs from exact checkout or index")
    matches = []
    for path, raw in files.items():
        if path.endswith("/artifacts/" + FILE):
            value = parse_json(raw.decode("utf-8"))
            if value.get("pr") == pr_number:
                matches.append((path, value))
    if len(matches) != 1:
        raise IntentRefused("feedback needs one kernel refusal receipt for this PR")
    path, receipt = matches[0]
    refusal_raw = files[path.rsplit("/", 1)[0] + "/validation-refusal.json"]
    expected = {"schema": "dark-factory/factory-feedback", "schema_version": "1.0",
        "repository": github.repository, "run_id": run_id, "run_attempt": attempt,
        "kernel_revision": revision, "programme_sha256": programme.sha256,
        "item_id": request["item_id"], "issue": issue["number"], "pr": pr_number,
        "head_sha": head, "base_sha": revision, "outcome": "validation-refused",
        "refusal_sha256": sha256_bytes(refusal_raw), "cause": "unresolved",
        "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}
    if sha256_value(receipt) != sha256_value(expected):
        raise IntentRefused("feedback receipt binding differs")
    refusal = _refusal(refusal_raw, receipt)
    comments = get(f"issues/{pr_number}/comments?per_page=100")
    if not isinstance(comments, list) or len(comments) >= 100:
        raise IntentRefused("feedback comment inventory exceeds bound")
    witnesses = [row for row in comments if row.get("user", {}).get("login") == "github-actions[bot]"
        and row["user"].get("type") == "Bot" and row.get("created_at") == row.get("updated_at")
        and run["created_at"] <= timestamp(row.get("created_at")) <= run["updated_at"]
        and marker(receipt) in str(row.get("body") or "").splitlines()]
    if len(witnesses) != 1 or not run["created_at"] <= refusal["timestamp"] <= run["updated_at"]:
        raise IntentRefused("feedback lacks the kernel's unedited platform receipt")
    # Re-read mutable platform boundaries after all downloads. A rerun, rehead, replacement,
    # artifact deletion/overwrite or receipt edit during observation cannot enter the ledger.
    if (get("branches/main") != branch or get(f"pulls/{pr_number}") != pr
            or get(f"actions/runs/{run_id}") != run
            or get(f"issues/{pr_number}/comments?per_page=100") != comments
            or queue.inventory(programme) != inventory):
        raise IntentRefused("feedback platform state changed during import")
    for observed in identities.values():
        fresh = get(f"actions/artifacts/{observed['id']}")
        if artifact_observation(fresh, source=run, name=observed["name"], limit=MAX_ARCHIVE) != observed:
            raise IntentRefused("feedback artifact changed during import")
    return {"receipt": receipt, "refusal": refusal, "artifacts": identities,
        "index_sha256": index["index_sha256"], "evidence_files": index["files"],
        "receipt_comment_id": positive(witnesses[0]["id"]),
        "provenance": "canonical-worker-artifact-and-kernel-receipt",
        "cause": "unresolved", "independent_strategy_rejection": "not-established",
        "qualification_status": "UNPROVEN", "proof_reuse_allowed": False,
        "programme_input": programme.to_input()}
