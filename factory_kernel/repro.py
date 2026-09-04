"""Kernel-executed reproduction loop for bug issues.

A root cause proposed before the failure has been seen going red is a guess. The investigate
worker cannot run commands, so it proposes the smallest repro and names the symptom; the kernel
executes it here, deterministically, with no shell, no credentials and an allowlisted program,
and refuses to continue unless the command fails and its output shows the named symptom. Only
then does the contract stage see the investigation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Callable, Mapping

from .credential_env import GITHUB_CREDENTIALS

REPRO_ARTIFACT = "repro.json"
OBSERVED_ARTIFACT = "repro-observed.json"
ALLOWED_PROGRAMS: frozenset[str] = frozenset({"python", "uv", "bun", "npx", "pytest"})
MAX_ARGV = 40
MAX_OUTPUT_CHARS = 200_000


class ReproRefused(ValueError):
    """The proposed reproduction cannot serve as evidence; the caller escalates."""


@dataclass(frozen=True)
class Repro:
    argv: tuple[str, ...]
    expect_failure_containing: str
    cwd: str  # repo-relative, "." for the root


@dataclass(frozen=True)
class Observation:
    rc: int
    output_sha256: str
    output_tail: str
    matched_symptom: str


def load_repro(path: Path) -> Repro:
    if not path.is_file():
        raise ReproRefused("investigate worker wrote no repro.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReproRefused(f"repro.json is unreadable: {exc}") from exc
    return validate_repro(raw)


def validate_repro(raw: object) -> Repro:
    if not isinstance(raw, Mapping) or raw.get("version") != "1.0":
        raise ReproRefused("repro.json must be a version 1.0 object")
    argv = raw.get("argv")
    if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV:
        raise ReproRefused("repro argv must be a non-empty list")
    if not all(isinstance(a, str) and a.strip() for a in argv):
        raise ReproRefused("repro argv entries must be non-empty strings")
    program = os.path.basename(argv[0])
    if program not in ALLOWED_PROGRAMS or program != argv[0]:
        raise ReproRefused(f"repro program is not allowlisted: {argv[0]!r}")
    for arg in argv[1:]:
        if any(ch in arg for ch in ("\n", "\r", "\x00")):
            raise ReproRefused("repro argv contains control characters")
    symptom = raw.get("expect_failure_containing")
    if not isinstance(symptom, str) or len(symptom.strip()) < 4:
        raise ReproRefused("repro must name a symptom of at least four characters")
    cwd = raw.get("cwd", ".")
    if not isinstance(cwd, str) or not cwd.strip():
        raise ReproRefused("repro cwd must be a non-empty relative path")
    if cwd.startswith(("/", "\\")) or ".." in Path(cwd).parts or (len(cwd) > 1 and cwd[1] == ":"):
        raise ReproRefused("repro cwd must stay inside the checkout")
    return Repro(argv=tuple(argv), expect_failure_containing=symptom.strip(), cwd=cwd.strip())


def scrubbed_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """No GitHub credentials reach a model-authored command, whatever the parent holds."""
    base = dict(os.environ if source is None else source)
    return {k: v for k, v in base.items() if k not in GITHUB_CREDENTIALS}


Runner = Callable[[tuple[str, ...], Path, dict[str, str], int], subprocess.CompletedProcess]


def default_runner(argv: tuple[str, ...], cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv), cwd=cwd, env=env, shell=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def execute(repro: Repro, *, worktree: Path, timeout: int = 600, runner: Runner = default_runner) -> Observation:
    """Run the repro and refuse unless it fails for the named reason."""
    cwd = (worktree / repro.cwd).resolve()
    if worktree.resolve() not in (cwd, *cwd.parents) or not cwd.is_dir():
        raise ReproRefused(f"repro cwd is outside the worktree or missing: {repro.cwd}")
    try:
        proc = runner(repro.argv, cwd, scrubbed_env(), timeout)
    except subprocess.TimeoutExpired as exc:
        raise ReproRefused(f"repro timed out after {timeout}s") from exc
    except OSError as exc:
        raise ReproRefused(f"repro could not start: {exc}") from exc
    output = ((proc.stdout or "") + (proc.stderr or ""))[-MAX_OUTPUT_CHARS:]
    if proc.returncode == 0:
        raise ReproRefused("repro passed; a bug that does not go red cannot be contracted")
    if repro.expect_failure_containing not in output:
        raise ReproRefused("repro failed but its output does not contain the named symptom")
    return Observation(
        rc=int(proc.returncode),
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        output_tail=output[-4000:],
        matched_symptom=repro.expect_failure_containing,
    )


def observed_record(repro: Repro, observation: Observation) -> dict:
    return {
        "version": "1.0",
        "argv": list(repro.argv),
        "cwd": repro.cwd,
        "rc": observation.rc,
        "output_sha256": observation.output_sha256,
        "matched_symptom": observation.matched_symptom,
        "output_tail": observation.output_tail,
    }
