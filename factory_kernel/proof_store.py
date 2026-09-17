"""Narrow proof object store over a retention directory (SPECIFICATION 3.5, WP05).

Objects are content-addressed and immutable: the same digest with different bytes is a conflict
that refuses, never an overwrite. Objects are partitioned by privacy role (`public`, `private`,
`judge`) so independent judge material and private owner wording never share a listing with
public bounded summaries. An index for a run is written only after every object it references is
retained: a partial write never establishes retention, and an index that names an object the
store does not hold is a defect `verify_inventory` reports, not a proof.

No object address grants proof. Resolving an attestation here returns bytes whose digest was
re-checked; whether the attestation is verified or current is decided by `attestations` and
`proof_dependencies`, never by presence in this store.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .canonical import canonical_bytes, sha256_bytes, sha256_value

ROLES = ("public", "private", "judge")
OBJECTS = "objects"
INDEX = "index"
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_OBJECT = 8_000_000
INDEX_SCHEMA = "dark-factory/proof-index"
INDEX_SCHEMA_VERSION = "1.0"


class ProofStoreRefused(ValueError):
    """The store refused to write or to vouch for an object."""


def _root(root: str | Path) -> Path:
    path = Path(root)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ProofStoreRefused("proof store root has a symlinked ancestor")
    return path


def _role(role: str) -> str:
    if role not in ROLES:
        raise ProofStoreRefused(f"unknown privacy role {role!r}")
    return role


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ProofStoreRefused("object digest must be a sha256")
    return value


def _object_path(root: Path, role: str, digest: str) -> Path:
    return root / OBJECTS / role / digest


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def put_verified_object(root: str | Path, data: bytes, *, role: str = "public") -> str:
    """Retain bytes under their digest. Idempotent for identical bytes; a different object under
    the same digest (or the same object under another role) refuses."""
    base, role = _root(root), _role(role)
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0 or len(data) > MAX_OBJECT:
        raise ProofStoreRefused("object must be non-empty bytes within bound")
    data = bytes(data)
    digest = sha256_bytes(data)
    for other in ROLES:
        if other != role and _object_path(base, other, digest).exists():
            raise ProofStoreRefused(f"object {digest} is already retained under role {other!r}")
    target = _object_path(base, role, digest)
    if target.is_symlink():
        raise ProofStoreRefused("object path is a symlink")
    if target.exists():
        if target.read_bytes() != data:
            raise ProofStoreRefused(f"object conflict: {digest} already holds different bytes")
        return digest
    _write_atomic(target, data)
    return digest


def get_by_digest(root: str | Path, digest: str, *, role: str | None = None) -> bytes | None:
    """The retained bytes, re-hashed on the way out, or None when nothing is retained."""
    base, digest = _root(root), _digest(digest)
    roles = (_role(role),) if role is not None else ROLES
    for candidate in roles:
        path = _object_path(base, candidate, digest)
        if path.is_symlink():
            raise ProofStoreRefused("object path is a symlink")
        if path.is_file():
            data = path.read_bytes()
            if sha256_bytes(data) != digest:
                raise ProofStoreRefused(f"retained object {digest} is corrupted")
            return data
    return None


def role_of(root: str | Path, digest: str) -> str | None:
    base, digest = _root(root), _digest(digest)
    for role in ROLES:
        if _object_path(base, role, digest).is_file():
            return role
    return None


def _entry(root: Path, attestation: Mapping[str, Any]) -> dict:
    if not isinstance(attestation, Mapping) or not isinstance(attestation.get("attestation_id"), str):
        raise ProofStoreRefused("index entries need attestation records with an attestation_id")
    evidence = attestation.get("evidence")
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        raise ProofStoreRefused("attestation evidence must be a list")
    objects = []
    for row in evidence:
        digest = _digest(row.get("sha256") if isinstance(row, Mapping) else None)
        role = role_of(root, digest)
        if role is None:
            raise ProofStoreRefused(f"evidence object {digest} is not retained; an index cannot precede its objects")
        objects.append({"retained_object_id": str(row.get("retained_object_id")), "sha256": digest, "role": role})
    record = canonical_bytes(attestation)
    return {
        "attestation_id": attestation["attestation_id"],
        "claim_key": str(attestation.get("claim_key") or ""),
        "obligation_profile_id": str(attestation.get("obligation_profile_id") or ""),
        "subject_digest": sha256_value(attestation.get("subject")),
        "dependency_digest": sha256_value(attestation.get("inputs")),
        "attestation_object": sha256_bytes(record),
        "evidence": sorted(objects, key=lambda row: row["retained_object_id"]),
    }


def index_run(root: str | Path, run_id: str, attestations: Sequence[Mapping[str, Any]]) -> dict:
    """Retain each attestation as a public object, then write the run's index. Every referenced
    evidence object must already be retained. The index is immutable: an identical rewrite is
    idempotent, a different one refuses."""
    base = _root(root)
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ProofStoreRefused("run_id is malformed")
    if not isinstance(attestations, Sequence) or isinstance(attestations, (str, bytes)) or not attestations:
        raise ProofStoreRefused("an index needs at least one attestation")
    entries = [_entry(base, attestation) for attestation in attestations]
    ids = [entry["attestation_id"] for entry in entries]
    if len(set(ids)) != len(ids):
        raise ProofStoreRefused("an attestation appears twice in one run index")
    for attestation, entry in zip(attestations, entries):
        record = canonical_bytes(attestation)
        stored = put_verified_object(base, record, role="public")
        if stored != entry["attestation_object"]:
            raise ProofStoreRefused("attestation object digest drifted during retention")
    index = {"schema": INDEX_SCHEMA, "schema_version": INDEX_SCHEMA_VERSION, "run_id": run_id,
             "entries": sorted(entries, key=lambda entry: entry["attestation_id"])}
    raw = canonical_bytes(index)
    path = base / INDEX / f"{run_id}.json"
    if path.is_symlink():
        raise ProofStoreRefused("index path is a symlink")
    if path.exists():
        if path.read_bytes() != raw:
            raise ProofStoreRefused(f"index for run {run_id!r} already exists with different content")
        return index
    _write_atomic(path, raw)
    return index


def _indexes(base: Path) -> list[Path]:
    directory = base / INDEX
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix == ".json" and not path.is_symlink())


def _load_index(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProofStoreRefused(f"index {path.name} is unreadable: {exc}") from exc
    if (not isinstance(value, dict) or value.get("schema") != INDEX_SCHEMA
            or value.get("schema_version") != INDEX_SCHEMA_VERSION or not isinstance(value.get("entries"), list)):
        raise ProofStoreRefused(f"index {path.name} is not a proof index")
    return value


def resolve_attestation(root: str | Path, attestation_id: str) -> dict | None:
    """The retained attestation record for an id, re-hashed, or None. Presence is not proof."""
    base = _root(root)
    if not isinstance(attestation_id, str) or not SHA256.fullmatch(attestation_id):
        raise ProofStoreRefused("attestation_id must be a sha256")
    for path in _indexes(base):
        for entry in _load_index(path)["entries"]:
            if isinstance(entry, Mapping) and entry.get("attestation_id") == attestation_id:
                data = get_by_digest(base, _digest(entry.get("attestation_object")), role="public")
                if data is None:
                    raise ProofStoreRefused(f"index names attestation object {entry.get('attestation_object')} that is not retained")
                record = json.loads(data.decode("utf-8"))
                if not isinstance(record, dict) or record.get("attestation_id") != attestation_id:
                    raise ProofStoreRefused("retained attestation object does not carry the indexed attestation_id")
                return record
    return None


def verify_inventory(root: str | Path) -> dict:
    """Rebuild every index from the retained canonical objects and compare byte for byte; hash
    every object. Reports problems; never repairs."""
    base = _root(root)
    problems: list[str] = []
    objects = 0
    for role in ROLES:
        directory = base / OBJECTS / role
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.is_symlink() or path.suffix == ".partial":
                if path.suffix == ".partial":
                    problems.append(f"partial object left behind: {role}/{path.name}")
                continue
            objects += 1
            if not SHA256.fullmatch(path.name) or sha256_bytes(path.read_bytes()) != path.name:
                problems.append(f"object corrupted: {role}/{path.name}")
    indexes = _indexes(base)
    for path in indexes:
        try:
            recorded = _load_index(path)
        except ProofStoreRefused as exc:
            problems.append(str(exc))
            continue
        rebuilt_entries = []
        for entry in recorded["entries"]:
            try:
                data = get_by_digest(base, _digest(entry.get("attestation_object")), role="public")
                if data is None:
                    problems.append(f"{path.name}: attestation object {entry.get('attestation_object')} missing")
                    continue
                rebuilt_entries.append(_entry(base, json.loads(data.decode("utf-8"))))
            except (ProofStoreRefused, ValueError) as exc:
                problems.append(f"{path.name}: {exc}")
        rebuilt = {"schema": INDEX_SCHEMA, "schema_version": INDEX_SCHEMA_VERSION, "run_id": recorded.get("run_id"),
                   "entries": sorted(rebuilt_entries, key=lambda entry: entry["attestation_id"])}
        if canonical_bytes(rebuilt) != path.read_bytes():
            problems.append(f"{path.name}: rebuilt index differs from the recorded index")
    return {"objects": objects, "indexes": len(indexes), "consistent": not problems, "problems": problems}


__all__ = ["ProofStoreRefused", "ROLES", "get_by_digest", "index_run", "put_verified_object", "resolve_attestation",
           "role_of", "verify_inventory"]
