"""Deterministic Git authority for model-authored checkout changes.

Model workers never stage or commit. This module validates the dirty-file envelope, stages exactly
that envelope and creates the commit with a kernel-owned identity.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Callable, Iterable, Mapping

from .credential_env import scoped_environment

from .worker_policy import KERNEL_COMMIT_ARGS


class GitAuthorityError(RuntimeError):
    pass


TEST_MARKERS = ("/tests/", "/__tests__/", ".test.", ".spec.")

# A manifest a worker may edit, the lockfile the guard requires alongside it, the command that
# refreshes that lockfile, and where to run it. Workers have no shell, so the kernel runs the
# refresh; the lockfile must be in the compiled design or the change is refused before any command
# runs, and the refresh may change nothing but the lockfile.
MANIFEST_LOCKS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("app/backend/pyproject.toml", "app/backend/uv.lock", ("uv", "lock"), "app/backend"),
    ("app/frontend/package.json", "app/frontend/bun.lock", ("bun", "install", "--no-progress"), "app/frontend"),
)
LockRunner = Callable[[Path, list[str]], None]


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


def _load_shared_test_predicate():
    """scripts/factory_shapes.test_shaped, loaded by path so the kernel needs no sys.path entry.

    The scripts are standalone programs and must not import the kernel; the kernel may load the
    one shared predicate from them so all three test-classifying authorities agree (D-030)."""
    import importlib.util

    source = Path(__file__).resolve().parents[1] / "scripts" / "factory_shapes.py"
    spec = importlib.util.spec_from_file_location("factory_shapes_shared", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.test_shaped


_shared_test_shaped = _load_shared_test_predicate()


def _test_oriented(path: str) -> bool:
    # Shared with the RED gate and the architecture guard (scripts/factory_shapes.test_shaped).
    return _shared_test_shaped(path)


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


def _default_lock_runner(cwd: Path, argv: list[str]) -> None:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=scoped_environment(scope="none"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    if proc.returncode:
        detail = ((proc.stdout or "") + (proc.stderr or ""))[-1600:]
        raise GitAuthorityError(f"lockfile refresh {' '.join(argv)} failed: {detail}")


def refresh_lockfiles(
    cwd: Path, planned: Iterable[str], *, runner: LockRunner | None = None
) -> list[str]:
    """Refresh the lockfile of every manifest the worker changed, and nothing else.

    Refuses if a changed manifest's lockfile is not a planned file, and refuses if the refresh
    touched any path other than that lockfile. Returns the lockfiles that now differ.
    """
    planned_set = set(planned)
    dirty = set(dirty_paths(cwd))
    refreshed: list[str] = []
    for manifest, lock, argv, subdir in MANIFEST_LOCKS:
        if manifest not in dirty:
            continue
        if lock not in planned_set:
            raise GitAuthorityError(
                f"{manifest} changed but {lock} is not in the compiled design; "
                "a dependency change must plan its lockfile"
            )
        (runner or _default_lock_runner)(cwd / subdir, list(argv))
        after = set(dirty_paths(cwd))
        extra = sorted(after - dirty - {lock})
        if extra:
            raise GitAuthorityError(f"lockfile refresh changed files beyond {lock}: {', '.join(extra)}")
        if lock in after:
            refreshed.append(lock)
        dirty = after
    return refreshed


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


CHECKPOINT_KINDS = ("red", "guard")


def commit_acceptance_tests(cwd: Path, spec_path: Path) -> str:
    """Commit exactly what the test author wrote, judged against what the spec declares.

    Three rules, the same three the RED gate applies to the commit this creates
    (scripts/factory_proof.declared_files): every changed file is declared; every file of a
    `red` checkpoint is changed (a red test is new or modified); a `guard` checkpoint's file
    exists at the head and may be untouched, because a guard pins an existing test the author
    must not rewrite, so a changed guard file is accepted only when a red checkpoint declares
    it too. Before D-064 the dirty checkout had to equal the declared union, which refused the
    first correct guard (run 34027157595: the guard named the kept hook test, unchanged).
    """
    spec = _load_object(spec_path, "acceptance test spec")
    checkpoints = spec.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise GitAuthorityError("acceptance test spec has no checkpoints")
    red_files: set[str] = set()
    guard_files: set[str] = set()
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, Mapping):
            raise GitAuthorityError("acceptance checkpoint must be an object")
        kind = checkpoint.get("kind", "red")
        if kind not in CHECKPOINT_KINDS:
            raise GitAuthorityError(
                f"acceptance checkpoint kind must be one of {list(CHECKPOINT_KINDS)}; got {kind!r}"
            )
        files = checkpoint.get("files")
        if not isinstance(files, list) or not files:
            raise GitAuthorityError("acceptance checkpoint has no files")
        for value in files:
            if not isinstance(value, str) or not _safe_rel(value) or not _test_oriented(value):
                raise GitAuthorityError(f"invalid acceptance-test path: {value!r}")
            (guard_files if kind == "guard" else red_files).add(value)
    declared = sorted(red_files | guard_files)
    actual = dirty_paths(cwd)
    changed = set(actual)
    stray = sorted(changed - set(declared))
    if stray:
        raise GitAuthorityError(
            f"test-author changed undeclared files {stray}; every changed file must be declared "
            f"by a checkpoint (declared acceptance files are {declared})"
        )
    unchanged_red = sorted(red_files - changed)
    if unchanged_red:
        raise GitAuthorityError(
            f"red checkpoint files {unchanged_red} are unchanged; a red checkpoint's file must "
            f"be new or modified (test-author changed {actual})"
        )
    for path in sorted(guard_files):
        if not (cwd / path).is_file():
            raise GitAuthorityError(
                f"guard checkpoint file {path!r} does not exist at the head; a guard pins an "
                "existing test"
            )
        if path in changed and path not in red_files:
            raise GitAuthorityError(
                f"guard checkpoint file {path!r} was changed and no red checkpoint declares it; "
                "a guard alone must not rewrite the test it guards"
            )
    return _commit(cwd, actual, "test(factory): prove acceptance contract red")


def commit_planned_changes(
    cwd: Path,
    *,
    design_path: Path,
    red_proof_path: Path,
    subject: str,
    issue_number: int,
    lock_runner: LockRunner | None = None,
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
    refresh_lockfiles(cwd, [str(path) for path in planned_raw], runner=lock_runner)
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
