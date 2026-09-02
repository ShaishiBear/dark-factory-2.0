"""Safe exact-SHA git worktree primitive.

The implementation is intentionally small. It borrows Archon's useful worktree invariants
(exact repository identity, corruption checks, fail-loud cleanup) without importing Archon or
its workspace/database model.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import uuid

GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class WorktreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Worktree:
    path: Path
    head_sha: str


def _run(repo: Path, argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if check and proc.returncode:
        detail = ((proc.stdout or "") + (proc.stderr or ""))[-1200:]
        raise WorktreeError(f"git {' '.join(argv)} failed: {detail}")
    return proc


def resolve_commit(repo: str | Path, ref: str) -> str:
    root = Path(repo).resolve()
    sha = _run(root, ["rev-parse", "--verify", f"{ref}^{{commit}}"]).stdout.strip()
    if not GIT_OID.fullmatch(sha):
        raise WorktreeError(f"git returned invalid commit id for {ref!r}: {sha!r}")
    return sha


def is_clean(path: str | Path) -> bool:
    root = Path(path).resolve()
    return not _run(root, ["status", "--porcelain"]).stdout.strip()


def create_detached(
    repo: str | Path,
    ref: str,
    *,
    base_dir: str | Path,
    prefix: str = "df2",
) -> Worktree:
    root = Path(repo).resolve()
    base = Path(base_dir).resolve()
    if not root.is_dir():
        raise WorktreeError(f"repository does not exist: {root}")
    expected = resolve_commit(root, ref)
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"{prefix}-{uuid.uuid4().hex[:12]}"
    if target.exists():
        raise WorktreeError(f"worktree target already exists: {target}")
    _run(root, ["worktree", "add", "--detach", str(target), expected])
    try:
        actual = resolve_commit(target, "HEAD")
        if actual != expected:
            raise WorktreeError(f"worktree HEAD mismatch: expected {expected}, got {actual}")
        if not is_clean(target):
            raise WorktreeError("new worktree is unexpectedly dirty")
        return Worktree(path=target, head_sha=actual)
    except Exception:
        _run(root, ["worktree", "remove", "--force", str(target)], check=False)
        shutil.rmtree(target, ignore_errors=True)
        raise


def remove(
    repo: str | Path,
    worktree: Worktree,
    *,
    require_clean: bool = True,
    force: bool = False,
) -> None:
    root = Path(repo).resolve()
    if require_clean and force:
        raise ValueError("force removal cannot also require a clean worktree")
    if require_clean and worktree.path.exists() and not is_clean(worktree.path):
        raise WorktreeError(f"refusing to remove dirty worktree: {worktree.path}")
    argv = ["worktree", "remove"]
    if force:
        argv.append("--force")
    argv.append(str(worktree.path))
    _run(root, argv)
    _run(root, ["worktree", "prune"], check=False)


@contextmanager
def isolated(
    repo: str | Path,
    ref: str,
    *,
    base_dir: str | Path,
    prefix: str = "df2",
):
    worktree = create_detached(repo, ref, base_dir=base_dir, prefix=prefix)
    try:
        yield worktree
    finally:
        remove(repo, worktree)
