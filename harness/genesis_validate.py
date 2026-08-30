#!/usr/bin/env python3
"""External validation driver for the one-time genesis ceremony.

The genesis verifier used to read its measurements out of the aggregated validation log with an
unconstrained ``re.search``. Candidate-controlled programs write to that same log, so a single
line printed early -- by a test, a runner banner, a mutation's own description -- decided what the
authority believed. A run with six escaped mutations could be recorded as ``1/1`` and satisfy the
``caught == total`` invariant. Searching a shared stream is not measurement.

Sequence is the other half of the problem. Running every stage in one mutable checkout proves
only that the *first* stage saw the authorized tree: a candidate-controlled stage can rewrite a
later stage's validator, drop untracked executable source next to it, or poison the runtime
environment, exit zero, and the driver would then execute the modified state and call it evidence.

So each stage runs in a worktree created fresh from the object store at the exact authorized
commit, whose HEAD, tree object id and cleanliness are re-verified immediately before the stage
runs and whose environment is destroyed immediately after. Git objects are content-addressed, so
re-resolving the authorized commit to the authorized tree before every stage also detects
tampering with the shared object store -- a tree cannot change content and keep its identity.
Only package-manager download caches are shared, and their contents are hash-checked against the
committed lockfiles; source and runtime state are never carried between stages.

This driver moves measurement into a pinned authority outside the candidate. It executes the
stages named by a recipe, and for each one:

* derives the code it runs freshly from the authorized commit, so no earlier stage can reach it;
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
import os
import re
import shutil
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


def worktree_add(repo: Path, commit: str, path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(path), commit],
        capture_output=True, text=True, timeout=600, check=False,
    )
    if not (path / ".git").exists():
        fail(f"could not create an isolated worktree at {path}")


def worktree_remove(repo: Path, path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(path)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "prune"], capture_output=True, timeout=120, check=False
    )


def git_out(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=180
    )
    if proc.returncode:
        fail(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout.strip()


def execute(argv: list[str], cwd: Path, timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env,
    )


def assert_object_store(repo: Path, commit: str, tree: str, name: str) -> None:
    """Git objects are content-addressed, so a tree that still resolves is a tree unchanged.

    Re-checking before every stage is what detects an earlier stage having tampered with the
    shared object store the isolated worktrees are derived from.
    """
    if git_out(repo, "rev-parse", f"{commit}^{{tree}}") != tree:
        fail(f"the authorized commit no longer resolves to the authorized tree before stage {name!r}")


def assert_stage_environment(path: Path, commit: str, tree: str, name: str) -> tuple[str, str]:
    """A stage runs only in a pristine checkout of the exact authorized commit, or not at all."""
    head = git_out(path, "rev-parse", "HEAD")
    stage_tree = git_out(path, "rev-parse", "HEAD^{tree}")
    if head != commit or stage_tree != tree:
        fail(f"stage {name!r} environment is not the authorized commit ({head}/{stage_tree})")
    dirty = git_out(path, "status", "--porcelain")
    if dirty:
        fail(f"stage {name!r} environment was not clean before execution: {dirty[:200]}")
    return head, stage_tree


def run_stage(
    repo: Path, commit: str, tree: str, stage: dict, recipe: dict, log_dir: Path,
    work_dir: Path, env: dict[str, str],
) -> dict:
    name = str(stage.get("name") or "")
    argv = stage.get("argv")
    if not name or not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        fail(f"recipe stage {name or '<unnamed>'} has no valid argv")

    assert_object_store(repo, commit, tree, name)

    path = work_dir / f"stage-{name}"
    worktree_add(repo, commit, path)
    try:
        head, stage_tree = assert_stage_environment(path, commit, tree, name)

        cwd = path / str(stage.get("cwd") or ".")
        if not cwd.is_dir():
            fail(f"stage {name!r} working directory does not exist: {cwd}")

        if stage.get("needs_dependencies"):
            for step in recipe.get("prepare") or []:
                prep = execute(
                    list(step["argv"]), path / str(step.get("cwd") or "."),
                    int(step.get("timeout_seconds") or 1800), env,
                )
                if prep.returncode != 0:
                    detail = ((prep.stdout or "") + (prep.stderr or ""))[-800:]
                    fail(f"stage {name!r} could not prepare its isolated environment: {detail}")

        proc = execute(list(argv), cwd, int(stage.get("timeout_seconds") or 3600), env)
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
            "executed_candidate_sha": head,
            "executed_tree_sha": stage_tree,
            "isolated": True,
        }
    finally:
        worktree_remove(repo, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--work-dir")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        fail(f"not a git repository: {repo}")
    head = git_out(repo, "rev-parse", "HEAD")
    if head != args.commit:
        fail(f"repository is at {head}, not the commit being validated ({args.commit})")
    tree = git_out(repo, "rev-parse", f"{args.commit}^{{tree}}")

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
    work_dir = Path(args.work_dir).resolve() if args.work_dir else repo.parent / "genesis-stages"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Only package-manager download caches cross the stage boundary. Their contents are verified
    # against the committed lockfiles, and no source or runtime state is shared.
    env = dict(os.environ)
    env.setdefault("UV_CACHE_DIR", str(work_dir / "cache-uv"))
    env.setdefault("BUN_INSTALL_CACHE_DIR", str(work_dir / "cache-bun"))

    results = []
    try:
        for stage in stages:
            result = run_stage(repo, args.commit, tree, stage, recipe, log_dir, work_dir, env)
            print(
                f"STAGE {result['name']} exit={result['exit']} "
                f"tree={result['executed_tree_sha'][:12]} {result['measurements']}",
                flush=True,
            )
            results.append(result)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    failed = [r["name"] for r in results if r["exit"] != 0]
    payload = {
        "version": "1.0",
        "driver_sha256": digest(SELF.read_bytes()),
        "recipe_sha256": digest(recipe_bytes),
        "candidate_sha": args.commit,
        "candidate_tree": tree,
        "stage_isolation": "per-stage-worktree",
        "stages": results,
        "failed_stages": failed,
        "verdict": "pass" if not failed else "fail",
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failed:
        fail("validation stages failed: " + ", ".join(failed))
    print(
        f"VALIDATION_RESULT_OK candidate={args.commit} tree={tree} stages={len(results)} "
        f"driver={payload['driver_sha256'][:12]} recipe={payload['recipe_sha256'][:12]}"
    )


if __name__ == "__main__":
    main()
