"""Trust-root programs execute from the kernel's checkout, never from the subject's copy.

The kernel names its deterministic authorities by repository-relative path
(`scripts/factory_*.py`, `harness/merge_verify.py`, `harness/post_merge.py`) and runs them with
the working directory set to the tree under test: a PR-head worktree during validation, re-head
and resume, the kernel's own checkout during dispatch. Python resolves a relative program path
against that working directory, so until D-036 the PR head's copy of the authority was the
program that judged the PR. The resume of the factory's first PR (#74, worker run 33927770223)
ran the head's pre-#75 `factory_provenance.py` and died on an import the kernel had already
fixed on main; a tampered copy would have been run just as readily.

`resolve_trusted_program` rewrites the program path to the kernel's checkout and leaves the
working directory alone. Every script derives the tree it inspects from its working directory,
not from its own location, so main's code operates on the PR's tree.

Deliberately not resolved: `harness/ci.py` and the rest of the canonical harness. That is the
harness *under test*, run inside the worktree by design; the Evidence Bundle refuses a PR whose
trust root differs from `origin/main` before it runs that harness (`factory_evidence.py`
`trust_root_drift`), and that check itself now runs from main's code.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Sequence

# Repository-relative programs the kernel treats as authorities over a PR.
AUTHORITY_DIRS: tuple[str, ...] = ("scripts/",)
AUTHORITY_PROGRAMS: frozenset[str] = frozenset({"harness/merge_verify.py", "harness/post_merge.py"})


class TrustedProgramMissing(RuntimeError):
    """The kernel's checkout does not carry the program it was asked to run."""


def is_authority(program: str) -> bool:
    """A repository-relative path the kernel must execute from its own checkout."""
    posix = program.replace("\\", "/")
    if PurePosixPath(posix).is_absolute() or posix.startswith("/") or ":" in posix.split("/")[0]:
        return False
    return posix.startswith(AUTHORITY_DIRS) or posix in AUTHORITY_PROGRAMS


def resolve_trusted_program(repo_root: Path, argv: Sequence[str]) -> list[str]:
    """Return argv with a trust-root program path rewritten to the kernel's checkout.

    Only `python <repo-relative program> ...` shapes are touched; `git`, `uv`, `bun`, the
    quick-gate harness and absolute paths pass through unchanged. The working directory is
    the caller's concern and is never changed here.
    """
    items = list(argv)
    if len(items) < 2 or items[0] != "python":
        return items
    program = items[1]
    if not is_authority(program):
        return items
    target = (Path(repo_root) / program.replace("\\", "/")).resolve()
    if not target.is_file():
        raise TrustedProgramMissing(
            f"trust-root program {program!r} is missing from the kernel checkout {Path(repo_root)}"
        )
    items[1] = str(target)
    return items
