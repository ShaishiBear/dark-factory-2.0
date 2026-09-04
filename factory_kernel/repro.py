"""Kernel-executed reproduction loop for bug issues.

A root cause proposed before the failure has been seen going red is a guess. The investigate
worker cannot run commands, so it proposes the smallest repro and names the symptom; the kernel
executes it here, deterministically, and refuses to continue unless the command fails and its
output shows the named symptom. Only then does the contract stage see the investigation.

What bounds a model-authored command here, and what does not:

* **Command shapes, not program names.** The argv must match one of `ALLOWED_SHAPES` -- the
  repository's own test runners (`pytest`, `python -m pytest`, `uv run pytest`, `bun test`,
  `bun run test`, `bunx vitest run`). "python is allowlisted" was not a boundary: `python -c`
  is arbitrary code. Interpreter-eval flags, shell metacharacters, absolute paths and `..` are
  refused in every argument. This is a shape allowlist, not a sandbox: a test file the command
  selects can still execute arbitrary Python or TypeScript.
* **An environment built from scratch.** `repro_env` forwards only the names in `REPRO_ENV_KEYS`
  (PATH, HOME, temp dirs, locale, PYTHONPATH) and sets `PYTHONDONTWRITEBYTECODE`. Nothing else
  from the parent reaches the child, so a new secret added to the worker's environment is
  withheld by construction rather than by a denylist someone must remember to extend.
* **A clean-tree guard around the run.** The kernel compares `git status` before and after the
  repro (`KernelRuntime._observe_repro`) and escalates if anything in the worktree changed, so a
  repro cannot rewrite what the contract worker reads next.
* **No shell, confined cwd, bounded time and output.**

Together these bound the damage of a hostile or careless repro to: reading the checkout, using
the runner's CPU for `timeout` seconds, and printing. They do not make the command trusted; its
output is evidence only of what it printed and its exit status.

Two modes, one red loop
-----------------------

An executed repro only fits a bug that an *existing* runner already fails on: a crash, an
exception, a test that is red today. The commonest bug is a wrong behaviour no test covers, and
for that no allowlisted command can fail on the unchanged tree, so the investigate worker must
not invent one. It writes `repro-deferred.json` instead: why no existing command can fail, the
seam the acceptance tests will exercise, and the exact symptom those tests will demonstrate.
The kernel validates the record and hands it to the contract worker as a deferred red loop,
then closes the loop where the factory already proves RED: after `factory_proof.py red`, at
least one checkpoint's failing output must contain the promised symptom
(`verify_deferred_in_red`), or the run escalates. The red loop is mandatory in both modes;
only where it is observed moves. Exactly one of the two artifacts may exist.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Mapping

REPRO_ARTIFACT = "repro.json"
DEFERRED_ARTIFACT = "repro-deferred.json"
OBSERVED_ARTIFACT = "repro-observed.json"
MIN_SYMPTOM_CHARS = 4
MAX_DEFERRED_FIELD_CHARS = 2000
MAX_ARGV = 40
MAX_OUTPUT_CHARS = 200_000

# Each shape is the exact argv prefix a repro must start with. The remainder is test-selection
# arguments, checked word by word by `_refuse_dangerous_argument`.
ALLOWED_SHAPES: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("uv", "run", "pytest"),
    ("uv", "run", "python", "-m", "pytest"),
    ("bun", "test"),
    ("bun", "run", "test"),
    ("bunx", "vitest", "run"),
    ("bun", "x", "vitest", "run"),
)

# Flags that turn a test runner into an interpreter, or interpreter flags that evaluate text.
EVAL_FLAGS: frozenset[str] = frozenset({"-c", "-e", "--eval", "-x", "--exec", "exec", "-p", "--print"})
SHELL_METACHARACTERS = re.compile(r"[;|&<>`]|\$\(")

# The only names a model-authored command may inherit from the kernel's environment.
REPRO_ENV_KEYS: tuple[str, ...] = (
    "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TERM", "CI", "NO_COLOR",
    "PYTHONPATH",
)
REPRO_ENV_SYNTHETIC: dict[str, str] = {"PYTHONDONTWRITEBYTECODE": "1"}


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


def matched_shape(argv: tuple[str, ...]) -> tuple[str, ...] | None:
    for shape in ALLOWED_SHAPES:
        if tuple(argv[: len(shape)]) == shape:
            return shape
    return None


def _refuse_dangerous_argument(arg: str) -> None:
    if any(ch in arg for ch in ("\n", "\r", "\x00")):
        raise ReproRefused("repro argv contains control characters")
    if arg in EVAL_FLAGS or arg.split("=", 1)[0] in EVAL_FLAGS:
        raise ReproRefused(f"repro argv contains an eval/exec flag: {arg!r}")
    if SHELL_METACHARACTERS.search(arg):
        raise ReproRefused(f"repro argv contains a shell metacharacter: {arg!r}")
    if arg.startswith(("/", "\\", "~")) or (len(arg) > 1 and arg[1] == ":"):
        raise ReproRefused(f"repro argv contains an absolute path: {arg!r}")
    if ".." in Path(arg).parts:
        raise ReproRefused(f"repro argv escapes the checkout: {arg!r}")


def validate_repro(raw: object) -> Repro:
    if not isinstance(raw, Mapping) or raw.get("version") != "1.0":
        raise ReproRefused("repro.json must be a version 1.0 object")
    argv = raw.get("argv")
    if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV:
        raise ReproRefused("repro argv must be a non-empty list")
    if not all(isinstance(a, str) and a.strip() for a in argv):
        raise ReproRefused("repro argv entries must be non-empty strings")
    shape = matched_shape(tuple(argv))
    if shape is None:
        raise ReproRefused(
            "repro command shape is not allowlisted: "
            + " ".join(argv[:4])
            + " (allowed: " + "; ".join(" ".join(s) for s in ALLOWED_SHAPES) + ")"
        )
    for arg in argv[len(shape):]:
        _refuse_dangerous_argument(arg)
    symptom = raw.get("expect_failure_containing")
    if not isinstance(symptom, str) or len(symptom.strip()) < 4:
        raise ReproRefused("repro must name a symptom of at least four characters")
    cwd = raw.get("cwd", ".")
    if not isinstance(cwd, str) or not cwd.strip():
        raise ReproRefused("repro cwd must be a non-empty relative path")
    if cwd.startswith(("/", "\\")) or ".." in Path(cwd).parts or (len(cwd) > 1 and cwd[1] == ":"):
        raise ReproRefused("repro cwd must stay inside the checkout")
    return Repro(argv=tuple(argv), expect_failure_containing=symptom.strip(), cwd=cwd.strip())


def repro_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the child environment from an allowlist; nothing else from the parent reaches it."""
    base = dict(os.environ if source is None else source)
    child = {key: base[key] for key in REPRO_ENV_KEYS if key in base and base[key]}
    child.update(REPRO_ENV_SYNTHETIC)
    return child


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
        proc = runner(repro.argv, cwd, repro_env(), timeout)
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
        "mode": "executed",
        "argv": list(repro.argv),
        "cwd": repro.cwd,
        "rc": observation.rc,
        "output_sha256": observation.output_sha256,
        "matched_symptom": observation.matched_symptom,
        "output_tail": observation.output_tail,
    }


@dataclass(frozen=True)
class Deferred:
    reason: str
    seam: str
    expected_symptom: str


def _bounded_text(raw: object, name: str, *, minimum: int = 1) -> str:
    if not isinstance(raw, str):
        raise ReproRefused(f"deferred repro {name} must be a string")
    value = raw.strip()
    if len(value) < minimum:
        raise ReproRefused(f"deferred repro {name} must be at least {minimum} characters")
    if len(value) > MAX_DEFERRED_FIELD_CHARS:
        raise ReproRefused(f"deferred repro {name} exceeds {MAX_DEFERRED_FIELD_CHARS} characters")
    if any(ch in value for ch in ("\x00",)):
        raise ReproRefused(f"deferred repro {name} contains control characters")
    return value


def validate_deferred(raw: object) -> Deferred:
    """A deferred record promises a symptom the RED tests will show; nothing here executes."""
    if not isinstance(raw, Mapping) or raw.get("version") != "1.0":
        raise ReproRefused("repro-deferred.json must be a version 1.0 object")
    reason = _bounded_text(raw.get("reason"), "reason", minimum=20)
    seam = _bounded_text(raw.get("seam"), "seam", minimum=3)
    if "\n" in seam or seam.startswith(("/", "\\", "~")) or ".." in Path(seam).parts:
        raise ReproRefused("deferred repro seam must be a repo-relative path or a symbol name")
    symptom = _bounded_text(raw.get("expected_symptom"), "expected_symptom", minimum=MIN_SYMPTOM_CHARS)
    return Deferred(reason=reason, seam=seam, expected_symptom=symptom)


def load_deferred(path: Path) -> Deferred:
    if not path.is_file():
        raise ReproRefused("investigate worker wrote no repro-deferred.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReproRefused(f"repro-deferred.json is unreadable: {exc}") from exc
    return validate_deferred(raw)


def deferred_record(deferred: Deferred) -> dict:
    return {
        "version": "1.0",
        "mode": "deferred",
        "reason": deferred.reason,
        "seam": deferred.seam,
        "expected_symptom": deferred.expected_symptom,
        "observed_in_red": None,
    }


def verify_deferred_in_red(record: Mapping, red_proof: Mapping) -> dict:
    """Close a deferred red loop against the RED proof: some checkpoint must have shown the symptom.

    `factory_proof.py red` records a bounded tail of each checkpoint's failing output. The
    promised symptom must appear in at least one of them, case-insensitively, exactly as the
    executed mode requires it to appear in the repro's own output.
    """
    if record.get("mode") != "deferred":
        raise ReproRefused("only a deferred repro is closed against RED")
    symptom = str(record.get("expected_symptom") or "").strip()
    if len(symptom) < MIN_SYMPTOM_CHARS:
        raise ReproRefused("deferred repro record lacks a symptom")
    checkpoints = red_proof.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ReproRefused("RED proof has no checkpoints to close the deferred repro against")
    needle = symptom.lower()
    for cp in checkpoints:
        if not isinstance(cp, Mapping):
            continue
        tail = cp.get("red_output_tail")
        if isinstance(tail, str) and needle in tail.lower():
            return {"checkpoint": str(cp.get("acceptance_id") or ""), "matched": True,
                    "symptom": symptom}
    raise ReproRefused("deferred repro symptom never observed in RED output")
