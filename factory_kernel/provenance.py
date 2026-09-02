"""Durable exact-commit provenance handoff between builder and fresh validator.

Builder artifacts must survive ephemeral GitHub runners without entering the product tree.
A canonical pack is therefore attached to the exact PR-head Git object through a dedicated
Git notes ref. The fresh validator re-hashes every embedded artifact before using it.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Mapping

from .canonical import canonical_bytes, sha256_value

NOTE_REF = "refs/notes/dark-factory-provenance"
GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

# Canonical builder-side claims that cannot be reconstructed after an ephemeral build runner exits.
# Architecture policy is copied from the exact checked-out trust root; the rest live in ARTIFACTS_DIR.
BUILDER_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("contract", "task-contract.json"),
    ("tickets", "ticket.json"),
    ("frontier", "frontier.json"),
    ("context", "context.json"),
    ("architecture-policy", ".factory/architecture.json"),
    ("design", "design.json"),
    ("architecture-governor", "architecture-governor.json"),
    ("test-plan", "test-plan.json"),
    ("red-proof", "red-proof.json"),
    ("green-proof", "final-green-proof.json"),
    ("impact", "final-green-proof.impact.json"),
    ("architecture-drift", "final-green-proof.architecture.json"),
    ("architecture-conformance", "architecture-conformance.json"),
)
BUILDER_CLAIMS = tuple(claim_id for claim_id, _path in BUILDER_ARTIFACTS)


def _json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read builder provenance artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"builder provenance artifact must be a JSON object: {path}")
    return value


def _oid(value: str, name: str) -> str:
    if not GIT_OID.fullmatch(value):
        raise ValueError(f"{name} must be a full Git object id")
    return value


def build_pack(
    *,
    artifact_root: str | Path,
    repo_root: str | Path,
    issue: int,
    base_sha: str,
    head_sha: str,
) -> dict:
    """Build the canonical builder handoff from already-validated artifacts."""
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise ValueError("builder provenance issue must be positive")
    base = _oid(base_sha, "base_sha")
    head = _oid(head_sha, "head_sha")
    artifacts = Path(artifact_root).resolve()
    repo = Path(repo_root).resolve()
    if not artifacts.is_dir() or not repo.is_dir():
        raise ValueError("builder provenance roots must exist")

    records: dict[str, dict] = {}
    for claim_id, rel in BUILDER_ARTIFACTS:
        source = repo / rel if claim_id == "architecture-policy" else artifacts / rel
        value = _json_object(source)
        records[claim_id] = {
            "source": rel,
            "sha256": sha256_value(value),
            "content": value,
        }

    contract = records["contract"]["content"]
    contract_issue = contract.get("issue") if isinstance(contract, dict) else None
    if not isinstance(contract_issue, dict) or contract_issue.get("number") != issue:
        raise ValueError("builder provenance contract does not match issue")
    contract_hash = records["contract"]["sha256"]
    ticket = records["tickets"]["content"]
    if ticket.get("issue") != issue or ticket.get("contract_sha256") != contract_hash:
        raise ValueError("builder provenance ticket is not bound to contract/issue")
    frontier = records["frontier"]["content"]
    if frontier.get("issue") != issue or frontier.get("ticket_sha256") != records["tickets"]["sha256"]:
        raise ValueError("builder provenance frontier is not bound to ticket/issue")
    if frontier.get("ready") is not True:
        raise ValueError("builder provenance frontier did not authorize work")
    context = records["context"]["content"]
    if context.get("contract_sha256") != contract_hash:
        raise ValueError("builder provenance context is not bound to contract")
    design = records["design"]["content"]
    if design.get("contract_sha256") != contract_hash or design.get("context_sha256") != records["context"]["sha256"]:
        raise ValueError("builder provenance design is not bound to contract/context")

    return {
        "version": "1.0",
        "issue": issue,
        "base_sha": base,
        "head_sha": head,
        "note_ref": NOTE_REF,
        "artifacts": records,
    }


def verify_pack(
    value: object,
    *,
    expected_head_sha: str | None = None,
    expected_base_sha: str | None = None,
    expected_issue: int | None = None,
) -> dict:
    if not isinstance(value, dict) or value.get("version") != "1.0":
        raise ValueError("builder provenance pack must be version 1.0")
    if value.get("note_ref") != NOTE_REF:
        raise ValueError("builder provenance note ref is invalid")
    issue = value.get("issue")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise ValueError("builder provenance issue is invalid")
    base = _oid(str(value.get("base_sha") or ""), "base_sha")
    head = _oid(str(value.get("head_sha") or ""), "head_sha")
    if expected_head_sha is not None and head != expected_head_sha:
        raise ValueError("builder provenance is attached to a different PR head")
    if expected_base_sha is not None and base != expected_base_sha:
        raise ValueError("builder provenance was built from a different base")
    if expected_issue is not None and issue != expected_issue:
        raise ValueError("builder provenance belongs to a different issue")

    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(BUILDER_CLAIMS):
        raise ValueError("builder provenance does not contain the complete canonical builder claim set")
    for claim_id in BUILDER_CLAIMS:
        record = artifacts.get(claim_id)
        if not isinstance(record, dict):
            raise ValueError(f"builder provenance claim is invalid: {claim_id}")
        content = record.get("content")
        if not isinstance(content, dict):
            raise ValueError(f"builder provenance claim content is invalid: {claim_id}")
        expected = str(record.get("sha256") or "")
        if expected != sha256_value(content):
            raise ValueError(f"builder provenance claim hash mismatch: {claim_id}")
        source = record.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"builder provenance source is invalid: {claim_id}")

    # Re-run the cheap cross-artifact binding checks on untrusted note bytes.
    contract = artifacts["contract"]["content"]
    contract_issue = contract.get("issue") if isinstance(contract, dict) else None
    if not isinstance(contract_issue, dict) or contract_issue.get("number") != issue:
        raise ValueError("builder provenance contract issue mismatch")
    contract_hash = artifacts["contract"]["sha256"]
    ticket = artifacts["tickets"]["content"]
    if ticket.get("issue") != issue or ticket.get("contract_sha256") != contract_hash:
        raise ValueError("builder provenance ticket binding mismatch")
    frontier = artifacts["frontier"]["content"]
    if frontier.get("issue") != issue or frontier.get("ticket_sha256") != artifacts["tickets"]["sha256"] or frontier.get("ready") is not True:
        raise ValueError("builder provenance frontier binding mismatch")
    context = artifacts["context"]["content"]
    if context.get("contract_sha256") != contract_hash:
        raise ValueError("builder provenance context binding mismatch")
    design = artifacts["design"]["content"]
    if design.get("contract_sha256") != contract_hash or design.get("context_sha256") != artifacts["context"]["sha256"]:
        raise ValueError("builder provenance design binding mismatch")
    return value


def pack_sha256(pack: Mapping[str, object]) -> str:
    return sha256_value(pack)


def materialize(pack: Mapping[str, object], output_root: str | Path) -> dict[str, Path]:
    """Materialize note-contained JSON into a validator-owned evidence directory."""
    verified = verify_pack(dict(pack))
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifacts = verified["artifacts"]
    result: dict[str, Path] = {}
    for claim_id in BUILDER_CLAIMS:
        record = artifacts[claim_id]
        target = root / f"{claim_id}.json"
        target.write_bytes(canonical_bytes(record["content"]))
        if sha256_value(record["content"]) != record["sha256"]:
            raise ValueError(f"materialized builder provenance changed: {claim_id}")
        result[claim_id] = target
    return result
