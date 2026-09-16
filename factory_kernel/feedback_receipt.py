"""Kernel refusal receipts. Reporting a refusal never establishes its strategic cause."""
from __future__ import annotations

from .canonical import sha256_bytes, sha256_value
from .evidence_retention import _json_bytes, _path
from .programme_runtime import ProgrammeQueue
from .trajectory import oid, positive

MARKER = "<!-- dark-factory-feedback:"
FILE = "factory-feedback.json"


def refusal_receipt(github, *, default_branch, issue_number, refusal, artifacts,
                    kernel_revision, environment):
    """Called by the protected validator after it writes the actual refusal.

    No imported historical files or candidate-supplied programme description can mint this
    receipt. Failure to establish these bindings leaves the ordinary refusal unchanged.
    """
    if (environment.get("GITHUB_REPOSITORY") != github.repository
            or environment.get("GITHUB_REF") != "refs/heads/main"
            or environment.get("GITHUB_SHA") != kernel_revision):
        raise ValueError("feedback requires the canonical workflow's exact checkout")
    admission = ProgrammeQueue(github, default_branch).admit(github.issue(positive(issue_number)))
    if admission is None or admission[0].strategy is None:
        raise ValueError("feedback requires an admitted strategy programme")
    programme, item = admission
    raw = _json_bytes(_path(artifacts, "validation-refusal.json"))
    from .programme import parse_json
    if parse_json(raw.decode("utf-8")) != refusal:
        raise ValueError("refusal bytes changed before receipt")
    return {"schema": "dark-factory/factory-feedback", "schema_version": "1.0",
        "repository": github.repository, "run_id": positive(int(environment["GITHUB_RUN_ID"])),
        "run_attempt": positive(int(environment["GITHUB_RUN_ATTEMPT"])),
        "kernel_revision": oid(kernel_revision), "programme_sha256": programme.sha256,
        "item_id": item["id"], "issue": issue_number, "pr": positive(refusal["pr"]),
        "head_sha": oid(refusal["head"]), "base_sha": oid(refusal["base"]),
        "outcome": "validation-refused", "refusal_sha256": sha256_bytes(raw),
        "cause": "unresolved", "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}


def marker(receipt):
    return f"{MARKER}{sha256_value(receipt)} -->"
