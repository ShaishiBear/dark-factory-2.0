"""Deterministic Git authority for model-authored checkout changes.

Model workers never stage or commit. This module validates the dirty-file envelope, stages exactly
that envelope and creates the commit with a kernel-owned identity.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable, Mapping

from .worker_policy import KERNEL_COMMIT_ARGS


class GitAuthorityError(RuntimeError):
    pass


TEST_MARKERS = ("/tests/", "/__tests__/", ".test.", ".spec.")


def _run(cwd: Path, argv: list[str]) -> str:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode:
        detail = ((proc.stdout or "") + (proc.stderr or ""))[-1600:]
        raise GitAuthorityError(f"{' '.join(argv)} failed: {detail}")
    return (proc.stdout or "").strip()


def _safe_rel(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(path) and not p.is_absolute() and ".." not in p.parts and p.as_posix() == path


def _test_oriented(path: str) -> bool:
    low = "/" + path.lower()
    return (
        "test" in PurePosixPath(path).name.lower()
        or any(marker in low for marker in TEST_MARKERS)
        or low.endswith("/conftest.py")
    )


def dirty_paths(cwd: Path) -> list[str]:
    """Return tracked/staged/untracked changes without trusting model-created Git state."""
    values: set[str] = set()
    for argv in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        output = _run(cwd, argv)
        values.update(line for line in output.splitlines() if line)
    bad = sorted(path for path in values if not _safe_rel(path))
    if bad:
        raise GitAuthorityError("unsafe dirty paths: " + ", ".join(bad))
    return sorted(values)


def _load_object(path: Path, name: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitAuthorityError(f"cannot read {name}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise GitAuthorityError(f"{name} must contain an object")
    return value


def _commit(cwd: Path, paths: list[str], subject: str, body: str | None = None) -> str:
    if not paths:
        raise GitAuthorityError("refusing to create an empty worker commit")
    _run(cwd, ["git", "add", "-A", "--", *paths])
    argv = ["git", *KERNEL_COMMIT_ARGS, "commit", "-m", subject]
    if body:
        argv.extend(["-m", body])
    _run(cwd, argv)
    remaining = dirty_paths(cwd)
    if remaining:
        raise GitAuthorityError("worker commit left uncommitted changes: " + ", ".join(remaining))
    return _run(cwd, ["git", "rev-parse", "HEAD"])


def commit_acceptance_tests(cwd: Path, spec_path: Path) -> str:
    spec = _load_object(spec_path, "acceptance test spec")
    checkpoints = spec.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise GitAuthorityError("acceptance test spec has no checkpoints")
    declared: set[str] = set()
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, Mapping):
            raise GitAuthorityError("acceptance checkpoint must be an object")
        files = checkpoint.get("files")
        if not isinstance(files, list) or not files:
            raise GitAuthorityError("acceptance checkpoint has no files")
        for value in files:
            if not isinstance(value, str) or not _safe_rel(value) or not _test_oriented(value):
                raise GitAuthorityError(f"invalid acceptance-test path: {value!r}")
            declared.add(value)
    actual = dirty_paths(cwd)
    if actual != sorted(declared):
        raise GitAuthorityError(
            f"test-author changed {actual}; declared acceptance files are {sorted(declared)}"
        )
    return _commit(cwd, actual, "test(factory): prove acceptance contract red")


def commit_planned_changes(
    cwd: Path,
    *,
    design_path: Path,
    red_proof_path: Path,
    subject: str,
    issue_number: int,
) -> str:
    design = _load_object(design_path, "compiled design")
    proof = _load_object(red_proof_path, "RED proof")
    planned_raw = design.get("planned_files")
    if not isinstance(planned_raw, list) or not planned_raw or any(
        not isinstance(path, str) or not _safe_rel(path) for path in planned_raw
    ):
        raise GitAuthorityError("compiled design has invalid planned_files")
    proof_files = proof.get("files")
    if not isinstance(proof_files, Mapping):
        raise GitAuthorityError("RED proof has invalid immutable files")
    immutable = {str(path) for path in proof_files}
    actual = dirty_paths(cwd)
    if not actual:
        raise GitAuthorityError("worker produced no checkout changes")
    touched_immutable = sorted(set(actual) & immutable)
    if touched_immutable:
        raise GitAuthorityError(
            "worker modified immutable acceptance tests: " + ", ".join(touched_immutable)
        )
    outside = sorted(set(actual) - set(planned_raw))
    if outside:
        raise GitAuthorityError(
            "worker changed files outside compiled design: " + ", ".join(outside)
        )
    return _commit(cwd, actual, subject, f"Fixes #{issue_number}")
