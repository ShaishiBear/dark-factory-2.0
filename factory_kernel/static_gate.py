"""File-scoped static checks the kernel runs on a worker's uncommitted files.

Why this exists (D-043): the quick gate runs the repository's five static checks after the
build, but by then the acceptance tests are RED-hashed and immutable, so a lint failure in a
test file can never be repaired by `implement` or `repair`; the build is structurally doomed
the moment RED freezes a lint-failing file. This module runs the same tools, scoped to the
files a worker just wrote and BEFORE the kernel commits them, so the worker that can still edit
the files is the one told about the failure.

Weakens nothing: the quick gate still runs every check over the whole tree afterwards and is
the authority. This gate only moves a subset of it earlier, where it is repairable.

Scope by tool, chosen by path prefix and suffix, mirroring `harness/static.py`'s commands:

* `app/backend/**/*.py`   -> `uv run ruff check <files>` and `uv run ruff format --check <files>`
* `app/frontend/**/*.{ts,tsx,js,jsx,mts,cts}` -> `bun x biome check <files>`

`mypy` and `tsc` are whole-program checks with no honest file scope, so they stay in the quick
gate only. Files outside both stacks (factory tests, docs) have no static rule here.

The formatter runs before the hand-back (D-068): a finding the repository's own formatter can
remove is whitespace, and whitespace must never cost a model stage. When the scoped checks fail,
this module applies `ruff format` / `biome format --write` to the same files, records which files
the formatter actually rewrote (by content, not by the tool's summary line), and re-runs the
checks. Only a finding that survives that is handed back to the worker. Lint *fixes* are never
applied: `--fix`, `--unsafe-fixes` and `biome check --write` can change behaviour, and the whole
point of the hand-back is that a behavioural defect goes to the author who can judge it.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
from typing import Callable, Mapping, Sequence

from .credential_env import scoped_environment

BACKEND_PREFIX = "app/backend/"
FRONTEND_PREFIX = "app/frontend/"
BACKEND_SUFFIXES = (".py",)
FRONTEND_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")
OUTPUT_TAIL_CHARS = 4000
TIMEOUT_SECONDS = 300

Runner = Callable[[Sequence[str], Path, Mapping[str, str], int], subprocess.CompletedProcess]


def default_runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv), cwd=cwd, env=dict(env), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


@dataclass(frozen=True)
class StaticResult:
    ok: bool
    checks: tuple[str, ...]
    output: str = ""
    skipped: tuple[str, ...] = field(default_factory=tuple)
    # Repo-relative paths the formatter rewrote before the checks were re-run (D-068). Empty
    # when the first pass was clean, when no formatter changed anything, and when no formatter
    # could run at all.
    formatted: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        formatted = (
            "\nSTATIC_SCOPED_FORMATTED files=" + ",".join(self.formatted) if self.formatted else ""
        )
        if self.ok:
            return "STATIC_SCOPED_OK checks=" + ",".join(self.checks) + formatted
        return (
            "STATIC_SCOPED_FAILED checks=" + ",".join(self.checks) + formatted
            + "\n" + self.output
        )


def partition(files: Sequence[str]) -> tuple[list[str], list[str], list[str]]:
    """Split repo-relative paths into (backend python, frontend ts/js, unscoped)."""
    backend: list[str] = []
    frontend: list[str] = []
    other: list[str] = []
    for rel in files:
        norm = rel.replace("\\", "/")
        if norm.startswith(BACKEND_PREFIX) and norm.endswith(BACKEND_SUFFIXES):
            backend.append(norm[len(BACKEND_PREFIX):])
        elif norm.startswith(FRONTEND_PREFIX) and norm.endswith(FRONTEND_SUFFIXES):
            frontend.append(norm[len(FRONTEND_PREFIX):])
        else:
            other.append(norm)
    return backend, frontend, other


def commands_for(files: Sequence[str]) -> list[tuple[str, str, list[str]]]:
    """(label, cwd-relative-to-worktree, argv) for every scoped check the files need."""
    backend, frontend, _ = partition(files)
    plan: list[tuple[str, str, list[str]]] = []
    if backend:
        plan.append(("ruff-lint", BACKEND_PREFIX.rstrip("/"), ["uv", "run", "ruff", "check", *backend]))
        plan.append(("ruff-format", BACKEND_PREFIX.rstrip("/"), ["uv", "run", "ruff", "format", "--check", *backend]))
    if frontend:
        plan.append(("biome", FRONTEND_PREFIX.rstrip("/"), ["bun", "x", "biome", "check", *frontend]))
    return plan


def format_commands_for(files: Sequence[str]) -> list[tuple[str, str, list[str]]]:
    """(label, cwd-relative-to-worktree, argv) for the FORMATTER of every stack the files touch.

    The formatter only: `ruff format` and `biome format --write` rewrite whitespace and can not
    change behaviour. `ruff check --fix`, `--unsafe-fixes` and `biome check --write` apply lint
    fixes, which can; those stay the worker's to make (D-068).
    """
    backend, frontend, _ = partition(files)
    plan: list[tuple[str, str, list[str]]] = []
    if backend:
        plan.append(("ruff-format-write", BACKEND_PREFIX.rstrip("/"), ["uv", "run", "ruff", "format", *backend]))
    if frontend:
        plan.append(
            ("biome-format-write", FRONTEND_PREFIX.rstrip("/"),
             ["bun", "x", "biome", "format", "--write", *frontend])
        )
    return plan


def check_files(
    worktree: Path,
    files: Sequence[str],
    *,
    runner: Runner = default_runner,
    timeout: int = TIMEOUT_SECONDS,
) -> StaticResult:
    """Run every scoped check the files need inside `worktree`, with no credentials.

    A tool that is missing or times out is a failure, not a skip: a check that silently did
    not run is exactly the shape the quick gate refuses too.

    A first pass that fails is not yet a hand-back. The formatter is applied to the same files
    and the checks are re-run; only what survives that is a finding (D-068). If no formatter
    rewrote anything - because none could run, or because the finding was never whitespace -
    the first pass's result is returned exactly as it was, and `formatted` is empty.
    """
    plan = commands_for(files)
    _, _, unscoped = partition(files)
    if not plan:
        return StaticResult(ok=True, checks=(), skipped=tuple(unscoped))
    env = scoped_environment(None, scope="none")
    env.setdefault("PATH", os.environ.get("PATH", ""))
    first = _run_plan(plan, worktree, env, runner=runner, timeout=timeout, unscoped=unscoped)
    if first.ok:
        return first
    formatted = _apply_formatter(worktree, files, env, runner=runner, timeout=timeout)
    if not formatted:
        return first
    second = _run_plan(plan, worktree, env, runner=runner, timeout=timeout, unscoped=unscoped)
    return dataclasses.replace(second, formatted=formatted)


def _run_plan(
    plan: Sequence[tuple[str, str, list[str]]],
    worktree: Path,
    env: Mapping[str, str],
    *,
    runner: Runner,
    timeout: int,
    unscoped: Sequence[str],
) -> StaticResult:
    failures: list[str] = []
    labels: list[str] = []
    for label, cwd_rel, argv in plan:
        labels.append(label)
        cwd = worktree / cwd_rel
        try:
            proc = runner(argv, cwd, env, timeout)
        except FileNotFoundError:
            failures.append(f"--- {label} ---\n{argv[0]} is not on PATH")
            continue
        except subprocess.TimeoutExpired:
            failures.append(f"--- {label} ---\ntimed out after {timeout}s")
            continue
        if proc.returncode != 0:
            text = ((proc.stdout or "") + (proc.stderr or "")).strip()[-OUTPUT_TAIL_CHARS:]
            failures.append(f"--- {label} ---\n{text}")
    if failures:
        return StaticResult(ok=False, checks=tuple(labels), output="\n".join(failures), skipped=tuple(unscoped))
    return StaticResult(ok=True, checks=tuple(labels), skipped=tuple(unscoped))


def _apply_formatter(
    worktree: Path,
    files: Sequence[str],
    env: Mapping[str, str],
    *,
    runner: Runner,
    timeout: int,
) -> tuple[str, ...]:
    """Run the stacks' formatters over `files` and return what they actually rewrote.

    Measured by content, not by a tool's summary line: the bytes of every scoped file are read
    before and after, and a file counts as reformatted only when they differ. A formatter that
    is missing, times out, or exits non-zero is not an error here - it simply rewrote nothing,
    and the caller then returns the first pass's finding unchanged.
    """
    scoped = [rel.replace("\\", "/") for rel in files]
    backend, frontend, _ = partition(scoped)
    if not backend and not frontend:
        return ()
    before = {rel: _read_bytes(worktree / rel) for rel in scoped}
    for _label, cwd_rel, argv in format_commands_for(scoped):
        try:
            runner(argv, worktree / cwd_rel, env, timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return tuple(
        sorted(rel for rel in scoped if _read_bytes(worktree / rel) != before[rel])
    )


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None
