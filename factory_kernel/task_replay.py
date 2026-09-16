"""Bounded offline execution and evidence recording for reviewed replay cases.

Execution receives only adapter inputs. Labels are opened by the scorer after
all attempts finish. This command has no production authority or live worker.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time

from factory_kernel.benchmark import canonical, digest, load_suite, OUTCOMES, score
from factory_kernel.task_replay_adapters import ADAPTERS

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{2,99}\Z")
MAX_BYTES = 256_000
SOURCE_DIRS = ("factory_kernel", "scripts", "harness", ".factory", "tests/factory",
               "MISSION.md", "FACTORY_RULES.md", "CLAUDE.md", "PROGRAMME.md", "README.md")
BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from factory_kernel.task_replay_adapters import main; main()"
)


def strict_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON number")

    if len(raw) > MAX_BYTES:
        raise ValueError("replay JSON exceeds size limit")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError("replay JSON must be an object")
    return value


@dataclass(frozen=True)
class ReplayCase:
    id: str
    group: str
    split: str
    adapter: str
    inputs_json: bytes

    def request(self) -> dict:
        # Deliberately excludes id/group/split, as well as expected outcomes.
        return {"adapter": self.adapter, "inputs": strict_json(self.inputs_json)}


def load_cases(path: Path) -> tuple[list[ReplayCase], str]:
    raw = path.read_bytes()
    value = strict_json(raw)
    if set(value) != {"version", "cases"} or value["version"] != "1.0":
        raise ValueError("replay inputs require version 1.0 and cases only")
    if not isinstance(value["cases"], list) or not 1 <= len(value["cases"]) <= 100:
        raise ValueError("replay requires 1 to 100 cases")
    cases, ids, groups = [], set(), {}
    for case in value["cases"]:
        if not isinstance(case, dict) or set(case) != {"id", "group", "split", "adapter", "inputs"}:
            raise ValueError("case fields must be id/group/split/adapter/inputs; no labels or commands")
        for key in ("id", "group"):
            if not isinstance(case[key], str) or not NAME.fullmatch(case[key]):
                raise ValueError("case id and incident group must be safe names")
        if case["id"] in ids:
            raise ValueError("duplicate case id")
        ids.add(case["id"])
        split = case["split"]
        if not isinstance(split, str) or split not in {"development", "screening", "confirmation"}:
            raise ValueError("unknown evaluation split")
        if case["group"] in groups and groups[case["group"]] != split:
            raise ValueError("same issue/incident group leaks across evaluation splits")
        groups[case["group"]] = split
        if not isinstance(case["adapter"], str) or case["adapter"] not in ADAPTERS or not isinstance(case["inputs"], dict):
            raise ValueError("unknown adapter or malformed inputs")
        cases.append(ReplayCase(case["id"], case["group"], split, case["adapter"], canonical(case["inputs"])))
    return cases, hashlib.sha256(raw).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", timeout=30).stdout.strip()


def source_identity(root: Path = ROOT) -> dict:
    """Bind the actual source bytes, including local replay implementation changes."""
    head = git(root, "rev-parse", "HEAD")
    files = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *SOURCE_DIRS)
    hashes = {}
    for rel in sorted(set(files.split("\0")) - {""}):
        path = root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("source identity refuses symlinks/escaping paths")
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    if not hashes:
        raise ValueError("source inventory is empty")
    return {"factory_sha": head, "source_sha256": digest(hashes), "source_files": hashes,
            "python": platform.python_version(), "platform": platform.platform()}


def child_environment(home: Path) -> dict:
    # No HOME credentials, PATH tools, Python startup, provider or GitHub variables.
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "TMP": str(home),
                "TEMP": str(home), "TMPDIR": str(home), "LANG": "C.UTF-8"})
    return env


def execute(case: ReplayCase, *, timeout: float, root: Path = ROOT) -> dict:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="factory-replay-") as tmp:
        home = Path(tmp)
        stdout_path, stderr_path = home / "stdout", home / "stderr"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", "-S", "-c", BOOTSTRAP, str(root)],
                    input=canonical(case.request()), cwd=home, env=child_environment(home),
                    stdout=stdout, stderr=stderr, timeout=timeout, check=False,
                )
            except subprocess.TimeoutExpired:
                return {"status": "timeout", "elapsed_seconds": time.monotonic() - started}
        elapsed = time.monotonic() - started
        if proc.returncode:
            # Exception text may contain injected payloads. Retain its hash, not an unbounded log.
            return {"status": "error", "returncode": proc.returncode, "elapsed_seconds": elapsed,
                    "stderr_sha256": hashlib.sha256(stderr_path.read_bytes()).hexdigest()}
        try:
            if stdout_path.stat().st_size > MAX_BYTES:
                raise ValueError("adapter output exceeds size limit")
            result = strict_json(stdout_path.read_bytes())
            if set(result) != {"outcome", "level", "evidence"} or result["outcome"] not in OUTCOMES:
                raise ValueError("malformed observed outcome")
            if result["level"] not in {"production-rule", "rule-with-recorded-process", "simulated-orchestration"}:
                raise ValueError("unknown evidence level")
            if not isinstance(result["evidence"], dict) or not result["evidence"]:
                raise ValueError("missing observation evidence")
        except (ValueError, TypeError):
            return {"status": "invalid-output", "elapsed_seconds": elapsed}
        return {"status": "observed", "elapsed_seconds": elapsed, **result}


def evaluate(cases: list[ReplayCase], attempts: list[dict], suites: list, factory_sha: str) -> dict:
    """Missing execution never becomes a correct refusal, even if its label is needs-human."""
    if not suites:
        return {"status": "unscored"}
    expected_ids = [case.case_id for suite in suites for case in suite.cases]
    if len(expected_ids) != len(set(expected_ids)) or set(expected_ids) != {case.id for case in cases}:
        raise ValueError("label coverage must equal executed input cases exactly")
    reports = []
    for repeat in sorted({attempt["repeat"] for attempt in attempts}):
        rows = [attempt for attempt in attempts if attempt["repeat"] == repeat]
        observed = {row["id"]: row["outcome"] for row in rows if row["status"] == "observed"}
        # Use the existing scorer only for a complete observation set. Do not invent outcomes.
        if len(observed) != len(cases):
            reports.append({"repeat": repeat, "status": "incomplete",
                            "missing": sorted({case.id for case in cases} - observed.keys())})
        else:
            reports.append({"repeat": repeat, "status": "scored",
                            "score": score(suites, results=observed, factory_sha=factory_sha)})
    complete = len(attempts) == len(cases) * len(reports) and all(row["status"] == "observed" for row in attempts)
    passed = complete and bool(reports) and all(row.get("score", {}).get("verdict") == "pass" for row in reports)
    return {"status": "pass" if passed else "fail", "repeats": reports,
            "assigned": len(attempts), "observed": sum(row["status"] == "observed" for row in attempts)}


def run(cases_path: Path, output: Path, *, labels: list[Path], repeats: int = 1,
        per_case_seconds: float = 30, total_seconds: float = 300) -> dict:
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("repeats must be between 1 and 10")
    for value in (per_case_seconds, total_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
            raise ValueError("time budgets must be finite, positive and at most 3600 seconds")
    cases, input_hash = load_cases(cases_path)
    frozen_inputs = cases_path.read_bytes()
    if hashlib.sha256(frozen_inputs).hexdigest() != input_hash:
        raise ValueError("inputs changed while loading")
    identity = source_identity()
    # Freeze label bytes before execution, without parsing expected answers for adapters.
    label_bytes = [path.read_bytes() for path in labels]
    if any(len(raw) > MAX_BYTES for raw in label_bytes):
        raise ValueError("label file exceeds size limit")
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a new directory; previous experiments are immutable")
    output.mkdir(parents=True)
    manifest = {"version": "1.0", "mode": "offline-regression", "identity": identity,
                "inputs_sha256": input_hash, "labels_sha256": [hashlib.sha256(raw).hexdigest() for raw in label_bytes],
                "repeats": repeats, "per_case_seconds": per_case_seconds, "total_seconds": total_seconds,
                "live_model_calls": 0, "live_external_effects": 0, "qualification_authority": False}
    (output / "manifest.json").write_bytes(canonical(manifest))
    (output / "inputs.json").write_bytes(frozen_inputs)
    start = time.monotonic()
    attempts, interrupted = [], False
    with (output / "events.jsonl").open("xb") as journal:
        for repeat in range(1, repeats + 1):
            for case in cases:
                key = {"id": case.id, "repeat": repeat, "group": case.group, "split": case.split,
                       "request_sha256": digest(case.request())}
                journal.write(canonical({**key, "status": "started"}))
                journal.flush()
                os.fsync(journal.fileno())
                remaining = total_seconds - (time.monotonic() - start)
                if interrupted or remaining <= 0:
                    result = {"status": "not-run", "reason": "interrupted" if interrupted else "total-budget"}
                else:
                    try:
                        result = execute(case, timeout=min(per_case_seconds, remaining))
                    except KeyboardInterrupt:
                        interrupted, result = True, {"status": "interrupted"}
                    except (OSError, ValueError) as exc:
                        result = {"status": "error", "exception": type(exc).__name__}
                row = {**key, **result}
                attempts.append(row)
                (output / f"{case.id}.{repeat}.json").write_bytes(canonical(row))
                journal.write(canonical(row))
                journal.flush()
                os.fsync(journal.fileno())
    suites, label_error = [], None
    for i, raw in enumerate(label_bytes):
        path = output / f"labels.{i}.json"
        path.write_bytes(raw)
        try:
            strict_json(raw)
            suites.append(load_suite(path))
        except (ValueError, TypeError) as exc:
            label_error = str(exc)
    # A source edit during a run invalidates the comparison, even when every case passes.
    try:
        unchanged = source_identity() == identity
    except (OSError, ValueError, subprocess.SubprocessError):
        unchanged = False
    try:
        if label_error:
            raise ValueError(label_error)
        evaluation = evaluate(cases, attempts, suites, identity["factory_sha"])
    except ValueError as exc:
        evaluation = {"status": "invalid-labels", "reason": str(exc)}
    report = {"version": "1.0", "manifest_sha256": digest(manifest), "source_unchanged": unchanged,
              "mode": "offline-regression", "qualification_authority": False,
              "live_verified_completions": None, "model_quality_measured": False,
              "attempts": attempts, "evaluation": evaluation}
    (output / "report.json").write_bytes(canonical(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--labels", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--per-case-seconds", type=float, default=30)
    parser.add_argument("--total-seconds", type=float, default=300)
    args = parser.parse_args()
    try:
        report = run(args.inputs, args.output, labels=args.labels, repeats=args.repeats,
                     per_case_seconds=args.per_case_seconds, total_seconds=args.total_seconds)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"REPLAY_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    ok = report["source_unchanged"] and report["evaluation"]["status"] in {"pass", "unscored"}
    ok = ok and all(row["status"] == "observed" for row in report["attempts"])
    print(f"REPLAY_{'OK' if ok else 'FAIL'} attempts={len(report['attempts'])} mode=offline-regression output={args.output}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
