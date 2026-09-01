#!/usr/bin/env python3
"""Build the genesis validation result from GitHub's own execution record.

An earlier design had each stage job write a structured result file and upload it. A throwaway
probe disproved that boundary empirically: a stage printed an honest measurement, exited zero, and
a detached child it had spawned -- inherited descriptors closed -- rewrote the driver's output
after the driver wrote it and before the upload step read it. Every binding survived intact
(candidate SHA, tree, driver digest, recipe digest, exit status) while the measurement became
999999, and the job still reported success. The runner's own "Cleaning up orphan processes" ran
only after the upload.

Recomputing the driver's digest from the object store proves the right program ran. It says
nothing about whether that program's output survived to the artifact. Any authority reading a file
a candidate process can still reach is reading a candidate-writable document.

So nothing authoritative is produced while candidate code can be alive. A stage job executes the
candidate and terminates. What remains is GitHub's record of that job: its conclusion and its log,
owned by GitHub, sealed when the job ends, and beyond the reach of any process the candidate left
behind. This collector runs on a separate disposable runner, reads that record, and constructs the
structured result there.

The chain is:

    candidate-controlled process -> runner terminates -> GitHub-owned record -> pinned authority

rather than:

    candidate-controlled process -> same-user file -> upload the candidate can still modify

What this does and does not defend. It defends the *integrity of the record*: a measurement cannot
be altered after the stage that produced it has ended. It does not stop a candidate's own program
from printing a false number in the first place -- that program is pinned by the manifest and
reviewed by a human at genesis, which is where that question belongs. The ambiguity rule matters
more here than anywhere else: if a stage emits a marker twice with different values, one of them
being a forgery attempt, the disagreement is a refusal rather than a first match.

This program makes no network calls. The workflow fetches the job metadata and logs with read-only
Actions authority and scrubs the token before invoking it, so the token is never ambient in the
process that builds the evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SELF = Path(__file__).resolve()
STAGE_JOB = re.compile(r"^(?P<prefix>[A-Za-z0-9_-]+)\s+\((?P<stage>[^)]+)\)$")


def fail(message: str) -> None:
    print(f"COLLECTION_REFUSED {message}", file=sys.stderr)
    raise SystemExit(1)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {label}: {exc}")
    if not isinstance(value, dict):
        fail(f"{label} must be a JSON object")
    return value


def measure(stage: str, log: str, key: str, marker: str) -> int:
    """Read one count from one stage's own GitHub log, refusing ambiguity.

    Disagreeing occurrences are the signature of a stage printing a forged marker alongside the
    real one, so they are a refusal rather than a first match.
    """
    found = re.findall(marker, log)
    if not found:
        fail(f"stage {stage!r} log does not report {key}")
    unique = set(found)
    if len(unique) != 1:
        fail(f"stage {stage!r} reported {key} ambiguously: {sorted(unique)}")
    return int(found[0])


def collect_stage_jobs(jobs: list, prefix: str) -> dict[str, dict]:
    seen: dict[str, dict] = {}
    for job in jobs:
        if not isinstance(job, dict):
            fail("job record is not an object")
        match = STAGE_JOB.match(str(job.get("name") or ""))
        if match is None or match.group("prefix") != prefix:
            continue
        stage = match.group("stage")
        if stage in seen:
            fail(f"stage {stage!r} appears in more than one job of this run")
        seen[stage] = job
    return seen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True, help="the run's jobs, as returned by the Actions API")
    parser.add_argument("--logs-dir", required=True, help="one log per stage job, named by job id")
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workflow-commit", required=True, help="the head this workflow run itself ran from")
    parser.add_argument("--commit", required=True, help="the candidate commit under validation")
    parser.add_argument("--tree", required=True, help="the candidate tree under validation")
    parser.add_argument("--stage-job-prefix", default="stage")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    recipe_path = Path(args.recipe).resolve()
    if not recipe_path.is_file():
        fail(f"validation recipe does not exist: {recipe_path}")
    recipe_bytes = recipe_path.read_bytes()
    try:
        recipe = json.loads(recipe_bytes)
    except json.JSONDecodeError as exc:
        fail(f"validation recipe is not valid JSON: {exc}")
    expected = [str(s.get("name") or "") for s in recipe.get("stages") or []]
    if not expected or not all(expected):
        fail("validation recipe defines no stages")

    record = read_json(Path(args.jobs).resolve(), "Actions job record")
    jobs = record.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        fail("Actions job record contains no jobs")

    found = collect_stage_jobs(jobs, args.stage_job_prefix)
    missing = sorted(set(expected) - set(found))
    unexpected = sorted(set(found) - set(expected))
    if missing:
        fail("this run has no stage job for: " + ", ".join(missing))
    if unexpected:
        fail("this run has stage jobs the recipe does not define: " + ", ".join(unexpected))

    logs_dir = Path(args.logs_dir).resolve()
    stages = []
    for name in expected:
        job = found[name]
        job_id = job.get("id")
        if not isinstance(job_id, int):
            fail(f"stage {name!r} job has no identity")
        if str(job.get("run_id") or "") != str(args.run_id):
            fail(f"stage {name!r} job belongs to a different workflow run")
        if str(job.get("head_sha") or "") != args.workflow_commit:
            fail(f"stage {name!r} job did not run from the authorized workflow commit")
        if job.get("conclusion") != "success":
            fail(f"stage {name!r} concluded {job.get('conclusion')!r}, not success")

        log_path = logs_dir / f"{job_id}.log"
        if not log_path.is_file():
            fail(f"no GitHub log was captured for stage {name!r} (job {job_id})")
        log = log_path.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"EXACT_HEAD_OK {re.escape(args.commit)}\b", log):
            fail(f"stage {name!r} log does not prove it ran against the authorized candidate")
        if not re.search(rf"EXACT_TREE_OK {re.escape(args.tree)}\b", log):
            fail(f"stage {name!r} log does not prove it ran against the authorized tree")

        spec = next(s for s in recipe["stages"] if s["name"] == name)
        measurements = {
            key: measure(name, log, key, str(marker))
            for key, marker in sorted((spec.get("measures") or {}).items())
        }
        stages.append({
            "name": name,
            "argv": list(spec.get("argv") or []),
            "cwd": str(spec.get("cwd") or "."),
            "exit": 0,
            "job_id": job_id,
            "job_conclusion": "success",
            "measurements": measurements,
            "output_sha256": digest(log.encode()),
        })

    payload = {
        "version": "1.0",
        "aggregator_sha256": digest(SELF.read_bytes()),
        "recipe_sha256": digest(recipe_bytes),
        "candidate_sha": args.commit,
        "candidate_tree": args.tree,
        "workflow_run_id": str(args.run_id),
        "workflow_commit_sha": args.workflow_commit,
        "stage_isolation": "one-disposable-runner-per-stage",
        "evidence_source": "github-actions-job-record",
        "stages": stages,
        "failed_stages": [],
        "verdict": "pass",
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"COLLECTION_OK run={args.run_id} candidate={args.commit} tree={args.tree} "
        f"stages={len(stages)} collector={payload['aggregator_sha256'][:12]}"
    )


if __name__ == "__main__":
    main()
