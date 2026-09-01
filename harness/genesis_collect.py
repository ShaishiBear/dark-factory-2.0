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

Two classes of refusal, deliberately separated:

  * A *structural* refusal means the record is not a record of the run we asked about -- it is
    unreadable, truncated, missing or duplicating stage jobs, or carrying jobs from another run,
    another workflow commit or another run attempt. Nothing is written. Assembling a document
    from an untrusted record would itself be the defect.
  * A *stage* failure means the record is sound and reports that a stage did not pass, or did not
    prove what it was required to prove. Those are aggregated across every stage and written out
    as a `verdict: "fail"` document listing each reason, and the process still exits non-zero. A
    failed run is supposed to leave auditable evidence of why; an earlier version claimed that in
    the workflow comment while refusing at the first bad stage and writing nothing at all.

Run-attempt uniformity is part of the structural set for a reason that is not cosmetic. GitHub's
job listing returns the *latest* attempt of each job, so a partially re-run ladder would present
stages from attempt 1 beside re-run stages from attempt 2, all reporting success. That is a
re-roll-until-green channel: a stage that fails can simply be retried alone. Requiring one attempt
across the whole ladder closes it.

Proving *which program ran* is separate from proving *where it ran*. The head and tree markers
show the stage checked out the authorized candidate; they say nothing about whether the pinned
validation driver is what executed. The workflow's pin step decides that and prints its verdict,
so the collector requires that verdict to be in the log rather than inferring it from the job
having not failed. A stage that prints the marker itself cannot help: if the pin step disagreed
the two values collide and the ambiguity rule refuses; if it agreed the marker was already true.
Whether the pins were *asserted* or merely *printed for review* is recorded rather than judged --
that judgement belongs to the external policy, which is what makes a provisional calibration run
distinguishable from an authoritative one instead of merely differing by intent.

Record truncation is structural for a subtler reason. The listing is paginated, and the guard
against one stage appearing in two jobs can only fire on jobs it can see -- so a forged duplicate
on page one with the honest job stranded on page two would defeat it. Refusing any listing whose
`total_count` exceeds the jobs actually present removes that window.

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
PINS_ASSERTED = "LADDER_PINS_OK"
PINS_PROVISIONAL = "LADDER_PINS_PROVISIONAL"


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


def measure(stage: str, log: str, key: str, marker: str) -> tuple[int | None, str | None]:
    """Read one count from one stage's own GitHub log, refusing ambiguity.

    Disagreeing occurrences are the signature of a stage printing a forged marker alongside the
    real one, so they are a refusal rather than a first match. Returns the value or the reason it
    could not be read, so the caller can report every bad stage rather than only the first.
    """
    found = re.findall(marker, log)
    if not found:
        return None, f"stage {stage!r} log does not report {key}"
    unique = set(found)
    if len(unique) != 1:
        return None, f"stage {stage!r} reported {key} ambiguously: {sorted(unique)}"
    return int(found[0]), None


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
    # A paginated listing that was not read to the end can hide the second half of a duplicate
    # pair, which is exactly what the duplicate guard below exists to catch.
    total = record.get("total_count")
    if not isinstance(total, int) or total != len(jobs):
        fail(f"Actions job record is truncated: total_count {total!r}, {len(jobs)} jobs present")

    found = collect_stage_jobs(jobs, args.stage_job_prefix)
    missing = sorted(set(expected) - set(found))
    unexpected = sorted(set(found) - set(expected))
    if missing:
        fail("this run has no stage job for: " + ", ".join(missing))
    if unexpected:
        fail("this run has stage jobs the recipe does not define: " + ", ".join(unexpected))

    logs_dir = Path(args.logs_dir).resolve()
    attempts: set[int] = set()
    stage_pins: list[bool] = []
    stages = []
    failed: list[dict] = []
    for name in expected:
        job = found[name]
        job_id = job.get("id")
        if not isinstance(job_id, int):
            fail(f"stage {name!r} job has no identity")
        if str(job.get("run_id") or "") != str(args.run_id):
            fail(f"stage {name!r} job belongs to a different workflow run")
        if str(job.get("head_sha") or "") != args.workflow_commit:
            fail(f"stage {name!r} job did not run from the authorized workflow commit")
        attempt = job.get("run_attempt")
        if not isinstance(attempt, int):
            fail(f"stage {name!r} job does not record which run attempt produced it")
        attempts.add(attempt)

        # Everything below is a property of the stage rather than of the record, so a bad answer
        # is collected as a failed stage instead of ending the collection at the first one.
        reasons: list[str] = []
        if job.get("conclusion") != "success":
            reasons.append(f"stage {name!r} concluded {job.get('conclusion')!r}, not success")

        log_path = logs_dir / f"{job_id}.log"
        log_bytes = b""
        if not log_path.is_file():
            reasons.append(f"no GitHub log was captured for stage {name!r} (job {job_id})")
        else:
            log_bytes = log_path.read_bytes()
        # The binding is to the bytes GitHub served. Decoding is lossy on purpose -- runner logs
        # carry terminal escape sequences -- so the digest is taken before decoding, not after.
        log = log_bytes.decode("utf-8", errors="replace")
        if not re.search(rf"EXACT_HEAD_OK {re.escape(args.commit)}\b", log):
            reasons.append(f"stage {name!r} log does not prove it ran against the authorized candidate")
        if not re.search(rf"EXACT_TREE_OK {re.escape(args.tree)}\b", log):
            reasons.append(f"stage {name!r} log does not prove it ran against the authorized tree")
        # Which program ran, read from the record rather than assumed from the job's success.
        asserted = PINS_ASSERTED in log
        provisional = PINS_PROVISIONAL in log
        if asserted and provisional:
            reasons.append(f"stage {name!r} log both asserts and waives the driver pins")
        elif not asserted and not provisional:
            reasons.append(f"stage {name!r} log does not say whether the driver pins were checked")
        stage_pins.append(asserted and not provisional)

        spec = next(s for s in recipe["stages"] if s["name"] == name)
        measurements: dict[str, int] = {}
        for key, marker in sorted((spec.get("measures") or {}).items()):
            value, why = measure(name, log, key, str(marker))
            if why is not None:
                reasons.append(why)
            else:
                measurements[key] = int(value or 0)

        if reasons:
            failed.append({"name": name, "job_id": job_id,
                           "job_conclusion": job.get("conclusion"), "reasons": reasons})
        stages.append({
            "name": name,
            "argv": list(spec.get("argv") or []),
            "cwd": str(spec.get("cwd") or "."),
            "exit": 1 if reasons else 0,
            "job_id": job_id,
            "job_conclusion": job.get("conclusion"),
            "measurements": measurements,
            "output_sha256": digest(log_bytes),
        })

    # A ladder assembled from more than one attempt is a re-roll channel, not a run.
    if len(attempts) != 1:
        fail("this run's stage jobs span more than one run attempt: " + ", ".join(
            str(a) for a in sorted(attempts)))

    payload = {
        "version": "1.0",
        "aggregator_sha256": digest(SELF.read_bytes()),
        "recipe_sha256": digest(recipe_bytes),
        "candidate_sha": args.commit,
        "candidate_tree": args.tree,
        "workflow_run_id": str(args.run_id),
        "workflow_run_attempt": sorted(attempts)[0],
        "workflow_commit_sha": args.workflow_commit,
        "stage_isolation": "one-disposable-runner-per-stage",
        # Stated, never judged: the external genesis policy decides whether a run whose pins were
        # only printed for review may authorize anything.
        "driver_pins_asserted": bool(stage_pins) and all(stage_pins),
        "evidence_source": "github-actions-job-record",
        "stages": stages,
        "failed_stages": failed,
        "verdict": "fail" if failed else "pass",
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failed:
        for entry in failed:
            for reason in entry["reasons"]:
                print(f"COLLECTION_STAGE_FAILED {reason}", file=sys.stderr)
        print(
            f"COLLECTION_FAILED run={args.run_id} candidate={args.commit} "
            f"failed={len(failed)}/{len(stages)}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(
        f"COLLECTION_OK run={args.run_id} candidate={args.commit} tree={args.tree} "
        f"stages={len(stages)} collector={payload['aggregator_sha256'][:12]}"
    )


if __name__ == "__main__":
    main()
