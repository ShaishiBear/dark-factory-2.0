#!/usr/bin/env python3
"""External validation driver for the one-time genesis ceremony.

The genesis verifier used to read its measurements out of the aggregated validation log with an
unconstrained ``re.search``. Candidate-controlled programs write to that same log, so a single
line printed early -- by a test, a runner banner, a mutation's own description -- decided what the
authority believed. A run with six escaped mutations could be recorded as ``1/1`` and satisfy the
``caught == total`` invariant. Searching a shared stream is not measurement.

This driver moves measurement into a pinned authority outside the candidate. It executes the
stages named by a recipe, and for each one:

* treats the **exit status** as the primary verdict, which no amount of printed text can forge;
* parses only that stage's *own* captured output, never a shared aggregate;
* requires each measured marker to appear unambiguously -- absent, or present with disagreeing
  values, is a refusal rather than a first match;
* records the exact argv, so the result says what was actually run.

It then emits a structured result binding its own SHA-256, the recipe's, and the exact candidate
commit. The genesis verifier consumes that structure and never parses candidate output again.

Where its authority comes from, precisely. This file lives in the candidate so that it gets
adversarial mutation coverage like any other trust-root component, and the external genesis policy
pins its SHA-256. But note what that pin can and cannot do: the ``driver_sha256`` this program
writes into its own result is *corroborating only*, because a tampered driver would happily report
the expected digest. The binding check therefore belongs to the validation workflow, which lives
outside the candidate, computes this file's digest itself and refuses to run it unless the digest
matches the pinned value. The genesis verifier re-checks the same field afterwards as defence in
depth, not as the primary control.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

SELF = Path(__file__).resolve()


def fail(message: str) -> None:
    print(f"VALIDATION_REFUSED {message}", file=sys.stderr)
    raise SystemExit(1)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def measure(name: str, output: str, marker: str) -> int:
    """Read one count from one stage's own output, or refuse.

    ``marker`` is a regex with a single integer group. Every occurrence must agree; a marker that
    appears twice with different values is ambiguous and ambiguity is a refusal.
    """
    found = re.findall(marker, output)
    if not found:
        fail(f"stage {name!r} did not report {marker!r}")
    unique = set(found)
    if len(unique) != 1:
        fail(f"stage {name!r} reported {marker!r} ambiguously: {sorted(unique)}")
    return int(found[0])


def run_stage(repo: Path, stage: dict, log_dir: Path) -> dict:
    name = str(stage.get("name") or "")
    argv = stage.get("argv")
    if not name or not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        fail(f"recipe stage {name or '<unnamed>'} has no valid argv")
    cwd = repo / str(stage.get("cwd") or ".")
    if not cwd.is_dir():
        fail(f"stage {name!r} working directory does not exist: {cwd}")

    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=int(stage.get("timeout_seconds") or 3600),
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    (log_dir / f"{name}.log").write_text(output, encoding="utf-8")

    measurements: dict[str, int] = {}
    measures = stage.get("measures") or {}
    if not isinstance(measures, dict):
        fail(f"recipe stage {name!r} has an invalid measures block")
    if proc.returncode == 0:
        for key, marker in sorted(measures.items()):
            measurements[key] = measure(name, output, str(marker))
    return {
        "name": name,
        "argv": list(argv),
        "cwd": str(stage.get("cwd") or "."),
        "exit": proc.returncode,
        "measurements": measurements,
        "output_sha256": digest(output.encode()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        fail(f"not a git repository: {repo}")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60
    ).stdout.strip()
    if head != args.commit:
        fail(f"repository is at {head}, not the commit being validated ({args.commit})")

    recipe_path = Path(args.recipe).resolve()
    if not recipe_path.is_file():
        fail(f"validation recipe does not exist: {recipe_path}")
    recipe_bytes = recipe_path.read_bytes()
    try:
        recipe = json.loads(recipe_bytes)
    except json.JSONDecodeError as exc:
        fail(f"validation recipe is not valid JSON: {exc}")
    stages = recipe.get("stages") if isinstance(recipe, dict) else None
    if not isinstance(stages, list) or not stages:
        fail("validation recipe defines no stages")
    names = [str(s.get("name") or "") for s in stages]
    if len(set(names)) != len(names) or not all(names):
        fail("validation recipe stage names must be unique and non-empty")

    log_dir = Path(args.log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for stage in stages:
        result = run_stage(repo, stage, log_dir)
        print(f"STAGE {result['name']} exit={result['exit']} {result['measurements']}", flush=True)
        results.append(result)

    failed = [r["name"] for r in results if r["exit"] != 0]
    payload = {
        "version": "1.0",
        "driver_sha256": digest(SELF.read_bytes()),
        "recipe_sha256": digest(recipe_bytes),
        "candidate_sha": args.commit,
        "stages": results,
        "failed_stages": failed,
        "verdict": "pass" if not failed else "fail",
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failed:
        fail("validation stages failed: " + ", ".join(failed))
    print(
        f"VALIDATION_RESULT_OK candidate={args.commit} stages={len(results)} "
        f"driver={payload['driver_sha256'][:12]} recipe={payload['recipe_sha256'][:12]}"
    )


if __name__ == "__main__":
    main()
