#!/usr/bin/env python3
"""What a mutation run actually observed, separated from what it concluded.

The factory mutation family used to answer one question per defect -- "did the focused suite
go red?" -- and it answered it with a returncode. Three different things reach that returncode
and only one of them is evidence about the defect:

  * a detector asserted a property and the assertion failed (the defect was CAUGHT);
  * the detector could not be collected or its process died (INFRA_ERROR: the tree, not the
    defect, is what changed);
  * the detector ran out of time (TIMEOUT: nothing was observed at all).

On 2026-09-18 the third of those aborted the whole family. `subprocess.TimeoutExpired` from
one file propagated out of `ThreadPoolExecutor.map`, the run printed a traceback instead of
903 verdicts, and the evidence for every other defect in the catalogue was discarded
(https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/35324653207, run at 73d557c).
A timeout is not a kill and not an escape; it is an absence of observation, and the run that
contains one is incomplete rather than green or red.

This module owns those distinctions so both the runner and its tests read the same rules:

  * `MutationResult` is the per-mutant record, one immutable JSON file each.
  * `classify_detector` maps a finished (or unfinished) detector process to a status.
  * `kill_tree` reaps descendants, because a detector that spawns children and is killed at
    the timeout leaves them holding the copy open.
  * `shard_members` and `aggregate_manifests` make a deterministic split verifiable: every
    expected mutant appears exactly once across disjoint shards sharing one baseline, and
    anything missing is `not_run` rather than absent.

Nothing here decides a floor or a budget. It decides what a run is allowed to claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

CAUGHT = "caught"
SURVIVED = "survived"
INFRA_ERROR = "infra_error"
TIMEOUT = "timeout"
NOT_RUN = "not_run"
STATES = (CAUGHT, SURVIVED, INFRA_ERROR, TIMEOUT, NOT_RUN)

# Detector statuses. `failed` is the only one that can prove a kill, and only from a detector
# whose baseline was `passed` in this same environment.
PASSED = "passed"
FAILED = "failed"

SCHEMA = "dark-factory/mutation-result"
SCHEMA_VERSION = "1.0"
MANIFEST_SCHEMA = "dark-factory/mutation-manifest"


@dataclass(frozen=True)
class MutationResult:
    """One mutant's evidence. `state` is what was observed, not what we hoped."""

    mutant_id: str
    injected_digest: str
    state: str
    detector: str
    baseline_ref: str
    elapsed_ns: int
    diagnostic_ref: str

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown mutation state: {self.state}")
        if self.state == CAUGHT and not self.detector:
            raise ValueError("a caught mutant must name the detector that caught it")

    def as_record(self) -> dict:
        return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_record(cls, record: dict) -> "MutationResult":
        fields = {key: record[key] for key in cls.__dataclass_fields__ if key in record}
        return cls(**fields)


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_record(record: dict) -> str:
    return digest_text(json.dumps(record, sort_keys=True, separators=(",", ":")))


def classify_detector(returncode: int | None, output: str, *, timed_out: bool = False) -> str:
    """What a finished detector process observed.

    A detector is a `unittest` program run as `python tests/factory/test_x.py`. unittest
    prints `Ran N tests` once it has imported the module and started executing; if that line
    is absent the module never ran, which is a collection/import failure -- an observation
    about the tree, not about the defect. A process that died on a signal (negative
    returncode) equally observed nothing. Everything else that exits non-zero ran tests and
    reported them red, which is a behavioural observation.
    """
    if timed_out:
        return TIMEOUT
    if returncode is None:
        return INFRA_ERROR
    if returncode == 0:
        return PASSED
    if returncode < 0:
        return INFRA_ERROR
    if "Ran " not in output:
        return INFRA_ERROR
    return FAILED


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the detector and everything it started.

    `Popen.kill` reaches the direct child only. A detector that launched its own subprocess
    (several of them do) leaves that grandchild holding the copy's files open, and on Windows
    the copy then cannot be removed at all.
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30, check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def run_detector(argv: list[str], *, cwd: Path, env: dict, seconds: float) -> tuple[str, int | None, str, int]:
    """Run one detector under a hard bound. Returns (status, returncode, output, elapsed_ns).

    The bound is enforced here rather than by `subprocess.run(timeout=...)` so a timeout is a
    status this run records and continues from, never an exception that unwinds the pool.
    """
    started = time.monotonic_ns()
    creation = {}
    if os.name == "nt":
        creation["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creation["start_new_session"] = True
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", **creation)
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=max(1.0, seconds))
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        try:
            output, _ = proc.communicate(timeout=30)
        except (subprocess.TimeoutExpired, ValueError):
            output = ""
    elapsed = time.monotonic_ns() - started
    output = output or ""
    return classify_detector(proc.returncode, output, timed_out=timed_out), proc.returncode, output, elapsed


def shard_members(ids: list[str], shards: int, index: int) -> list[str]:
    """The mutants this shard owns: sorted index modulo K, so the split is deterministic.

    Sorting first means the split does not move when a manifest gains a defect in the middle,
    and modulo means each shard gets a mix of cheap and expensive mutants rather than one
    shard inheriting an entire slow manifest.
    """
    if shards < 1 or not 0 <= index < shards:
        raise ValueError(f"shard {index} of {shards} is not a shard")
    ordered = sorted(ids)
    return [mutant for position, mutant in enumerate(ordered) if position % shards == index]


def aggregate_manifests(manifests: list[dict]) -> dict:
    """One verdict from disjoint shards, or a refusal naming what is missing.

    An aggregate is only meaningful when the shards agree on what they were measuring and
    between them measured everything exactly once. Anything else is `incomplete`: a missing
    shard cannot be assumed green, and a mutant measured twice means the split was not a
    split.
    """
    if not manifests:
        return {"status": "incomplete", "reasons": ["no shard manifests"], "results": []}
    reasons: list[str] = []
    expected = sorted({mutant for manifest in manifests for mutant in manifest.get("expected_ids", [])})
    baselines = {manifest.get("baseline_ref") for manifest in manifests}
    if len(baselines) != 1 or not all(baselines):
        reasons.append(f"shards used {len(baselines)} different baselines")
    declared = [(manifest.get("shard_index"), manifest.get("shard_count")) for manifest in manifests]
    counts = {count for _, count in declared}
    if len(counts) != 1:
        reasons.append("shards disagree about how many shards there are")
    else:
        count = counts.pop()
        indexes = sorted(index for index, _ in declared)
        if indexes != list(range(count or 0)):
            reasons.append(f"shard indexes {indexes} are not 0..{(count or 0) - 1}")

    seen: dict[str, list[dict]] = {}
    for manifest in manifests:
        for record in manifest.get("results", []):
            if record.get("state") == NOT_RUN:
                continue
            seen.setdefault(record["mutant_id"], []).append(record)
    duplicated = sorted(mutant for mutant, records in seen.items() if len(records) > 1)
    if duplicated:
        reasons.append(f"measured more than once: {','.join(duplicated[:10])}")
    missing = [mutant for mutant in expected if mutant not in seen]
    results = []
    for mutant in expected:
        records = seen.get(mutant)
        if records:
            results.append(records[0])
        else:
            results.append(MutationResult(mutant, "", NOT_RUN, "", "", 0, "").as_record())
    if missing:
        reasons.append(f"never run: {','.join(missing[:10])}")
    incomplete = [record for record in results
                  if record["state"] in (INFRA_ERROR, TIMEOUT, NOT_RUN)]
    survived = [record for record in results if record["state"] == SURVIVED]
    if incomplete:
        status = "incomplete"
    elif survived:
        status = "failed"
    else:
        status = "green" if not reasons else "incomplete"
    return {"schema": MANIFEST_SCHEMA, "schema_version": SCHEMA_VERSION, "status": status,
            "reasons": reasons, "expected": len(expected), "results": results,
            "incomplete_ids": [record["mutant_id"] for record in incomplete],
            "survived_ids": [record["mutant_id"] for record in survived]}


def write_atomic(path: Path, record: dict) -> None:
    """Publish a result file only once its bytes are on disk.

    A result half-written when the family is cancelled must not be readable as a verdict, so
    the bytes are flushed and fsynced under a temporary name and then moved into place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    payload = json.dumps(record, sort_keys=True, indent=2) + "\n"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_results(directory: Path) -> list[dict]:
    records = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return records


def main(argv: list[str] | None = None) -> int:
    """`--aggregate DIR [DIR ...]` prints one verdict over shard manifests."""
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] != "--aggregate":
        print("usage: outcomes.py --aggregate DIR [DIR ...]", flush=True)
        return 2
    manifests = []
    for raw in args[1:]:
        path = Path(raw)
        path = path / "manifest.json" if path.is_dir() else path
        manifests.append(json.loads(path.read_text(encoding="utf-8")))
    aggregate = aggregate_manifests(manifests)
    print(json.dumps(aggregate, sort_keys=True, indent=2), flush=True)
    print(f"FACTORY_MUTATIONS_AGGREGATE={aggregate['status']} expected={aggregate['expected']}",
          flush=True)
    return 0 if aggregate["status"] == "green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
