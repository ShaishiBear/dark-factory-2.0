"""A retry reuses the upstream work it already certified (D-071).

The archived trajectory corpus measured $106.16 of model spend across 161 stages in two days,
of which investigate $21.63 + context $21.26 + contract $10.73 + architecture $10.10 was spent
re-deriving the same upstream fourteen times for one issue (#103), on an unchanged issue at an
unchanged base, because every build after a downstream failure started again from nothing.

These tests pin the carry: it is written once a build passes the architecture gate and never
before; a hit skips exactly the four model stages and re-runs every deterministic authority
over the restored artifacts; each invalidation condition misses for its own stated reason; a
tampered artifact, a refused restored gate or a compiled artifact the gates do not reproduce
falls back to a full build; the hit and miss lines and the `FACTORY_STAGE ... name=carry` row
are emitted; and the carry lives on a Git notes ref no worker can reach.
"""

from __future__ import annotations

import contextlib
import dataclasses
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_factory_red_evidence_and_stop import FakeGitHub, FakeWorktree  # noqa: E402

from factory_kernel import carry as carry_mod  # noqa: E402
from factory_kernel.canonical import canonical_bytes, sha256_bytes  # noqa: E402
from factory_kernel.carry import (  # noqa: E402
    CARRY_ARTIFACTS,
    CARRY_IDENTITY_ARTIFACT,
    CARRY_NOTE_REF,
    CARRY_POLICY_PATHS,
    KERNEL_FRESH_ARTIFACTS,
    NEVER_CARRIED,
    OPTIONAL_CARRY_ARTIFACTS,
    REASONS,
    RECOMPILED_ARTIFACTS,
    CarryMiss,
    age_minutes,
    build_carry,
    carry_key_content,
    identity_record,
    issue_digest,
    miss_line,
    policy_digest,
    recompiled_mismatches,
    restore,
    verify_carry,
)
from factory_kernel.config import load_config  # noqa: E402
from factory_kernel.provenance import build_pack, verify_pack  # noqa: E402
from factory_kernel.providers import path_rules  # noqa: E402
from factory_kernel.refusal import ToolRefused  # noqa: E402
from factory_kernel.runtime import (  # noqa: E402
    STAGE_TIMINGS,
    KernelRuntime,
    NeedsHuman,
    RunPaths,
)
from factory_kernel.worker_policy import (  # noqa: E402
    ROLE_PATH_SCOPE,
    ROLE_TOOLS,
    TRUST_ROOT_DENY_PATHS,
    allowed_tools,
    path_scope,
)

BASE = "b" * 40
KERNEL = "1" * 40
POLICY = "5" * 64
ISSUE = {
    "number": 103,
    "title": "the streaming hook drops the sources event",
    "body": "please fix",
    "labels": [{"name": "factory:accepted"}],
    "state": "OPEN",
    "updatedAt": "2026-09-06T00:00:00Z",
}


def jbytes(value: object) -> bytes:
    return canonical_bytes(value)


def write_upstream(artifacts: Path, *, issue: dict = ISSUE, bug: bool = False) -> None:
    """Everything a build has on disk when its architecture gate returns `proceed`."""
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "issue.json").write_bytes(jbytes(issue))
    (artifacts / "issue-frontier.json").write_bytes(
        jbytes({"version": "1.0", "issue": issue, "blockers": [], "fetched_at": "now"})
    )
    (artifacts / "task-contract.raw.json").write_bytes(jbytes({"raw": "contract"}))
    (artifacts / "context.raw.json").write_bytes(jbytes({"raw": "context"}))
    (artifacts / "design.raw.json").write_bytes(jbytes({"raw": "design"}))
    (artifacts / "architecture-governor.raw.json").write_bytes(jbytes({"raw": "governor"}))
    compile_gates(artifacts, issue_number=int(issue["number"]))
    if bug:
        (artifacts / "repro-observed.json").write_bytes(
            jbytes({"mode": "deferred", "expected_symptom": "sources event missing"})
        )
        (artifacts / "repro-deferred.json").write_bytes(jbytes({"reason": "no runner fails"}))


# A deterministic stand-in for each of the three compilers: a pure function of the raw
# artifacts the model stage before it wrote, which is what makes a restored artifact
# reproducible at an unchanged base.


def load(artifacts: Path, rel: str) -> dict:
    return json.loads((artifacts / rel).read_text(encoding="utf-8"))


def compile_contract(artifacts: Path, *, issue_number: int, salt: str = "") -> None:
    raw = load(artifacts, "task-contract.raw.json")
    contract = {"version": "2.0", "issue": {"number": issue_number}, **raw}
    (artifacts / "task-contract.json").write_bytes(jbytes(contract))
    (artifacts / "task-contract.sha256").write_bytes(
        (sha256_bytes(jbytes(contract)) + "\n").encode()
    )


def compile_context(artifacts: Path, *, issue_number: int, salt: str = "") -> None:
    contract_sha = sha256_bytes((artifacts / "task-contract.json").read_bytes())
    enriched = {**load(artifacts, "context.raw.json"), "derived": ["app/x.ts"]}
    (artifacts / "context.enriched.json").write_bytes(jbytes(enriched))
    (artifacts / "context.json").write_bytes(
        jbytes({**enriched, "contract_sha256": contract_sha, "salt": salt})
    )
    ticket = {"version": "1.0", "issue": issue_number, "contract_sha256": contract_sha}
    (artifacts / "ticket.json").write_bytes(jbytes(ticket))
    (artifacts / "frontier.json").write_bytes(
        jbytes({"version": "1.0", "issue": issue_number, "ready": True,
                "ticket_sha256": sha256_bytes(jbytes(ticket))})
    )
    (artifacts / "design.json").write_bytes(
        jbytes({**load(artifacts, "design.raw.json"), "contract_sha256": contract_sha})
    )


def compile_architecture(artifacts: Path, *, issue_number: int, salt: str = "") -> None:
    (artifacts / "architecture-governor.json").write_bytes(
        jbytes({**load(artifacts, "architecture-governor.raw.json"),
                "version": "1.0", "decision": "proceed"})
    )


COMPILERS = {
    "contract-gate": compile_contract,
    "context-gate": compile_context,
    "architecture-gate": compile_architecture,
}


def compile_gates(artifacts: Path, *, issue_number: int, salt: str = "") -> None:
    for compiler in COMPILERS.values():
        compiler(artifacts, issue_number=issue_number, salt=salt)


def a_carry(artifacts: Path, **overrides) -> dict:
    fields = {
        "artifact_root": artifacts,
        "issue": int(ISSUE["number"]),
        "base_sha": BASE,
        "issue_sha256": issue_digest(ISSUE),
        "kernel_commit": KERNEL,
        "policy_sha256": POLICY,
        "run_id": "issue-103-a1-deadbeef01",
        "written_at": "2026-09-06T12:00:00Z",
    }
    fields.update(overrides)
    return build_carry(**fields)


def verified(carry: dict, **overrides) -> dict:
    fields = {
        "expected_issue": int(ISSUE["number"]),
        "expected_base_sha": BASE,
        "expected_issue_sha256": issue_digest(ISSUE),
        "kernel_commit": KERNEL,
        "expected_policy_sha256": POLICY,
        "is_ancestor": lambda a, b: True,
    }
    fields.update(overrides)
    return verify_carry(carry, **fields)


# --- what a carry is, and what invalidates it -------------------------------------------------


class CarryShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-carry-")
        self.addCleanup(self.tmp.cleanup)
        self.artifacts = Path(self.tmp.name) / "artifacts"
        write_upstream(self.artifacts)

    def test_the_carry_records_the_bindings_that_make_it_valid(self):
        carry = a_carry(self.artifacts)
        self.assertEqual(carry["issue"], 103)
        self.assertEqual(carry["base_sha"], BASE)
        self.assertEqual(carry["issue_sha256"], issue_digest(ISSUE))
        self.assertEqual(carry["kernel_commit"], KERNEL)
        self.assertEqual(carry["policy_sha256"], POLICY)
        self.assertEqual(carry["note_ref"], CARRY_NOTE_REF)
        self.assertEqual(carry["run_id"], "issue-103-a1-deadbeef01")

    def test_every_carried_artifact_carries_its_own_sha256(self):
        carry = a_carry(self.artifacts)
        self.assertEqual(set(carry["artifacts"]), set(CARRY_ARTIFACTS))
        for rel, record in carry["artifacts"].items():
            with self.subTest(rel):
                self.assertEqual(
                    record["sha256"], sha256_bytes((self.artifacts / rel).read_bytes())
                )

    def test_a_bug_issues_repro_records_are_carried_too(self):
        write_upstream(self.artifacts, bug=True)
        carry = a_carry(self.artifacts)
        for rel in OPTIONAL_CARRY_ARTIFACTS:
            self.assertIn(rel, carry["artifacts"])

    def test_run_state_is_never_carried(self):
        self.assertIn("factory-lease.json", NEVER_CARRIED)
        for rel in NEVER_CARRIED:
            self.assertNotIn(rel, CARRY_ARTIFACTS)
            self.assertNotIn(rel, OPTIONAL_CARRY_ARTIFACTS)

    def test_the_issue_digest_is_the_title_and_body_not_the_whole_snapshot(self):
        """A retry comments on its issue and claims it, which moves `updatedAt` and `labels`;
        hashing the whole snapshot would mean no carry ever matched its own issue."""
        moved = {**ISSUE, "updatedAt": "2026-09-06T09:99:99Z",
                 "labels": [{"name": "factory:accepted"}, {"name": "factory:in-progress"}]}
        self.assertEqual(issue_digest(moved), issue_digest(ISSUE))
        self.assertNotEqual(issue_digest({**ISSUE, "body": "other"}), issue_digest(ISSUE))
        self.assertNotEqual(issue_digest({**ISSUE, "title": "other"}), issue_digest(ISSUE))

    def test_a_missing_upstream_artifact_cannot_be_carried(self):
        (self.artifacts / "design.json").unlink()
        with self.assertRaisesRegex(ValueError, "carry artifact is missing: design.json"):
            a_carry(self.artifacts)

    def test_the_carry_key_is_deterministic_per_issue(self):
        self.assertEqual(carry_key_content(103), b"dark-factory-carry:issue:103\n")
        self.assertNotEqual(carry_key_content(103), carry_key_content(104))
        with self.assertRaises(ValueError):
            carry_key_content(0)

    def test_the_policy_digest_moves_with_the_trust_root(self):
        self.assertNotEqual(policy_digest("100644 blob aaa\tx"), policy_digest("100644 blob bbb\tx"))
        for path in (".factory", "factory_kernel", "scripts", "harness", "FACTORY_RULES.md"):
            self.assertIn(path, CARRY_POLICY_PATHS)


class CarryInvalidationTests(unittest.TestCase):
    """Every condition refused separately, each by its own reason."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-carry-verify-")
        self.addCleanup(self.tmp.cleanup)
        self.artifacts = Path(self.tmp.name) / "artifacts"
        write_upstream(self.artifacts)
        self.carry = a_carry(self.artifacts)

    def miss(self, **overrides) -> CarryMiss:
        with self.assertRaises(CarryMiss) as ctx:
            verified(self.carry, **overrides)
        return ctx.exception

    def test_an_unchanged_issue_at_an_unchanged_base_verifies(self):
        self.assertEqual(verified(self.carry)["issue"], 103)

    def test_another_issue_is_refused(self):
        self.assertEqual(self.miss(expected_issue=104).reason, "different_issue")

    def test_a_moved_base_is_refused(self):
        exc = self.miss(expected_base_sha="c" * 40)
        self.assertEqual(exc.reason, "base_moved")
        self.assertIn(BASE[:7], exc.detail)

    def test_an_edited_issue_is_refused(self):
        exc = self.miss(expected_issue_sha256=issue_digest({**ISSUE, "body": "edited"}))
        self.assertEqual(exc.reason, "issue_changed")

    def test_a_kernel_that_is_not_a_descendant_is_refused(self):
        exc = self.miss(kernel_commit="d" * 40, is_ancestor=lambda a, b: False)
        self.assertEqual(exc.reason, "kernel_not_ancestor")

    def test_a_descendant_kernel_is_accepted(self):
        self.assertEqual(
            verified(self.carry, kernel_commit="d" * 40, is_ancestor=lambda a, b: True)["issue"],
            103,
        )

    def test_a_changed_prompt_gate_or_policy_is_refused(self):
        exc = self.miss(expected_policy_sha256="0" * 64)
        self.assertEqual(exc.reason, "policy_changed")

    def test_a_tampered_artifact_is_refused(self):
        self.carry["artifacts"]["design.json"]["text"] = '{"planned_files":["app/evil.ts"]}\n'
        exc = self.miss()
        self.assertEqual(exc.reason, "artifact_hash_mismatch")
        self.assertIn("design.json", exc.detail)

    def test_a_tampered_hash_is_refused_too(self):
        self.carry["artifacts"]["task-contract.json"]["sha256"] = "0" * 64
        self.assertEqual(self.miss().reason, "artifact_hash_mismatch")

    def test_a_carry_missing_an_artifact_is_refused(self):
        del self.carry["artifacts"]["context.json"]
        exc = self.miss()
        self.assertEqual(exc.reason, "malformed")
        self.assertIn("context.json", exc.detail)

    def test_a_carry_smuggling_an_extra_artifact_is_refused(self):
        self.carry["artifacts"]["settings.json"] = {"text": "{}\n", "sha256": sha256_bytes(b"{}\n")}
        exc = self.miss()
        self.assertEqual(exc.reason, "malformed")
        self.assertIn("settings.json", exc.detail)

    def test_a_carry_of_the_wrong_version_or_ref_is_refused(self):
        for field, value in (("version", "2.0"), ("note_ref", "refs/notes/elsewhere")):
            with self.subTest(field):
                broken = {**self.carry, field: value}
                with self.assertRaises(CarryMiss) as ctx:
                    verified(broken)
                self.assertEqual(ctx.exception.reason, "malformed")

    def test_a_carry_whose_issue_snapshot_is_another_issue_is_refused(self):
        other = json.dumps({"number": 104, "title": "x", "body": "y"}, sort_keys=True) + "\n"
        self.carry["artifacts"]["issue.json"] = {
            "text": other, "sha256": sha256_bytes(other.encode())
        }
        self.assertEqual(self.miss().reason, "issue_changed")

    def test_every_reason_is_a_declared_one(self):
        for reason in REASONS:
            self.assertRegex(miss_line(issue=1, reason=reason), r"^FACTORY_CARRY_MISS issue=#1 ")
        with self.assertRaises(ValueError):
            miss_line(issue=1, reason="because")
        with self.assertRaises(ValueError):
            CarryMiss("because")


class CarryRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-carry-restore-")
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "source"
        write_upstream(self.source, bug=True)
        self.carry = a_carry(self.source)
        self.target = Path(self.tmp.name) / "target"
        self.target.mkdir()

    def test_restore_writes_the_carried_artifacts_byte_for_byte(self):
        written = restore(self.carry, self.target)
        for rel in written:
            with self.subTest(rel):
                self.assertEqual(
                    (self.target / rel).read_bytes(), (self.source / rel).read_bytes()
                )

    def test_the_kernels_own_fresh_snapshot_is_never_overwritten(self):
        """The kernel has just fetched the issue and its blockers with its own GitHub authority;
        the frontier the ticket compiler judges must be the one GitHub reports now."""
        written = restore(self.carry, self.target)
        for rel in KERNEL_FRESH_ARTIFACTS:
            self.assertIn(rel, self.carry["artifacts"])
            self.assertNotIn(rel, written)
            self.assertFalse((self.target / rel).exists(), rel)

    def test_a_tampered_artifact_is_refused_at_restore_too(self):
        self.carry["artifacts"]["context.json"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash mismatch on restore"):
            restore(self.carry, self.target)

    def test_recompiled_artifacts_are_compared_after_the_gates_rerun(self):
        restore(self.carry, self.target)
        self.assertEqual(recompiled_mismatches(self.carry, self.target), ())
        (self.target / "context.json").write_bytes(b'{"other":1}\n')
        self.assertEqual(recompiled_mismatches(self.carry, self.target), ("context.json",))
        (self.target / "design.json").unlink()
        self.assertIn("design.json", recompiled_mismatches(self.carry, self.target))

    def test_every_compiled_artifact_the_gates_write_is_compared(self):
        for rel in RECOMPILED_ARTIFACTS:
            self.assertIn(rel, CARRY_ARTIFACTS)
        for rel in ("task-contract.json", "context.json", "design.json",
                    "architecture-governor.json", "ticket.json", "frontier.json"):
            self.assertIn(rel, RECOMPILED_ARTIFACTS)

    def test_the_identity_record_names_the_run_the_upstream_came_from(self):
        record = identity_record(
            self.carry, reused_at="2026-09-06T12:30:00Z", age_minutes=30,
            skipped_roles=("investigate", "contract", "context", "architecture"),
        )
        self.assertEqual(record["source_run_id"], "issue-103-a1-deadbeef01")
        self.assertEqual(record["age_minutes"], 30)
        self.assertEqual(record["base_sha"], BASE)
        self.assertEqual(record["skipped_roles"][0], "investigate")
        self.assertEqual(
            record["artifacts"]["design.json"], self.carry["artifacts"]["design.json"]["sha256"]
        )

    def test_age_is_whole_minutes_and_never_negative(self):
        self.assertEqual(age_minutes("2026-09-06T12:00:00Z", "2026-09-06T12:45:30Z"), 45)
        self.assertEqual(age_minutes("2026-09-06T12:00:00Z", "2026-09-06T11:00:00Z"), 0)
        self.assertEqual(age_minutes("nonsense", "2026-09-06T12:00:00Z"), 0)


# --- the kernel path ---------------------------------------------------------------------------


class Recorder:
    """A fake `_exec` that plays the deterministic gates and records every argv."""

    def __init__(self, artifacts: Path, *, issue_number: int = 103) -> None:
        self.artifacts = artifacts
        self.issue_number = issue_number
        self.calls: list[list[str]] = []
        self.events: list[str] = []
        self.note: dict | None = None
        self.written: list[dict] = []
        self.dropped: list[int] = []
        self.refuse: set[str] = set()
        self.salt = ""

    def __call__(self, argv, *, cwd, env=None, credential_scope="none", timeout=300,
                 transcript=None):
        self.calls.append(list(argv))
        joined = " ".join(str(part) for part in argv)
        if "factory_protocol.py contract" in joined:
            name = "contract-gate"
        elif "factory_protocol.py context" in joined:
            name = "context-gate"
        elif "factory_architecture.py compile" in joined:
            name = "architecture-gate"
        elif "factory_architecture.py scope" in joined:
            name = "architecture-scope"
        elif "carry-read" in joined:
            name = "carry-read"
        elif "carry-write" in joined:
            name = "carry-write"
        elif "carry-drop" in joined:
            name = "carry-drop"
        else:
            name = argv[0] if argv else "?"
        self.events.append(name)
        if name in self.refuse:
            raise ToolRefused(list(argv), rc=1, output=f"{name} refused")
        if name in COMPILERS:
            COMPILERS[name](self.artifacts, issue_number=self.issue_number, salt=self.salt)
        elif name == "carry-read":
            if self.note is not None:
                Path(argv[argv.index("--output") + 1]).write_bytes(jbytes(self.note))
        elif name == "carry-write":
            self.written.append(
                {argv[i]: argv[i + 1] for i in range(0, len(argv) - 1) if str(argv[i]).startswith("--")}
            )
        elif name == "carry-drop":
            self.dropped.append(int(argv[argv.index("--issue") + 1]))
        return ""


def carry_runtime(tmp: Path, recorder: Recorder, paths: RunPaths) -> KernelRuntime:
    rt = object.__new__(KernelRuntime)
    rt.repo_root = ROOT
    rt._exec = recorder
    rt._git = lambda *args, cwd=None: KERNEL if args[:1] == ("rev-parse",) else "ls-tree-output"
    rt._is_ancestor = lambda base, head: True
    rt._lease_heartbeat = lambda *a, **kw: None
    return rt


class CarryReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-carry-reuse-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.paths = RunPaths.create(self.home / "work", "run")
        self.source = self.home / "source"
        write_upstream(self.source)
        # The kernel writes its own fresh snapshot before it ever looks for a carry.
        for rel in KERNEL_FRESH_ARTIFACTS:
            (self.paths.artifacts / rel).write_bytes((self.source / rel).read_bytes())
        self.recorder = Recorder(self.paths.artifacts)
        self.rt = carry_runtime(self.home, self.recorder, self.paths)
        self.policy = policy_digest("ls-tree-output")

    def reuse(self, **overrides) -> tuple[bool, str]:
        fields = {
            "issue": ISSUE,
            "issue_number": 103,
            "base_sha": BASE,
            "skipped_roles": ("investigate", "contract", "context", "architecture"),
        }
        fields.update(overrides)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = self.rt._carry_reuse(self.paths, self.home, {}, **fields)
        return result, out.getvalue()

    def stage(self, **overrides) -> dict:
        fields = {"kernel_commit": KERNEL, "policy_sha256": self.policy}
        fields.update(overrides)
        carry = a_carry(self.source, **fields)
        self.recorder.note = carry
        return carry

    def rows(self) -> list[dict]:
        path = self.paths.transcripts / STAGE_TIMINGS
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_a_hit_restores_the_upstream_and_runs_every_deterministic_gate(self):
        self.stage()
        hit, out = self.reuse()
        self.assertTrue(hit, out)
        self.assertEqual(
            self.recorder.events,
            ["carry-read", "contract-gate", "context-gate", "architecture-gate",
             "architecture-scope"],
        )
        for rel in ("task-contract.raw.json", "context.raw.json", "design.raw.json",
                    "architecture-governor.raw.json"):
            self.assertTrue((self.paths.artifacts / rel).is_file(), rel)
        self.assertIn("FACTORY_CARRY_HIT issue=#103 base=bbbbbbb", out)
        self.assertIn("stages=investigate,contract,context,architecture", out)

    def test_a_hit_records_where_its_upstream_came_from(self):
        self.stage()
        self.assertTrue(self.reuse()[0])
        record = json.loads(
            (self.paths.artifacts / CARRY_IDENTITY_ARTIFACT).read_text(encoding="utf-8")
        )
        self.assertEqual(record["issue"], 103)
        self.assertEqual(record["source_run_id"], "issue-103-a1-deadbeef01")
        self.assertEqual(record["base_sha"], BASE)
        self.assertEqual(len(record["artifacts"]), len(CARRY_ARTIFACTS))

    def test_a_hit_emits_one_carry_stage_row(self):
        self.stage()
        _, out = self.reuse()
        rows = [row for row in self.rows() if row["name"] == "carry"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["kind"], "exec")
        self.assertEqual(rows[0]["outcome"], "ok")
        self.assertEqual(rows[0]["carry"], "hit")
        self.assertIsInstance(rows[0]["seconds"], float)
        self.assertIn("FACTORY_STAGE kind=exec name=carry seconds=", out)
        self.assertIn("outcome=ok", out)

    def test_an_absent_carry_misses_and_leaves_nothing_behind(self):
        self.recorder.note = None
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("FACTORY_CARRY_MISS issue=#103 reason=absent", out)
        self.assertEqual(self.recorder.events, ["carry-read"])
        self.assertFalse((self.paths.artifacts / "task-contract.raw.json").exists())
        self.assertEqual([row["carry"] for row in self.rows() if row["name"] == "carry"], ["miss"])

    def test_a_moved_base_misses_by_its_own_reason(self):
        self.stage()
        hit, out = self.reuse(base_sha="c" * 40)
        self.assertFalse(hit)
        self.assertIn("reason=base_moved", out)
        self.assertFalse((self.paths.artifacts / "design.json").exists())

    def test_an_edited_issue_misses_by_its_own_reason(self):
        self.stage()
        hit, out = self.reuse(issue={**ISSUE, "body": "rewritten"})
        self.assertFalse(hit)
        self.assertIn("reason=issue_changed", out)

    def test_a_changed_policy_misses_by_its_own_reason(self):
        self.stage(policy_sha256="0" * 64)
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("reason=policy_changed", out)

    def test_a_kernel_that_is_not_an_ancestor_misses_by_its_own_reason(self):
        self.stage(kernel_commit="e" * 40)
        self.rt._is_ancestor = lambda base, head: False
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("reason=kernel_not_ancestor", out)

    def test_a_tampered_artifact_misses_and_nothing_is_restored(self):
        carry = self.stage()
        carry["artifacts"]["design.json"]["text"] = '{"planned_files":["app/evil.ts"]}\n'
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("reason=artifact_hash_mismatch", out)
        self.assertFalse((self.paths.artifacts / "design.json").exists())

    def test_a_refused_restored_gate_discards_the_carry_and_falls_back(self):
        self.stage()
        self.recorder.refuse = {"architecture-gate"}
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("reason=gate_refused", out)
        for rel in ("task-contract.raw.json", "context.raw.json", "design.raw.json",
                    "architecture-governor.raw.json"):
            self.assertFalse((self.paths.artifacts / rel).exists(), rel)
        self.assertFalse((self.paths.artifacts / CARRY_IDENTITY_ARTIFACT).exists())

    def test_a_governor_that_no_longer_proceeds_discards_the_carry(self):
        self.stage()
        original = self.rt._require_governor_proceed

        def refuse(paths):
            raise NeedsHuman("architecture governor returned veto: ")

        self.rt._require_governor_proceed = refuse
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("reason=gate_refused", out)
        self.assertIsNotNone(original)

    def test_a_gate_that_does_not_reproduce_its_artifact_discards_the_carry(self):
        self.stage()
        self.recorder.salt = "the world moved"
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("reason=recompiled_mismatch", out)
        self.assertFalse((self.paths.artifacts / "context.json").exists())

    def test_a_note_that_declares_no_binding_misses_as_malformed_not_as_absent(self):
        """The reader hands the bytes over; the kernel is the judge and names the fault."""
        self.recorder.note = {"version": "0.9", "issue": 103}
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("reason=malformed", out)

    def test_a_carry_read_failure_is_a_miss_not_a_build_failure(self):
        self.recorder.refuse = {"carry-read"}
        hit, out = self.reuse()
        self.assertFalse(hit)
        self.assertIn("reason=read_failed", out)

    def test_the_kernel_reads_and_writes_the_carry_with_github_scope_and_nothing_else(self):
        source = inspect.getsource(KernelRuntime._carry_load)
        source += inspect.getsource(KernelRuntime._carry_write)
        source += inspect.getsource(KernelRuntime._carry_drop)
        self.assertEqual(source.count('credential_scope="github"'), 3)
        self.assertEqual(source.count("scripts/factory_provenance.py"), 3)


class CarryInBuildIssueTests(unittest.TestCase):
    """`build_issue` driven against fakes to the test author, where the build is stopped."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-carry-build-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.work_root = self.home / "work"
        self.work_root.mkdir()
        self.worktree = self.home / "worktree"
        self.worktree.mkdir()
        env = mock.patch.dict(os.environ, {"FACTORY_WORKDIR": str(self.work_root)})
        env.start()
        self.addCleanup(env.stop)
        config = load_config(ROOT / ".factory" / "kernel.json")
        self.config = dataclasses.replace(
            config, runtime=dataclasses.replace(config.runtime, work_root=self.work_root)
        )
        self.source = self.home / "source"
        write_upstream(self.source)

    def build(self, *, note: dict | None = None, refuse: set[str] | None = None,
              expect: type[BaseException] = NeedsHuman):
        gh = FakeGitHub()
        rt = KernelRuntime(repo_root=ROOT, config=self.config)
        rt.github = gh
        rt.check_stop = lambda: None
        rt._fetch_main = lambda: None
        rt._git = lambda *args, cwd=None: KERNEL if args[:1] == ("rev-parse",) else "ls-tree"
        rt._prepare_worktree = lambda cwd, paths: None
        rt._issue_frontier = lambda issue: {
            "version": "1.0", "issue": dict(issue), "blockers": []
        }
        rt._lease_heartbeat = lambda *a, **kw: None
        rt._is_ancestor = lambda base, head: True
        rt._require_stage_note = lambda artifacts, role: None
        holder: dict = {}
        roles: list[str] = []

        def agent(role, cwd, paths, *, context="", env):
            roles.append(role)
            recorder = holder["recorder"]
            recorder.events.append(f"agent:{role}")
            if role == "test_author":
                raise NeedsHuman("stopped at the test author")
            artifacts = paths.artifacts
            if role in ("plan", "investigate"):
                (artifacts / "plan.md").write_text("plan\n", encoding="utf-8")
            if role == "contract":
                (artifacts / "task-contract.raw.json").write_bytes(jbytes({"raw": "contract"}))
            if role == "context":
                (artifacts / "context.raw.json").write_bytes(jbytes({"raw": "context"}))
                (artifacts / "design.raw.json").write_bytes(jbytes({"raw": "design"}))
            if role == "architecture":
                (artifacts / "architecture-governor.raw.json").write_bytes(
                    jbytes({"raw": "governor"})
                )

        rt._agent = agent
        original_create = RunPaths.create

        def create(cls_work_root, run_id):
            paths = original_create(cls_work_root, run_id)
            recorder = Recorder(paths.artifacts)
            recorder.note = note
            recorder.refuse = set(refuse or ())
            holder["recorder"] = recorder
            holder["paths"] = paths
            rt._exec = recorder
            return paths

        with (
            mock.patch("factory_kernel.runtime.RunPaths.create", side_effect=create),
            mock.patch(
                "factory_kernel.runtime.create_detached", return_value=FakeWorktree(self.worktree)
            ),
            mock.patch("factory_kernel.runtime.remove", lambda repo, wt: None),
            contextlib.redirect_stdout(io.StringIO()) as out,
            self.assertRaises(expect),
        ):
            rt.build_issue(103)
        return holder["recorder"], roles, out.getvalue(), holder["paths"]

    def test_a_fresh_build_writes_the_carry_after_the_architecture_gate_and_not_before(self):
        recorder, roles, out, _ = self.build()
        self.assertEqual(
            roles, ["plan", "contract", "context", "architecture", "test_author"], roles
        )
        events = recorder.events
        self.assertIn("carry-write", events)
        self.assertLess(events.index("architecture-gate"), events.index("carry-write"))
        self.assertLess(events.index("architecture-scope"), events.index("carry-write"))
        self.assertLess(events.index("carry-write"), events.index("agent:test_author"))
        self.assertIn("FACTORY_CARRY_MISS issue=#103 reason=absent", out)

    def test_the_carry_is_written_with_the_bindings_that_will_be_checked(self):
        recorder, _, _, _ = self.build()
        (written,) = recorder.written
        self.assertEqual(written["--issue"], "103")
        self.assertEqual(written["--base"], KERNEL)
        self.assertEqual(written["--issue-sha256"], issue_digest(FakeGitHub().issue(103)))
        self.assertEqual(written["--kernel-commit"], KERNEL)
        self.assertEqual(written["--policy-sha256"], policy_digest("ls-tree"))
        self.assertTrue(written["--run-id"].startswith("issue-103-a"))

    def test_a_build_that_dies_before_the_architecture_gate_writes_no_carry(self):
        recorder, _, _, _ = self.build(refuse={"context-gate"}, expect=ToolRefused)
        self.assertEqual(recorder.written, [], "a carry was written from uncertified upstream")
        self.assertNotIn("architecture-gate", recorder.events)

    def test_a_hit_skips_exactly_the_model_stages_and_runs_every_gate(self):
        issue = FakeGitHub().issue(103)
        source = self.home / "hit"
        write_upstream(source, issue=issue)
        carry = a_carry(
            source, issue_sha256=issue_digest(issue), base_sha=KERNEL,
            kernel_commit=KERNEL, policy_sha256=policy_digest("ls-tree"),
        )
        recorder, roles, out, paths = self.build(note=carry)
        self.assertEqual(roles, ["test_author"], "only the model stages are skipped")
        self.assertEqual(
            [e for e in recorder.events if not e.startswith("agent:")],
            ["carry-read", "contract-gate", "context-gate", "architecture-gate",
             "architecture-scope"],
        )
        self.assertIn("FACTORY_CARRY_HIT issue=#103", out)
        self.assertEqual(recorder.written, [], "a reused carry is not rewritten")
        self.assertTrue((paths.artifacts / CARRY_IDENTITY_ARTIFACT).is_file())

    def test_a_hit_never_overwrites_the_kernels_fresh_issue_snapshot(self):
        issue = FakeGitHub().issue(103)
        source = self.home / "hit2"
        write_upstream(source, issue=issue)
        stale = {**issue, "labels": [{"name": "stale"}]}
        (source / "issue-frontier.json").write_bytes(
            jbytes({"version": "1.0", "issue": stale, "blockers": [{"issue": 9, "state": "OPEN"}]})
        )
        carry = a_carry(
            source, issue_sha256=issue_digest(issue), base_sha=KERNEL,
            kernel_commit=KERNEL, policy_sha256=policy_digest("ls-tree"),
        )
        _, _, out, paths = self.build(note=carry)
        self.assertIn("FACTORY_CARRY_HIT", out)
        frontier = json.loads((paths.artifacts / "issue-frontier.json").read_text(encoding="utf-8"))
        self.assertEqual(frontier["blockers"], [], "the kernel's own fresh snapshot survived")

    def test_the_build_sequence_calls_each_deterministic_authority_from_one_place(self):
        source = inspect.getsource(KernelRuntime.build_issue)
        for helper in ("_gate_contract", "_gate_context", "_gate_architecture",
                       "_require_governor_proceed", "_gate_architecture_scope"):
            self.assertEqual(source.count(f"self.{helper}("), 1, helper)
        reuse = inspect.getsource(KernelRuntime._carry_reuse)
        for helper in ("_gate_contract", "_gate_context", "_gate_architecture",
                       "_require_governor_proceed", "_gate_architecture_scope"):
            self.assertIn(f"self.{helper}(", reuse, f"a reused build skips {helper}")

    def test_the_carry_is_written_from_exactly_one_place(self):
        source = inspect.getsource(KernelRuntime)
        self.assertEqual(source.count("self._carry_write("), 1)
        self.assertEqual(source.count("self._carry_reuse("), 1)

    def test_a_merge_drops_the_issues_carry(self):
        """The drop moved into `_merge_and_verify` with ACP-004, so BOTH merge entry points --
        the inline one and the one that runs behind its own freshly minted identity -- drop the
        carry. The ordering it pins is unchanged: after the merge, before the verified marker."""
        source = inspect.getsource(KernelRuntime._merge_and_verify)
        self.assertIn("self._carry_drop(", source)
        self.assertLess(source.index("merge_squash"), source.index("self._carry_drop("))
        self.assertLess(source.index("self._carry_drop("), source.index("FACTORY_MERGED_VERIFIED"))

    def test_both_merge_entry_points_share_the_one_drop(self):
        source = inspect.getsource(KernelRuntime)
        self.assertEqual(source.count("self._carry_drop("), 1, "one merge, one drop")
        for entry in (KernelRuntime.validate_pr, KernelRuntime.merge_authorized):
            self.assertIn("self._merge_and_verify(", inspect.getsource(entry))


# --- what the carry must not change -------------------------------------------------------------


class CarryIsOutOfAWorkersReachTests(unittest.TestCase):
    def test_the_carry_lives_on_a_git_notes_ref_not_in_the_tree(self):
        self.assertTrue(CARRY_NOTE_REF.startswith("refs/notes/"))
        self.assertNotEqual(CARRY_NOTE_REF, "refs/notes/dark-factory-provenance")

    def test_every_tool_bearing_role_is_denied_the_git_directory(self):
        for role, tools in ROLE_TOOLS.items():
            if not tools:
                continue
            with self.subTest(role):
                scope = path_scope(role)
                self.assertIn(".git", scope.deny)
                self.assertIn(".git/**", scope.deny)

    def test_the_deny_rules_the_cli_receives_name_the_git_directory(self):
        role = "implement"
        _, deny = path_rules(ROLE_PATH_SCOPE[role], allowed_tools(role), artifacts=None)
        self.assertIn("Read(./.git/**)", deny)
        self.assertIn("Edit(./.git/**)", deny)
        self.assertIn("Read(./.git)", deny)

    def test_no_role_may_write_anywhere_near_the_carry(self):
        for role in ROLE_TOOLS:
            for pattern in path_scope(role).write:
                self.assertFalse(pattern.startswith(".git"), f"{role}: {pattern}")

    def test_the_deny_list_still_covers_the_trust_root(self):
        for root in ("factory_kernel/**", "harness/**", "scripts/**", ".factory/prompts/**"):
            self.assertIn(root, TRUST_ROOT_DENY_PATHS)


class CarryDoesNotChangeValidationTests(unittest.TestCase):
    """The certifiers and holdouts run at validation and judge the artifacts in the PR."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-carry-pack-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.artifacts = self.home / "artifacts"
        self.artifacts.mkdir()
        self.repo = self.home / "repo"
        (self.repo / ".factory").mkdir(parents=True)
        (self.repo / ".factory" / "architecture.json").write_bytes(jbytes({"version": "1.0"}))
        contract = {"version": "2.0", "issue": {"number": 103, "title": "t"}}
        contract_sha = sha256_bytes(jbytes(contract))
        ticket = {"version": "1.0", "issue": 103, "contract_sha256": contract_sha}
        context = {"version": "1.0", "contract_sha256": contract_sha}
        payload = {
            "task-contract.json": contract,
            "ticket.json": ticket,
            "frontier.json": {"version": "1.0", "issue": 103, "ready": True,
                              "ticket_sha256": sha256_bytes(jbytes(ticket))},
            "context.json": context,
            "design.json": {"contract_sha256": contract_sha,
                            "context_sha256": sha256_bytes(jbytes(context))},
            "architecture-governor.json": {"decision": "proceed"},
            "test-plan.json": {"version": "1.0"},
            "red-proof.json": {"version": "1.0"},
            "final-green-proof.json": {"version": "1.0"},
            "final-green-proof.impact.json": {"version": "1.0"},
            "final-green-proof.architecture.json": {"version": "1.0"},
            "architecture-conformance.json": {"version": "1.0"},
        }
        for rel, value in payload.items():
            (self.artifacts / rel).write_bytes(jbytes(value))

    def pack(self) -> dict:
        return build_pack(
            artifact_root=self.artifacts, repo_root=self.repo, issue=103,
            base_sha="a" * 40, head_sha="f" * 40,
        )

    def test_a_build_that_derived_its_own_upstream_produces_the_pack_it_always_did(self):
        pack = self.pack()
        self.assertNotIn("carry", pack)
        self.assertEqual(verify_pack(pack)["issue"], 103)

    def test_a_reused_build_says_so_in_its_pack_and_names_the_run(self):
        (self.artifacts / CARRY_IDENTITY_ARTIFACT).write_bytes(
            jbytes({"version": "1.0", "issue": 103, "base_sha": "a" * 40,
                    "source_run_id": "issue-103-a1-deadbeef01"})
        )
        pack = self.pack()
        self.assertEqual(pack["carry"]["source_run_id"], "issue-103-a1-deadbeef01")
        self.assertEqual(verify_pack(pack)["carry"]["issue"], 103)

    def test_a_pack_may_not_claim_another_issues_or_another_bases_upstream(self):
        pack = self.pack()
        pack["carry"] = {"issue": 104, "base_sha": "a" * 40}
        with self.assertRaisesRegex(ValueError, "carry belongs to a different issue"):
            verify_pack(pack)
        pack["carry"] = {"issue": 103, "base_sha": "c" * 40}
        with self.assertRaisesRegex(ValueError, "carry was built from a different base"):
            verify_pack(pack)

    def test_the_claim_set_the_validator_verifies_is_unchanged(self):
        pack = self.pack()
        self.assertEqual(len(pack["artifacts"]), 13)
        for claim in ("contract", "tickets", "frontier", "context", "design",
                      "architecture-governor", "red-proof", "green-proof"):
            self.assertIn(claim, pack["artifacts"])


class CarryScriptTests(unittest.TestCase):
    """`scripts/factory_provenance.py carry-*` against a real local repository."""

    SCRIPT = ROOT / "scripts" / "factory_provenance.py"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-carry-script-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "x.txt").write_text("hi\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.artifacts = self.home / "artifacts"
        write_upstream(self.artifacts)

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@e.invalid", *args],
            cwd=self.repo, capture_output=True, text=True,
        )
        if proc.returncode:
            raise RuntimeError(f"git {args}: {proc.stdout}{proc.stderr}")
        return proc.stdout.strip()

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            cwd=self.repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def write(self, issue: int = 103) -> subprocess.CompletedProcess:
        return self.run_script(
            "carry-write", "--issue", str(issue), "--artifacts", str(self.artifacts),
            "--base", self.base, "--issue-sha256", issue_digest(ISSUE),
            "--kernel-commit", self.base, "--policy-sha256", POLICY,
            "--run-id", "issue-103-a1-deadbeef01", "--local-notes",
        )

    def read(self, issue: int = 103) -> tuple[subprocess.CompletedProcess, Path]:
        out = self.home / f"note-{issue}.json"
        proc = self.run_script(
            "carry-read", "--issue", str(issue), "--output", str(out), "--local-notes"
        )
        return proc, out

    def test_a_carry_round_trips_through_the_notes_ref(self):
        proc = self.write()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("CARRY_WRITTEN issue=#103", proc.stdout)
        self.assertIn(CARRY_NOTE_REF, self.git("for-each-ref", "--format=%(refname)"))
        proc, out = self.read()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        note = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(note["issue"], 103)
        self.assertEqual(note["base_sha"], self.base)
        self.assertEqual(set(note["artifacts"]), set(CARRY_ARTIFACTS))

    def test_the_carry_is_keyed_by_issue_number(self):
        self.write(103)
        proc, out = self.read(104)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("CARRY_ABSENT issue=#104", proc.stdout)
        self.assertFalse(out.exists())

    def test_a_second_build_replaces_the_carry_rather_than_adding_one(self):
        self.write()
        self.write()
        listed = self.git("notes", f"--ref={CARRY_NOTE_REF}", "list").splitlines()
        self.assertEqual(len(listed), 1, listed)

    def test_dropping_a_carry_removes_it_and_dropping_twice_is_not_an_error(self):
        self.write()
        first = self.run_script("carry-drop", "--issue", "103", "--local-notes")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        proc, out = self.read()
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(out.exists())
        second = self.run_script("carry-drop", "--issue", "103", "--local-notes")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

    def test_a_note_that_declares_no_binding_is_still_handed_to_the_kernel(self):
        self.write()
        key = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.repo,
            input=carry_key_content(103), capture_output=True,
        ).stdout.decode().strip()
        bad = self.home / "bad.json"
        bad.write_bytes(jbytes({"version": "0.9"}))
        self.git("-c", "user.name=t", "-c", "user.email=t@e.invalid", "notes",
                 f"--ref={CARRY_NOTE_REF}", "add", "-f", "-F", str(bad), key)
        proc, out = self.read()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("unbound=", proc.stdout)
        self.assertTrue(out.is_file(), "the kernel, not the reader, judges a carry")

    def test_a_note_the_kernel_writes_carries_the_kernel_identity(self):
        self.write()
        author = self.git("log", "-1", "--format=%an", CARRY_NOTE_REF)
        self.assertEqual(author, "github-actions[bot]")

    def test_an_uncertified_artifact_set_cannot_be_written(self):
        (self.artifacts / "architecture-governor.json").unlink()
        proc = self.write()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("architecture-governor.json", proc.stderr)


class CarryDocumentedTests(unittest.TestCase):
    def test_the_decision_is_recorded(self):
        text = (ROOT / ".factory" / "decisions.md").read_text(encoding="utf-8")
        self.assertIn("## D-071", text)
        self.assertIn("FACTORY_CARRY_HIT", text)

    def test_the_build_sequence_documents_the_carry(self):
        rules = (ROOT / "FACTORY_RULES.md").read_text(encoding="utf-8")
        self.assertIn("carry", rules.lower())
        self.assertIn("D-071", rules)
        self.assertIn("D-071", (ROOT / "FACTORY.md").read_text(encoding="utf-8"))

    def test_the_module_states_what_it_deliberately_does_not_reuse(self):
        doc = carry_mod.__doc__ or ""
        self.assertIn("deterministic", doc)
        self.assertIn("model", doc)


if __name__ == "__main__":
    unittest.main()
