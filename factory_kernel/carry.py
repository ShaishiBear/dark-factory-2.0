"""Certified upstream artifacts, carried from one build of an issue to the next.

Fourteen builds of issue #103 re-derived the same investigate/contract/context/architecture
work from scratch after a downstream stage failed, on an unchanged issue at an unchanged base:
roughly $64 of the $106.16 the archived trajectory corpus measured over two days. Everything
upstream of `test_author` had already been written and had already passed its deterministic
gate when the build died.

A carry is that upstream, bound to the exact conditions that make it valid. It is written by
the kernel once a build passes the architecture gate, stored as a Git note on a ref of its own
(`refs/notes/dark-factory-carry`), and read back at the start of the next build of the same
issue. It is reused only when every condition below holds, each checked separately so a miss
can say which one failed:

- the carry is for this issue;
- its `base_sha` is the commit this build is cutting from;
- the issue's title and body still hash to what the carry recorded;
- the kernel commit that wrote it is an ancestor of the kernel commit running now;
- nothing in the trust root that shapes these artifacts changed (`policy_sha256`);
- every recorded artifact still hashes to its recorded sha256.

Reuse skips the four *model* stages and nothing else. The deterministic authorities are re-run
over the restored artifacts exactly as a fresh build runs them; they are seconds of CPU, and
they are what makes a restored artifact trustworthy. On any refusal the carry is discarded and
the build runs in full (D-071).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes, sha256_bytes

CARRY_NOTE_REF = "refs/notes/dark-factory-carry"
CARRY_VERSION = "1.0"
# The kernel writes this beside the restored artifacts so the provenance pack, and through it
# the evidence spine, records that these artifacts came from a carry and from which run.
CARRY_IDENTITY_ARTIFACT = "carry.json"
GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

# What a build that reaches the architecture gate has already written and already had judged.
# `issue.json` is the hash basis and is carried as evidence; it and `issue-frontier.json` are
# never restored, because the kernel has just written its own fresh snapshot of both and the
# ticket compiler must judge the frontier as GitHub reports it now.
CARRY_ARTIFACTS: tuple[str, ...] = (
    "issue.json",
    "issue-frontier.json",
    "ticket.json",
    "frontier.json",
    "task-contract.raw.json",
    "task-contract.json",
    "context.raw.json",
    "context.enriched.json",
    "context.json",
    "design.raw.json",
    "design.json",
    "architecture-governor.raw.json",
    "architecture-governor.json",
)
# Written only for a bug issue (`investigate` plus `_observe_repro`). Carried when present.
OPTIONAL_CARRY_ARTIFACTS: tuple[str, ...] = ("repro-observed.json", "repro-deferred.json")
# The kernel's own fresh snapshot wins over the carried copy.
KERNEL_FRESH_ARTIFACTS: tuple[str, ...] = ("issue.json", "issue-frontier.json")
# Run state, never carried: a lease belongs to the run that holds it.
NEVER_CARRIED: tuple[str, ...] = ("factory-lease.json",)

# The compiled artifacts a restored gate must reproduce byte for byte. At an identical base
# with an identical policy the deterministic compilers are functions of their inputs, so a
# difference means something the conditions above did not catch moved; the carry is dropped
# and the build runs in full rather than proceeding on an artifact nobody can explain.
RECOMPILED_ARTIFACTS: tuple[str, ...] = (
    "task-contract.json",
    "context.enriched.json",
    "context.json",
    "ticket.json",
    "frontier.json",
    "design.json",
    "architecture-governor.json",
)

# The trust root whose contents shape the carried artifacts. Any change to any of it refuses
# the carry: a moved prompt, a moved gate or a moved policy would have produced other work.
CARRY_POLICY_PATHS: tuple[str, ...] = (
    ".factory",
    ".github",
    "CLAUDE.md",
    "FACTORY_RULES.md",
    "MISSION.md",
    "factory_kernel",
    "harness",
    "scripts",
    "tests/factory",
)

# Every reason a carry can be missed, each raised at exactly one place.
REASONS: tuple[str, ...] = (
    "absent",
    "read_failed",
    "malformed",
    "different_issue",
    "base_moved",
    "issue_changed",
    "kernel_not_ancestor",
    "policy_changed",
    "artifact_hash_mismatch",
    "restore_failed",
    "gate_refused",
    "recompiled_mismatch",
)


class CarryMiss(RuntimeError):
    """No reuse, for exactly one stated reason. Never fails a build."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown carry miss reason: {reason!r}")
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def issue_digest(issue: Mapping[str, Any]) -> str:
    """The hash of what the build is *about*: the issue's number, title and body.

    Not the whole `issue.json`. Every retry of an issue posts a validation-failure comment on
    it, which moves `updatedAt`, and the factory's own claim moves `labels`; hashing the whole
    snapshot would mean no carry ever matched its own issue. Editing the title or the body is
    the event that invalidates upstream work, and that is what this covers.
    """
    return sha256_bytes(
        canonical_bytes(
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "body": issue.get("body"),
            }
        )
    )


def carry_key_content(issue: int) -> bytes:
    """The bytes of the blob a carry note hangs on: one deterministic object per issue.

    Git notes attach to an object, and the carry is keyed by issue number, so the key is a
    blob the kernel writes with `git hash-object -w`. It carries no issue content: it is an
    address, and the whole payload lives in the note. This is why the carry needs no new ref
    machinery -- `git notes add -f`, `show`, `remove`, `fetch` and `push` already do all of it.
    """
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise ValueError("carry issue must be a positive integer")
    return f"dark-factory-carry:issue:{issue}\n".encode()


def policy_digest(ls_tree_output: str) -> str:
    """The digest of the trust root as `git ls-tree -r <commit> -- <CARRY_POLICY_PATHS>` sees it.

    The caller runs the git command (this module holds no subprocesses); the lines carry a mode,
    a type, an object id and a path each, so any content change under any policy path changes
    the digest.
    """
    return sha256_bytes(ls_tree_output.encode("utf-8"))


def _oid(value: str, name: str) -> str:
    if not GIT_OID.fullmatch(value or ""):
        raise ValueError(f"carry {name} must be a full Git object id")
    return value


def _record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    text = data.decode("utf-8")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"carry artifact must be a JSON object: {path.name}")
    return {"sha256": sha256_bytes(data), "text": text}


def build_carry(
    *,
    artifact_root: str | Path,
    issue: int,
    base_sha: str,
    issue_sha256: str,
    kernel_commit: str,
    policy_sha256: str,
    run_id: str,
    written_at: str,
) -> dict[str, Any]:
    """The carry a build writes once its architecture gate has returned `proceed`."""
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise ValueError("carry issue must be a positive integer")
    root = Path(artifact_root).resolve()
    if not root.is_dir():
        raise ValueError("carry artifact root does not exist")
    for name in NEVER_CARRIED:
        if name in CARRY_ARTIFACTS or name in OPTIONAL_CARRY_ARTIFACTS:
            raise ValueError(f"run state must never be carried: {name}")

    artifacts: dict[str, dict[str, Any]] = {}
    for rel in CARRY_ARTIFACTS:
        source = root / rel
        if not source.is_file():
            raise ValueError(f"carry artifact is missing: {rel}")
        artifacts[rel] = _record(source)
    for rel in OPTIONAL_CARRY_ARTIFACTS:
        source = root / rel
        if source.is_file():
            artifacts[rel] = _record(source)

    carried_issue = json.loads(artifacts["issue.json"]["text"])
    if issue_digest(carried_issue) != issue_sha256:
        raise ValueError("carry issue digest does not match the snapshot being carried")
    if carried_issue.get("number") != issue:
        raise ValueError("carry issue snapshot is for another issue")

    return {
        "version": CARRY_VERSION,
        "note_ref": CARRY_NOTE_REF,
        "issue": issue,
        "base_sha": _oid(base_sha, "base_sha"),
        "issue_sha256": issue_sha256,
        "kernel_commit": _oid(kernel_commit, "kernel_commit"),
        "policy_sha256": policy_sha256,
        "run_id": run_id,
        "written_at": written_at,
        "artifacts": artifacts,
    }


def carry_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """The binding a carry declares, before its contents are trusted."""
    if not isinstance(value, Mapping) or value.get("version") != CARRY_VERSION:
        raise ValueError(f"carry must be version {CARRY_VERSION}")
    if value.get("note_ref") != CARRY_NOTE_REF:
        raise ValueError("carry note ref is invalid")
    issue = value.get("issue")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise ValueError("carry issue is invalid")
    return {
        "issue": issue,
        "base_sha": _oid(str(value.get("base_sha") or ""), "base_sha"),
        "kernel_commit": _oid(str(value.get("kernel_commit") or ""), "kernel_commit"),
        "issue_sha256": str(value.get("issue_sha256") or ""),
        "policy_sha256": str(value.get("policy_sha256") or ""),
        "run_id": str(value.get("run_id") or ""),
        "written_at": str(value.get("written_at") or ""),
    }


def verify_carry(
    value: object,
    *,
    expected_issue: int,
    expected_base_sha: str,
    expected_issue_sha256: str,
    kernel_commit: str,
    expected_policy_sha256: str,
    is_ancestor: Callable[[str, str], bool],
) -> dict[str, Any]:
    """Hold a carry to every condition, one refusal reason each. Raises `CarryMiss`."""
    try:
        identity = carry_identity(value if isinstance(value, Mapping) else {})
    except ValueError as exc:
        raise CarryMiss("malformed", str(exc)) from exc
    assert isinstance(value, Mapping)

    if identity["issue"] != expected_issue:
        raise CarryMiss(
            "different_issue", f"carry is for #{identity['issue']}, not #{expected_issue}"
        )
    if identity["base_sha"] != expected_base_sha:
        raise CarryMiss(
            "base_moved",
            f"carry was cut from {identity['base_sha'][:7]}, this build from "
            f"{expected_base_sha[:7]}",
        )
    if identity["issue_sha256"] != expected_issue_sha256:
        raise CarryMiss("issue_changed", "the issue title or body has been edited")
    if identity["kernel_commit"] != kernel_commit and not is_ancestor(
        identity["kernel_commit"], kernel_commit
    ):
        raise CarryMiss(
            "kernel_not_ancestor",
            f"carry was written by kernel {identity['kernel_commit'][:7]}, which is not an "
            f"ancestor of {kernel_commit[:7]}",
        )
    if identity["policy_sha256"] != expected_policy_sha256:
        raise CarryMiss("policy_changed", "a prompt, gate or .factory policy changed")

    artifacts = value.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise CarryMiss("malformed", "carry artifacts must be an object")
    missing = [rel for rel in CARRY_ARTIFACTS if rel not in artifacts]
    if missing:
        raise CarryMiss("malformed", "carry is missing artifacts: " + ", ".join(missing))
    unknown = [
        rel for rel in artifacts if rel not in CARRY_ARTIFACTS + OPTIONAL_CARRY_ARTIFACTS
    ]
    if unknown:
        raise CarryMiss("malformed", "carry carries unknown artifacts: " + ", ".join(sorted(unknown)))

    for rel in sorted(artifacts):
        record = artifacts[rel]
        if not isinstance(record, Mapping):
            raise CarryMiss("malformed", f"carry artifact record is invalid: {rel}")
        text = record.get("text")
        expected = record.get("sha256")
        if not isinstance(text, str) or not isinstance(expected, str):
            raise CarryMiss("malformed", f"carry artifact record is invalid: {rel}")
        if sha256_bytes(text.encode("utf-8")) != expected:
            raise CarryMiss("artifact_hash_mismatch", f"carry artifact hash mismatch: {rel}")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CarryMiss("malformed", f"carry artifact is not JSON: {rel}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise CarryMiss("malformed", f"carry artifact is not an object: {rel}")

    carried_issue = json.loads(artifacts["issue.json"]["text"])
    if issue_digest(carried_issue) != expected_issue_sha256:
        raise CarryMiss("issue_changed", "the carried issue snapshot is not this issue")
    return dict(value)


def restore(carry: Mapping[str, Any], artifact_root: str | Path) -> tuple[str, ...]:
    """Write the carried artifacts into the run's artifacts directory, verified on the way out.

    `issue.json` and `issue-frontier.json` are not written: the kernel already snapshotted both
    with its own GitHub authority at the top of this build, and the frontier the ticket compiler
    judges must be the one GitHub reports now.
    """
    artifacts = carry["artifacts"]
    root = Path(artifact_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for rel in sorted(artifacts):
        if rel in KERNEL_FRESH_ARTIFACTS:
            continue
        record = artifacts[rel]
        data = str(record["text"]).encode("utf-8")
        if sha256_bytes(data) != record["sha256"]:
            raise ValueError(f"carry artifact hash mismatch on restore: {rel}")
        target = root / rel
        target.write_bytes(data)
        if sha256_bytes(target.read_bytes()) != record["sha256"]:
            raise ValueError(f"restored carry artifact changed on disk: {rel}")
        written.append(rel)
    return tuple(written)


def recompiled_mismatches(
    carry: Mapping[str, Any], artifact_root: str | Path
) -> tuple[str, ...]:
    """Which compiled artifacts the re-run gates did not reproduce byte for byte."""
    artifacts = carry["artifacts"]
    root = Path(artifact_root).resolve()
    drift: list[str] = []
    for rel in RECOMPILED_ARTIFACTS:
        record = artifacts.get(rel)
        target = root / rel
        if not isinstance(record, Mapping) or not target.is_file():
            drift.append(rel)
            continue
        if sha256_bytes(target.read_bytes()) != record["sha256"]:
            drift.append(rel)
    return tuple(drift)


def identity_record(
    carry: Mapping[str, Any],
    *,
    reused_at: str,
    age_minutes: int,
    skipped_roles: tuple[str, ...],
) -> dict[str, Any]:
    """`carry.json`: what the run's artifacts say about where its upstream came from."""
    identity = carry_identity(carry)
    artifacts = carry["artifacts"]
    return {
        "version": CARRY_VERSION,
        "note_ref": CARRY_NOTE_REF,
        "issue": identity["issue"],
        "base_sha": identity["base_sha"],
        "issue_sha256": identity["issue_sha256"],
        "kernel_commit": identity["kernel_commit"],
        "policy_sha256": identity["policy_sha256"],
        "source_run_id": identity["run_id"],
        "written_at": identity["written_at"],
        "reused_at": reused_at,
        "age_minutes": age_minutes,
        "skipped_roles": list(skipped_roles),
        "artifacts": {rel: artifacts[rel]["sha256"] for rel in sorted(artifacts)},
    }


def hit_line(*, issue: int, base_sha: str, skipped_roles: tuple[str, ...], age_minutes: int) -> str:
    return (
        f"FACTORY_CARRY_HIT issue=#{issue} base={base_sha[:7]} "
        f"stages={','.join(skipped_roles)} age={age_minutes}"
    )


def miss_line(*, issue: int, reason: str) -> str:
    if reason not in REASONS:
        raise ValueError(f"unknown carry miss reason: {reason!r}")
    return f"FACTORY_CARRY_MISS issue=#{issue} reason={reason}"


STAMP = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> str:
    """The stamp a carry is written with, in the format the kernel's records already use."""
    return time.strftime(STAMP, time.gmtime())


def age_minutes(written_at: str, now: str) -> int:
    """Whole minutes between two `utc_now()` stamps; 0 when either cannot be read."""
    from datetime import datetime

    def parse(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, STAMP)
        except (TypeError, ValueError):
            return None

    start, end = parse(written_at), parse(now)
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() // 60))


def carry_bytes(carry: Mapping[str, Any]) -> bytes:
    return canonical_bytes(carry)


def carry_sha256(carry: Mapping[str, Any]) -> str:
    return hashlib.sha256(carry_bytes(carry)).hexdigest()
