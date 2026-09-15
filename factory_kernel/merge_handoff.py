"""Byte-preserving transport for an already authorised merge between jobs of one run.

The trusted producer's job output authenticates the envelope digest. An artifact name or
an envelope's self-declared identity grants nothing. Existing merge authorities still judge
currency, evidence closure and the actual merged tree.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from .canonical import canonical_bytes

FILES = ("merge-authorization.json", "evidence-bundle.json")
MAX_BYTES = 5_000_000


def export_handoff(artifacts: Path, destination: Path, *, subject: dict) -> str:
    files = {}
    for name in FILES:
        path = artifacts / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError(f"invalid merge handoff input: {name}")
        files[name] = base64.b64encode(path.read_bytes()).decode("ascii")
    raw = canonical_bytes({"version": "1.0", "subject": subject, "files": files})
    if len(raw) > MAX_BYTES:
        raise ValueError("merge handoff exceeds transport limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def import_handoff(source: Path, destination: Path, *, expected_sha256: str,
                   subject: dict) -> None:
    if source.is_symlink() or source.stat().st_size > MAX_BYTES:
        raise ValueError("invalid merge handoff transport")
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("merge handoff differs from the trusted producer's digest")
    envelope = json.loads(raw)
    if (set(envelope) != {"version", "subject", "files"}
            or envelope["version"] != "1.0" or envelope["subject"] != subject
            or set(envelope["files"]) != set(FILES)):
        raise ValueError("merge handoff has a different run, kernel or PR identity")
    decoded = {name: base64.b64decode(envelope["files"][name], validate=True) for name in FILES}
    # Refuse existing destinations: no prior attempt's files can survive a partial import.
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in decoded.items():
        (destination / name).write_bytes(content)
