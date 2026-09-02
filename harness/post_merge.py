#!/usr/bin/env python3
"""Post-merge authority for Dark Factory.

A successful pre-merge Evidence Bundle and byte-identical squash merge are not the end of the
factory lifecycle. This authority re-runs the canonical full harness on the exact merge commit
that is currently the tip of origin/main and emits a machine-readable post-merge proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(os.environ.get("FACTORY_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

# Ensure repo-owned helpers are importable even when executed as `python harness/post_merge.py`.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.credential_env import scoped_environment  # noqa: E402
from factory_kernel.worktree import create_detached, remove  # noqa: E402
from harness.observe import parse_transcript  # noqa: E402


def die(message: str) -> None:
    print(f"POST_MERGE_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON evidence: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON evidence must be an object")
    return value


def require_oid(value: object, name: str) -> str:
    text = str(value or "")
    if not OID.fullmatch(text):
        raise ValueError(f"{name} is not a valid git object id")
    return text


def verified_merge(value: dict) -> tuple[str, str]:
    if value.get("version") != "1.0" or value.get("verdict") != "verified":
        raise ValueError("post-merge authority requires verified merge evidence v1")
    merge_sha = require_oid(value.get("merge_sha"), "merge_sha")
    tree_sha = require_oid(value.get("tree_sha"), "tree_sha")
    return merge_sha, tree_sha


def assert_exact_main(*, merge_sha: str, main_sha: str) -> None:
    require_oid(merge_sha, "merge_sha")
    require_oid(main_sha, "origin/main")
    if main_sha != merge_sha:
        raise ValueError("origin/main moved before post-merge validation")


def result_payload(
    *,
    merge_sha: str,
    tree_sha: str,
    current_main_sha: str,
    transcript: str,
    observed: dict,
) -> dict:
    assert_exact_main(merge_sha=merge_sha, main_sha=current_main_sha)
    if not isinstance(observed.get("e2e_steps"), int) or observed["e2e_steps"] < 1:
        raise ValueError("post-merge observation contains no browser steps")
    return {
        "version": "1.0",
        "verdict": "verified",
        "merge_sha": merge_sha,
        "tree_sha": tree_sha,
        "origin_main_sha": current_main_sha,
        "harness_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "observed": observed,
    }


def run(argv: list[str], *, cwd: Path, timeout: int, env: dict[str, str] | None = None,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and proc.returncode:
        detail = ((proc.stdout or "") + (proc.stderr or ""))[-3000:]
        raise RuntimeError(f"{' '.join(argv)} failed: {detail}")
    return proc


def git_oid(rev: str, *, cwd: Path) -> str:
    value = run(["git", "rev-parse", rev], cwd=cwd, timeout=30).stdout.strip()
    return require_oid(value, rev)


def execute(*, merge_verification: Path, output: Path) -> dict:
    merge = load_json(merge_verification)
    merge_sha, expected_tree = verified_merge(merge)

    # GitHub authority is not needed here. The parent trusted kernel fetches before invoking this
    # authority in production; diagnostics may also run in a checkout with a public origin.
    run(["git", "fetch", "origin", "main", "--quiet"], cwd=ROOT, timeout=120)
    current_main = git_oid("origin/main", cwd=ROOT)
    assert_exact_main(merge_sha=merge_sha, main_sha=current_main)

    actual_tree = git_oid(f"{merge_sha}^{{tree}}", cwd=ROOT)
    if actual_tree != expected_tree:
        raise ValueError("verified merge tree changed before post-merge validation")

    work_root = Path(os.environ.get("FACTORY_WORKDIR", "/tmp/dark-factory"))
    worktree = create_detached(ROOT, merge_sha, base_dir=work_root / "post-merge-worktrees")
    try:
        # The actual merge commit gets its own locked dependency environment. No state is reused
        # from the pre-merge validator worktree.
        run(
            ["uv", "sync", "--frozen", "--all-extras"],
            cwd=worktree.path / "app" / "backend",
            timeout=600,
            env=scoped_environment(scope="none"),
        )
        run(
            ["bun", "install", "--frozen-lockfile"],
            cwd=worktree.path / "app" / "frontend",
            timeout=600,
            env=scoped_environment(scope="none"),
        )

        harness = run(
            [sys.executable, "harness/ci.py"],
            cwd=worktree.path,
            timeout=3600,
            env=scoped_environment(scope="validation"),
            check=False,
        )
        transcript = (harness.stdout or "") + (harness.stderr or "")
        if harness.returncode:
            raise RuntimeError("canonical full harness failed on merged main: " + transcript[-3000:])
        observed = parse_transcript(transcript)

        head_after = git_oid("HEAD", cwd=worktree.path)
        tree_after = git_oid("HEAD^{tree}", cwd=worktree.path)
        if head_after != merge_sha or tree_after != expected_tree:
            raise RuntimeError("post-merge worktree HEAD/tree changed during validation")
        status = run(["git", "status", "--porcelain"], cwd=worktree.path, timeout=30).stdout.strip()
        if status:
            raise RuntimeError("post-merge full harness left the worktree dirty")

        payload = result_payload(
            merge_sha=merge_sha,
            tree_sha=expected_tree,
            current_main_sha=current_main,
            transcript=transcript,
            observed=observed,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical(payload))
        print(
            f"POST_MERGE_OK merge={merge_sha} tree={expected_tree} "
            f"steps={observed['e2e_steps']} harness_sha256={payload['harness_sha256']}"
        )
        return payload
    finally:
        remove(ROOT, worktree, force=True, require_clean=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge-verification", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        execute(merge_verification=args.merge_verification, output=args.output)
    except (RuntimeError, ValueError) as exc:
        die(str(exc))


if __name__ == "__main__":
    main()
