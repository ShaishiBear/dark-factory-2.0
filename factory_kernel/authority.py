"""Deterministic command authority primitive.

AI output is a proposal. This module records machine-executed evidence for commands that
certify claims such as static gates, RED/GREEN replay, mutation runs, and merge checks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import subprocess
from typing import Mapping, Sequence

from .canonical import sha256_value


@dataclass(frozen=True)
class CommandEvidence:
    authority_id: str
    cwd: str
    argv: tuple[str, ...]
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        return sha256_value(self.to_dict())


def run_command(
    *,
    authority_id: str,
    cwd: str | Path,
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
    timeout: int = 900,
) -> CommandEvidence:
    if not authority_id.strip():
        raise ValueError("authority_id must be non-empty")
    if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
        raise ValueError("argv must contain non-empty strings")
    root = Path(cwd).resolve()
    if not root.is_dir():
        raise ValueError(f"authority cwd does not exist: {root}")
    proc = subprocess.run(
        list(argv),
        cwd=root,
        capture_output=True,
        text=False,
        timeout=timeout,
        env=dict(env) if env is not None else None,
    )
    return CommandEvidence(
        authority_id=authority_id,
        cwd=str(root),
        argv=tuple(argv),
        exit_code=proc.returncode,
        stdout_sha256=hashlib.sha256(proc.stdout or b"").hexdigest(),
        stderr_sha256=hashlib.sha256(proc.stderr or b"").hexdigest(),
    )
