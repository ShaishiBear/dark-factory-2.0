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
"""
from __future__ import annotations

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

    def describe(self) -> str:
        if self.ok:
            return "STATIC_SCOPED_OK checks=" + ",".join(self.checks)
        return "STATIC_SCOPED_FAILED checks=" + ",".join(self.checks) + "\n" + self.output


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
    """
    plan = commands_for(files)
    _, _, unscoped = partition(files)
    if not plan:
        return StaticResult(ok=True, checks=(), skipped=tuple(unscoped))
    env = scoped_environment(None, scope="none")
    env.setdefault("PATH", os.environ.get("PATH", ""))
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
