"""Bounded project-local attempt observations. Never consumed by proof or merge authority.

Read completed canonical worker runs, preserve repeated stage attempts and record gaps rather
than inventing missing evidence. Raw prompts, model output, exception text and cookies are not
copied into the durable metadata archive. Diagnostic artifact references retain their hashes.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
from pathlib import Path
import re

from .canonical import canonical_bytes, sha256_bytes
from .programme import parse_json

WORKER_PATH = ".github/workflows/dark-factory-worker.yml"
MAX_RECORD = 250000
MAX_FILE = 2000000
MAX_STAGES = 500
ARTIFACTS = frozenset({
    "attached-contract.json", "attached-proof.json", "contract.json", "context.json", "design.json",
    "test-spec.json", "proof.json", "review-spec.json", "review-standards.json",
    "architecture-governor.json", "architecture-conformance.json", "holdout.json",
    "architecture-holdout.json", "contract-certification.json", "design-certification.json",
    "architecture-governor-certification.json", "evidence-bundle.json", "merge-authorization.json",
    "merge-verification.json", "post-merge.json", "failure.json", "issue.json", "publish-handoff.json",
})


class TrajectoryRefused(ValueError):
    pass


def oid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise TrajectoryRefused("invalid trajectory revision")
    return value


def positive(value):
    if type(value) is not int or value < 1:
        raise TrajectoryRefused("positive integer identity required")
    return value


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise TrajectoryRefused("UTC timestamp required")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def validate_source(run, *, repository, run_id, attempt):
    if (run.get("repository", {}).get("full_name") != repository or positive(run.get("id")) != positive(run_id)
            or positive(run.get("run_attempt")) != positive(attempt) or run.get("path") != WORKER_PATH
            or run.get("head_branch") != "main" or run.get("event") not in {"schedule", "workflow_dispatch"}
            or run.get("status") != "completed"
            or run.get("conclusion") not in {"success", "failure", "cancelled", "timed_out", "startup_failure",
                                             "action_required", "skipped", "neutral", "stale"}):
        raise TrajectoryRefused("trajectory source is not a completed canonical main worker attempt")
    oid(run.get("head_sha"))
    timestamp(run.get("created_at"))
    timestamp(run.get("updated_at"))
    return run


def _number(value, *, integer=False):
    if value is None:
        return None
    if (type(value) not in ({int} if integer else {int, float}) or not math.isfinite(value)
            or not 0 <= value <= 100000000):
        raise TrajectoryRefused("invalid bounded stage measurement")
    return value


def stage_summary(row, *, ordinal, models):
    kind, name = row.get("kind"), row.get("name")
    if kind not in {"agent", "exec"} or not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", name):
        raise TrajectoryRefused("invalid stage identity")
    outcome = row.get("outcome")
    if outcome not in {"ok", "error", "failed", "refused", "timeout", "cap_reached"}:
        raise TrajectoryRefused("unknown stage outcome")
    effort = row.get("effort")
    if effort not in {None, "low", "medium", "high", "xhigh", "max"}:
        raise TrajectoryRefused("unknown recorded effort")
    model = row.get("model")
    exit_code = row.get("rc")
    if exit_code is not None and (type(exit_code) is not int or not -255 <= exit_code <= 255):
        raise TrajectoryRefused("invalid stage exit code")
    flags = {}
    for name_of_flag in ("cap_reached", "draft_deadline_missed", "idle_killed"):
        flag = row.get(name_of_flag)
        if flag is not None and type(flag) is not bool:
            raise TrajectoryRefused("invalid stage termination flag")
        flags[name_of_flag] = flag
    # A model identifier is retained only if present in this run's public committed config.
    # Arbitrary strings from an artifact do not become a secret-exfiltration channel.
    return {"stage": name, "kind": kind, "ordinal": ordinal,
            "attempt": _number(row.get("stage_run"), integer=True),
            "provider_attempts": _number(row.get("attempts"), integer=True),
            "model": model if model in models else None, "effort": effort,
            "started_at": timestamp(row.get("started_at")), "ended_at": timestamp(row.get("ended_at")),
            "turns": _number(row.get("num_turns"), integer=True), "wall_seconds": _number(row.get("seconds")),
            "cost": {"currency": "USD", "amount": _number(row.get("cost_usd"))},
            "thinking_tokens": _number(row.get("thinking_tokens"), integer=True),
            "events_seen": _number(row.get("events_seen"), integer=True), "result": outcome,
            "exit_code": exit_code, "termination_flags": flags}


def _read(path, root):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > MAX_FILE:
        raise TrajectoryRefused("artifact escapes its bounded observation directory")
    return path.read_bytes()


def summarize(run, *, repository, directories, models, gaps=()):
    validate_source(run, repository=repository, run_id=run["id"], attempt=run["run_attempt"])
    records, observed_issues, observed_prs = [], set(), set()
    missing = list(gaps)
    for phase, root in sorted(directories.items()):
        if phase not in {"dispatch", "merge"}:
            raise TrajectoryRefused("unknown trajectory phase")
        root = Path(root)
        if root.is_symlink():
            raise TrajectoryRefused("observation directory cannot be a symlink")
        attempts = sorted(path for path in root.iterdir() if path.is_dir())
        if len(attempts) > 20:
            raise TrajectoryRefused("too many kernel attempts")
        for attempt_dir in attempts:
            if attempt_dir.is_symlink() or not re.fullmatch(
                    r"(?:issue-[0-9]+-a[0-9]+-[0-9a-f]{10}|(?:pr|merge|rehead|resume)-[0-9]+-[0-9a-f]{12})", attempt_dir.name):
                missing.append({"phase": phase, "reason": "unrecognized-kernel-attempt"})
                continue
            record = {"phase": phase, "kernel_run": attempt_dir.name, "stages": [], "artifact_refs": [],
                      "bindings": {}, "gaps": []}
            subject = int(attempt_dir.name.split("-")[1])
            (observed_issues if attempt_dir.name.startswith("issue-") else observed_prs).add(subject)
            timings = attempt_dir / "transcripts/stage-timings.jsonl"
            if timings.is_file():
                lines = _read(timings, root).decode("utf-8").splitlines()
                if len(lines) > MAX_STAGES:
                    raise TrajectoryRefused("stage inventory exceeds its bound")
                for ordinal, line in enumerate(lines, 1):
                    try:
                        record["stages"].append(stage_summary(parse_json(line), ordinal=ordinal, models=models))
                    except (ValueError, TypeError, KeyError, AttributeError):
                        record["gaps"].append({"reason": "invalid-stage-record", "ordinal": ordinal})
            else:
                record["gaps"].append({"reason": "stage-timings-unavailable"})
            for name in sorted(ARTIFACTS):
                path = attempt_dir / "artifacts" / name
                if not path.is_file():
                    continue
                raw = _read(path, root)
                record["artifact_refs"].append({"path": f"{attempt_dir.name}/artifacts/{name}",
                                                "sha256": sha256_bytes(raw), "bytes": len(raw)})
                if name not in {"issue.json", "evidence-bundle.json", "merge-verification.json", "post-merge.json"}:
                    continue
                try:
                    data = parse_json(raw.decode("utf-8"))
                    for key in ("base_sha", "head_sha", "merge_sha", "tree_sha"):
                        if key in data:
                            record["bindings"][f"{name}:{key}"] = oid(data[key])
                    if name == "issue.json":
                        observed_issues.add(positive(data["number"]))
                        body = data.get("body", "")
                        marker = re.search(r"^<!-- dark-factory-programme:([0-9a-f]{64}):([a-z][a-z0-9-]{0,63}) -->", body)
                        spec = re.search(r"(?m)^Specification SHA256: ([0-9a-f]{64})$", body)
                        if marker and spec:
                            record["programme_item"] = {"programme_sha256": marker[1], "item_id": marker[2],
                                                         "spec_sha256": spec[1]}
                    if name == "evidence-bundle.json":
                        observed_issues.add(positive(data["issue"]))
                        observed_prs.add(positive(data["pr"]))
                except (ValueError, KeyError, TypeError):
                    record["gaps"].append({"reason": "invalid-artifact-metadata", "artifact": name})
            records.append(record)
    if not records:
        missing.append({"reason": "no-kernel-artifacts"})
    value = {"schema": "dark-factory/trajectory", "schema_version": "1.0",
             "trajectory_id": f"traj_{run['id']}_{run['run_attempt']}", "run_id": run["id"],
             "run_attempt": run["run_attempt"], "repository": repository,
             "project_id": "proj_" + hashlib.sha256(repository.encode()).hexdigest()[:24],
             "learning_scope": "project-local", "authority": "observation-only",
             "source_workflow": WORKER_PATH, "source_revision": run["head_sha"],
             "configuration_ref": {"revision": run["head_sha"], "path": ".factory/kernel.json"},
             "started_at": run["created_at"], "ended_at": run["updated_at"], "outcome": run["conclusion"],
             "issues": sorted(observed_issues), "pull_requests": sorted(observed_prs),
             "attempts": records, "gaps": missing}
    if len(canonical_bytes(value)) > MAX_RECORD:
        raise TrajectoryRefused("trajectory exceeds its durable metadata bound")
    return value
