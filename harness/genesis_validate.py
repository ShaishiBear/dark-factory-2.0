#!/usr/bin/env python3
"""External validation driver for the one-time genesis ceremony: exactly one stage per run.

The genesis verifier once read its measurements out of the aggregated validation log with an
unconstrained regex search. Candidate-controlled programs write to that same log, so a single line
printed early decided what the authority believed: a run with six escaped mutations could be
recorded as "1/1" and satisfy the caught-equals-total invariant. Searching a shared stream is not
measurement.

Sequence was the other half. Running every stage in one process, one checkout, one machine proves
only that the *first* stage saw the authorized tree. A candidate-controlled stage can rewrite a
later stage's validator, plant untracked source beside it, or persist influence well outside its
checkout -- through user-site and sitecustomize, HOME and tool config, temporary directories,
global Node/Bun state, the Git common directory and its hooks, surviving background processes, or
a shared dependency cache. A fresh worktree is not a fresh execution environment, and no amount of
custom sandboxing inside one process makes it one.

So this driver does not sequence anything. It runs **one** named stage and exits. Isolation is
delegated to a boundary that is genuinely stronger and far simpler than anything implementable
here: one fresh GitHub-hosted runner per stage, each checking out the exact authorized commit,
independently asserting the pinned driver and recipe digests, preparing dependencies from the
committed lockfiles in that job alone, and terminating with the runner so no process or filesystem
state can reach another stage. Because this program cannot run two stages, that property cannot be
quietly lost by changing how it is called.

This driver deliberately sets no cache environment of its own. "No cross-stage cache" is a
property of the pinned validation workflow -- which restores no cache for any stage job -- and the
external genesis policy pins that workflow's content hash. It is not a property this file can
assert about itself, and an earlier version of this docstring claimed the shared caches were
"hash-checked against the committed lockfiles" when no such verification existed anywhere. It has
been removed rather than softened.

Within its one stage, the driver:

* treats the **exit status** as the primary verdict, which no amount of printed text can forge;
* parses only that stage's own captured output, never a shared aggregate;
* requires each measured marker to appear unambiguously -- absent, or present with disagreeing
  values, is a refusal rather than a first match;
* records the exact argv, so the result says what was actually run;
* binds its own SHA-256, the recipe's, and the exact candidate commit and tree.

A separate aggregation authority combines the per-stage artifacts and refuses unless every stage
the frozen policy requires is present exactly once, from the same commit, driver and recipe.

Where its authority comes from, precisely. This file lives in the candidate so that it gets
adversarial mutation coverage like any other trust-root component, and the external genesis policy
pins its SHA-256. The ``driver_sha256`` it writes into its own result is *corroborating only*: a
tampered driver would report the expected digest. The binding check belongs to the validation
workflow, which lives outside the candidate and computes this file's digest itself before running
it.
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


def git_out(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=180
    )
    if proc.returncode:
        fail(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout.strip()


def measure(name: str, output: str, marker: str) -> int:
    """Read one count from one stage's own output, or refuse.

    Every occurrence must agree. A marker appearing twice with different values is ambiguous, and
    ambiguity is a refusal -- taking the first match is exactly the spoof this replaces.
    """
    found = re.findall(marker, output)
    if not found:
        fail(f"stage {name!r} did not report {marker!r}")
    unique = set(found)
    if len(unique) != 1:
        fail(f"stage {name!r} reported {marker!r} ambiguously: {sorted(unique)}")
    return int(found[0])


def assert_environment(repo: Path, commit: str) -> str:
    """This stage runs against the exact authorized commit, in a clean checkout, or not at all."""
    if not (repo / ".git").exists():
        fail(f"not a git repository: {repo}")
    head = git_out(repo, "rev-parse", "HEAD")
    if head != commit:
        fail(f"repository is at {head}, not the commit being validated ({commit})")
    dirty = git_out(repo, "status", "--porcelain")
    if dirty:
        fail(f"stage environment is not a clean checkout: {dirty[:200]}")
    return git_out(repo, "rev-parse", f"{commit}^{{tree}}")


def execute(argv: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def run_stage(repo: Path, stage: dict, recipe: dict, log_dir: Path) -> dict:
    name = str(stage.get("name") or "")
    argv = stage.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        fail(f"recipe stage {name or '<unnamed>'} has no valid argv")
    cwd = repo / str(stage.get("cwd") or ".")
    if not cwd.is_dir():
        fail(f"stage {name!r} working directory does not exist: {cwd}")

    if stage.get("needs_dependencies"):
        for step in recipe.get("prepare") or []:
            prep = execute(
                list(step["argv"]), repo / str(step.get("cwd") or "."),
                int(step.get("timeout_seconds") or 1800),
            )
            if prep.returncode != 0:
                detail = ((prep.stdout or "") + (prep.stderr or ""))[-800:]
                fail(f"stage {name!r} could not prepare its environment: {detail}")

    proc = execute(list(argv), cwd, int(stage.get("timeout_seconds") or 3600))
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


def load_recipe(path: Path) -> tuple[dict, bytes]:
    if not path.is_file():
        fail(f"validation recipe does not exist: {path}")
    raw = path.read_bytes()
    try:
        recipe = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"validation recipe is not valid JSON: {exc}")
    stages = recipe.get("stages") if isinstance(recipe, dict) else None
    if not isinstance(stages, list) or not stages:
        fail("validation recipe defines no stages")
    names = [str(s.get("name") or "") for s in stages]
    if len(set(names)) != len(names) or not all(names):
        fail("validation recipe stage names must be unique and non-empty")
    return recipe, raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--stage", required=True, help="the single stage this invocation runs")
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--list-stages", action="store_true")
    args = parser.parse_args()

    recipe, recipe_bytes = load_recipe(Path(args.recipe).resolve())
    if args.list_stages:
        print(json.dumps([s["name"] for s in recipe["stages"]]))
        return

    stage = next((s for s in recipe["stages"] if str(s.get("name")) == args.stage), None)
    if stage is None:
        fail(f"validation recipe has no stage named {args.stage!r}")

    repo = Path(args.repo).resolve()
    tree = assert_environment(repo, args.commit)
    log_dir = Path(args.log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    result = run_stage(repo, stage, recipe, log_dir)
    payload = {
        "version": "2.0",
        "driver_sha256": digest(SELF.read_bytes()),
        "recipe_sha256": digest(recipe_bytes),
        "candidate_sha": args.commit,
        "candidate_tree": tree,
        "stage": result,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"STAGE_RESULT {result['name']} exit={result['exit']} tree={tree[:12]} "
        f"measurements={result['measurements']}",
        flush=True,
    )
    if result["exit"] != 0:
        fail(f"validation stage {result['name']!r} failed")
    print(
        f"STAGE_OK {result['name']} candidate={args.commit} tree={tree} "
        f"driver={payload['driver_sha256'][:12]} recipe={payload['recipe_sha256'][:12]}"
    )


if __name__ == "__main__":
    main()
