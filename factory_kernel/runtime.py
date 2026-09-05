"""Repo-owned orchestration for Dark Factory.

Model workers may investigate, design, test, implement and review. They never become engineering
or merge authorities: deterministic compilers, replay gates, holdouts, the canonical harness and
exact merged-tree verification remain the judges.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
from typing import Any, Mapping

from .agents import AgentRequest
from .canonical import canonical_bytes
from .config import KernelConfig
from .credential_env import scoped_environment
from .github_cli import GitHubClient
from .providers import ClaudeCliProvider, prompt_text
from .independence import (
    authority_inputs,
    build_certificate,
    claims_for_authority,
    verify_certificate,
)
from .provenance import BUILDER_ARTIFACTS, verify_pack
from .refusal import (
    ToolRefused,
    describe,
    refusal_record,
    rehead_count,
    rehead_eligible,
    render_refusal_marker,
    render_rehead_marker,
    render_resume_marker,
    resume_count,
    scrub,
)
from .repro import (
    RED_TAIL_CHARS,
    DEFERRED_ARTIFACT, OBSERVED_ARTIFACT, REPRO_ARTIFACT, ReproRefused, default_runner,
    deferred_record, execute, load_deferred, load_repro, observed_record, verify_deferred_in_red,
)
from .review import AXES, ROLE_FOR_AXIS, ReviewInvalid, aggregate, read_axes
from .pr_body import render_pr_body
from .trusted_programs import resolve_trusted_program
from .attached import extract_block
from .worker_policy import BUILDER_BLIND_PATHS, KERNEL_COMMIT_NAME, KERNEL_COMMIT_ARGS, max_turns
from .worktree import Worktree, create_detached, remove

STAGE_TIMINGS = "stage-timings.jsonl"


def record_stage_timing(
    transcripts: Path, *, kind: str, name: str, started: float, ended: float, **extra: Any
) -> None:
    """Append one line per stage so a run's wall time can be read back per stage.

    Observability only: nothing reads this file to decide anything. `started`/`ended` are
    `time.time()` values; the ISO forms are for humans reading the artifact.
    """
    transcripts.mkdir(parents=True, exist_ok=True)
    row = {
        "kind": kind,
        "name": name,
        "started_at": _iso(started),
        "ended_at": _iso(ended),
        "seconds": round(ended - started, 3),
    }
    row.update({k: v for k, v in extra.items() if v is not None})
    with (transcripts / STAGE_TIMINGS).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _iso(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))

# `Blocked by: #N` lines in an issue body name the issues that must be CLOSED before this one
# is on the ready frontier. The kernel resolves them with its own GitHub authority and writes
# the snapshot for the credential-free ticket compiler; the same pattern lives in
# scripts/factory_artifacts.py for the frontier filter.
BLOCKED_BY = re.compile(r"(?im)^Blocked by:\s+#([1-9][0-9]*)\s*$")
ISSUE_FRONTIER_ARTIFACT = "issue-frontier.json"


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FactoryStopped(RuntimeError):
    pass


class PostMergeUnverified(RuntimeError):
    """The squash merge happened and then failed verification, so main is untrusted.

    This is deliberately not a NeedsHuman. Every other validation failure leaves the branch
    unmerged and the repository exactly as it was, which is why the ordinary handler can honestly
    say no merge was authorized and invite a fresh rebuild. After this one a merge exists on main
    that nothing has verified, so both of those statements would be false and the invitation would
    be actively harmful: a rebuild starts from the very commit that is in doubt.

    Detection after the fact is inherent -- pre-authorization prevents, post-verification detects.
    Continuing to dispatch autonomously after detecting it is not inherent, and is what this class
    exists to stop.
    """


class NeedsHuman(RuntimeError):
    pass


@dataclass(frozen=True)
class DispatchDecision:
    kind: str
    number: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class RunPaths:
    root: Path
    artifacts: Path
    transcripts: Path

    @classmethod
    def create(cls, work_root: Path, run_id: str) -> "RunPaths":
        root = work_root / "runs" / run_id
        artifacts = root / "artifacts"
        transcripts = root / "transcripts"
        artifacts.mkdir(parents=True, exist_ok=False)
        transcripts.mkdir(parents=True, exist_ok=True)
        return cls(root=root, artifacts=artifacts, transcripts=transcripts)


class KernelRuntime:
    VALIDATION_FAILURE_MARKER = "<!-- dark-factory-validation-failed -->"
    PRIORITY = {"priority:critical": 0, "priority:high": 1, "priority:medium": 2, "priority:low": 3}

    def __init__(self, *, repo_root: Path, config: KernelConfig):
        self.repo_root = repo_root.resolve()
        self.config = config
        self.provider = ClaudeCliProvider(config.provider)
        self.github = GitHubClient(config.repository, cwd=self.repo_root)

    # ---------- control plane ----------

    def check_stop(self) -> None:
        env = scoped_environment(
            {
                "FACTORY_REPO": self.config.repository,
                "FACTORY_WORKDIR": str(self.config.runtime.work_root),
            },
            scope="github",
        )
        proc = subprocess.run(
            ["bash", "scripts/factory-stop.sh"],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if proc.returncode:
            detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
            raise FactoryStopped(detail or "factory stop check failed closed")

    def reap_stale_claims(self) -> None:
        self._exec(
            [
                "python", "scripts/factory_lease.py", "reap",
                "--active-ttl", str(self.config.runtime.active_lease_ttl_seconds),
                "--legacy-ttl", str(self.config.runtime.legacy_lease_ttl_seconds),
            ],
            cwd=self.repo_root,
            credential_scope="github",
            timeout=180,
        )

    def choose_dispatch(self) -> DispatchDecision:
        """Canonical priority: stop -> reap -> review -> highest-priority accepted issue."""
        self.check_stop()
        self.reap_stale_claims()

        review = self.github.list_prs(self.config.labels["needs_review"])
        if review:
            return DispatchDecision(
                "validate-pr", self._oldest_number(review), "PR validation has priority"
            )

        # A refused PR whose only fault is that main moved under it is re-headed without a
        # model, before any new build starts: finishing certified work outranks starting more.
        # Every other refusal leaves the PR where it is (section 7).
        for pr in sorted(
            self.github.list_prs(self.config.labels["needs_fix"]),
            key=lambda row: (str(row.get("updatedAt") or ""), int(row["number"])),
        ):
            number = int(pr["number"])
            if rehead_eligible(self.github.pr_comments(number)):
                return DispatchDecision(
                    "rehead-pr", number, "stale-base refusal; model-free re-head onto current main"
                )

        accepted = self.github.list_issues(self.config.labels["accepted"])
        idle = [
            issue
            for issue in accepted
            if self.config.labels["in_progress"] not in self.github.labels(issue)
        ]
        if idle:
            issue = min(idle, key=self._issue_dispatch_key)
            return DispatchDecision(
                "build-issue", int(issue["number"]), "highest-priority accepted issue is idle"
            )
        return DispatchDecision("idle", reason="no review PR or accepted idle issue")

    def dispatch_once(self, *, merge: bool = True) -> DispatchDecision:
        decision = self.choose_dispatch()
        if decision.kind == "validate-pr" and decision.number is not None:
            self.validate_pr(decision.number, merge=merge)
        elif decision.kind == "rehead-pr" and decision.number is not None:
            self.rehead_pr(decision.number)
        elif decision.kind == "build-issue" and decision.number is not None:
            self.build_issue(decision.number)
        return decision

    # ---------- builder ----------

    def _lease_heartbeat(
        self,
        action: str,
        issue: int,
        stage: str,
        paths: RunPaths,
        *,
        cwd: Path,
        pr: int | None = None,
    ) -> None:
        """The kernel alone touches the issue lease, and it is the only build-side subprocess
        that holds GitHub credentials. Contract/proof programs run model-authored commands and
        must never see a repository token, so they do not heartbeat."""
        argv = [
            "python", "scripts/factory_lease.py", action,
            "--issue", str(issue), "--stage", stage,
            "--lease-file", str(paths.artifacts / "factory-lease.json"),
        ]
        if pr is not None and action != "start":
            argv.extend(["--pr", str(pr)])
        self._exec(
            argv,
            cwd=cwd,
            credential_scope="github",
            timeout=120,
            transcript=paths.transcripts / f"lease-{stage}.log",
        )

    def build_issue(self, issue_number: int) -> int:
        self.check_stop()
        issue = self.github.issue(issue_number)
        labels = self.github.labels(issue)
        if self.config.labels["accepted"] not in labels:
            raise NeedsHuman(f"issue #{issue_number} is not {self.config.labels['accepted']}")
        if self.config.labels["in_progress"] in labels:
            raise NeedsHuman(f"issue #{issue_number} already has an active factory claim")
        attempt = self._next_build_attempt(issue_number)
        if attempt > self.config.runtime.max_attempts:
            self._mark_issue_human(
                issue_number,
                f"independent validation failed {attempt - 1} times; retry budget exhausted",
            )
            raise NeedsHuman(f"issue #{issue_number} exhausted the autonomous retry budget")

        self._fetch_main()
        base_sha = self._git("rev-parse", f"origin/{self.config.default_branch}")
        run_id = f"issue-{issue_number}-a{attempt}-{uuid.uuid4().hex[:10]}"
        paths = RunPaths.create(self.config.runtime.work_root, run_id)
        # The builder never sees the holdout: its worktree is created without the scenario
        # programs on disk. The validator worktree below is created without this argument.
        worktree = create_detached(
            self.repo_root,
            base_sha,
            base_dir=self.config.runtime.work_root / "worktrees",
            blind=BUILDER_BLIND_PATHS,
        )
        branch = f"factory/issue-{issue_number}-a{attempt}-{run_id.rsplit('-', 1)[-1]}"
        handed_off = False
        self.github.add_issue_label(issue_number, self.config.labels["in_progress"])
        try:
            self._git("checkout", "-b", branch, cwd=worktree.path)
            self._prepare_worktree(worktree.path, paths)
            self._write_json(paths.artifacts / "issue.json", issue)
            # The ticket/frontier compiler runs with no credentials, so the kernel fetches the
            # issue and every blocker it names here, before any model stage, and the script
            # judges readiness from this snapshot rather than by calling GitHub itself.
            self._write_json(
                paths.artifacts / ISSUE_FRONTIER_ARTIFACT, self._issue_frontier(issue)
            )
            # The branch was cut from base_sha, resolved once at the start of this build. It is
            # what the provenance pack records; it is never re-read from origin/main, which can
            # advance while the build runs (D-042).
            env = self._run_env(
                paths, base_ref=f"origin/{self.config.default_branch}", base_sha=base_sha
            )
            issue_context = self._issue_context(issue)

            role = "investigate" if self._is_bug(labels) else "plan"
            self._agent(role, worktree.path, paths, context=issue_context, env=env)
            # plan.md / investigation.md are read by no deterministic program, only by the
            # contract worker. A worker that wrote nothing would otherwise pass silently and the
            # contract would be drawn from the issue alone (D-028).
            self._require_stage_note(paths.artifacts, role)
            contract_context = issue_context
            if role == "investigate":
                # The red loop is a precondition of the contract: the kernel executes the
                # proposed repro and refuses to continue unless it fails for the named reason.
                contract_context = issue_context + "\n\n" + self._observe_repro(
                    paths.artifacts, worktree.path
                )
            self._agent("contract", worktree.path, paths, context=contract_context, env=env)
            self._exec(
                [
                    "python", "scripts/factory_protocol.py", "contract",
                    "--input", str(paths.artifacts / "task-contract.raw.json"),
                    "--output", str(paths.artifacts / "task-contract.json"),
                    "--hash-output", str(paths.artifacts / "task-contract.sha256"),
                    "--issue", str(issue_number),
                ],
                cwd=worktree.path,
                env=env,
                credential_scope="none",
                timeout=120,
                transcript=paths.transcripts / "contract-gate.log",
            )
            self._lease_heartbeat("start", issue_number, "contract", paths, cwd=worktree.path)

            contract_hash = (paths.artifacts / "task-contract.sha256").read_text(
                encoding="utf-8"
            ).strip()
            # The worker used to receive only the hash and had to rediscover the task from
            # disk; that was the slowest stage of the first canary (D-020). The validated
            # contract is what the kernel is already holding, so it travels in the prompt. The
            # hash stays first: it is the binding the deterministic compiler re-verifies.
            self._agent(
                "context",
                worktree.path,
                paths,
                context=self._worker_brief(
                    paths, contract_hash=contract_hash, issue_context=issue_context
                ),
                env=env,
            )
            self._exec(
                [
                    "python", "scripts/factory_protocol.py", "context",
                    "--input", str(paths.artifacts / "context.raw.json"),
                    "--contract", str(paths.artifacts / "task-contract.json"),
                    "--output", str(paths.artifacts / "context.json"),
                ],
                cwd=worktree.path,
                env=env,
                credential_scope="none",
                timeout=180,
                transcript=paths.transcripts / "context-gate.log",
            )
            self._lease_heartbeat("touch", issue_number, "design-context", paths, cwd=worktree.path)

            self._agent(
                "architecture",
                worktree.path,
                paths,
                context=self._worker_brief(
                    paths, contract_hash=contract_hash, issue_context=issue_context,
                    include_design=True, include_applicable_policy=True,
                ),
                env=env,
            )
            self._exec(
                [
                    "python", "scripts/factory_architecture.py", "compile",
                    "--policy", ".factory/architecture.json",
                    "--input", str(paths.artifacts / "architecture-governor.raw.json"),
                    "--contract", str(paths.artifacts / "task-contract.json"),
                    "--context", str(paths.artifacts / "context.json"),
                    "--design", str(paths.artifacts / "design.json"),
                    "--output", str(paths.artifacts / "architecture-governor.json"),
                ],
                cwd=worktree.path,
                env=env,
                timeout=120,
                transcript=paths.transcripts / "architecture-gate.log",
            )
            governor = self._read_json(paths.artifacts / "architecture-governor.json")
            if governor.get("decision") != "proceed":
                required = governor.get("required_changes")
                details = "; ".join(required) if isinstance(required, list) else ""
                raise NeedsHuman(
                    f"architecture governor returned {governor.get('decision')}: {details}"
                )
            self._exec(
                [
                    "python", "scripts/factory_architecture.py", "scope",
                    "--governor", str(paths.artifacts / "architecture-governor.json"),
                    "--action", "implement",
                ],
                cwd=worktree.path,
                env=env,
                timeout=60,
            )

            # A fresh model process authors acceptance checkpoints; deterministic RED is authority.
            self._agent(
                "test_author",
                worktree.path,
                paths,
                context=self._worker_brief(
                    paths, contract_hash=contract_hash, issue_context=issue_context
                ) + self._deferred_symptom_brief(paths.artifacts),
                env=env,
            )
            self._exec(
                [
                    "python", "scripts/factory_proof.py", "red",
                    "--spec", str(paths.artifacts / "test-spec.json"),
                    "--output", str(paths.artifacts / "red-proof.json"),
                ],
                cwd=worktree.path,
                env=env,
                credential_scope="none",
                timeout=600,
                transcript=paths.transcripts / "red-gate.log",
            )
            # A deferred repro promised that the acceptance tests would show the symptom; RED
            # has now run them on the unchanged tree, so the promise is checked here, not
            # believed.
            self._close_deferred_repro(paths.artifacts)
            self._lease_heartbeat("touch", issue_number, "red", paths, cwd=worktree.path)

            self._agent(
                "implement",
                worktree.path,
                paths,
                context=f"Dispatched issue number is #{issue_number}. Build attempt is {attempt}.",
                env=env,
            )
            self._exec(
                [
                    "python", "scripts/factory_proof.py", "green",
                    "--proof", str(paths.artifacts / "red-proof.json"),
                    "--output", str(paths.artifacts / "green-proof.json"),
                ],
                cwd=worktree.path,
                env=env,
                credential_scope="none",
                timeout=600,
                transcript=paths.transcripts / "green-gate.log",
            )
            self._lease_heartbeat("touch", issue_number, "green", paths, cwd=worktree.path)

            self._review_and_repair(worktree, paths, env)
            self._agent(
                "conformance", worktree.path, paths,
                context=self._conformance_context(worktree.path, paths, env), env=env,
            )
            self._exec(
                [
                    "python", "scripts/factory_architecture.py", "conformance",
                    "--policy", ".factory/architecture.json",
                    "--input", str(paths.artifacts / "architecture-conformance.raw.json"),
                    "--contract", str(paths.artifacts / "task-contract.json"),
                    "--context", str(paths.artifacts / "context.json"),
                    "--design", str(paths.artifacts / "design.json"),
                    "--governor", str(paths.artifacts / "architecture-governor.json"),
                    "--output", str(paths.artifacts / "architecture-conformance.json"),
                    "--base-ref", f"origin/{self.config.default_branch}",
                ],
                cwd=worktree.path,
                env=env,
                timeout=180,
                transcript=paths.transcripts / "conformance-gate.log",
            )
            self._exec(
                [
                    "python", "scripts/factory_proof.py", "green",
                    "--proof", str(paths.artifacts / "red-proof.json"),
                    "--output", str(paths.artifacts / "final-green-proof.json"),
                ],
                cwd=worktree.path,
                env=env,
                credential_scope="none",
                timeout=600,
                transcript=paths.transcripts / "final-green-gate.log",
            )
            self._lease_heartbeat("touch", issue_number, "final-green", paths, cwd=worktree.path)

            self._exec(
                list(self.config.validation.quick_command),
                cwd=worktree.path,
                env=env,
                timeout=900,
                transcript=paths.transcripts / "quick-gate.log",
            )
            self._assert_clean(worktree.path)
            self.github.cwd = str(worktree.path)
            self.github.push_branch(branch)

            body = paths.root / "pr-body.md"
            body.write_text(
                render_pr_body(
                    issue_number, attempt, self._read_json(paths.artifacts / "task-contract.json")
                ),
                encoding="utf-8",
            )
            pr = self.github.create_pr(
                head=branch,
                base=self.config.default_branch,
                title=f"factory: {str(issue.get('title') or '').strip()}",
                body_file=body,
            )
            pr_number = int(pr["number"])
            self._attach_and_publish(paths, worktree.path, env, pr_number)
            self._lease_heartbeat(
                "finish", issue_number, "pr-handoff", paths, cwd=worktree.path, pr=pr_number
            )
            self._hand_to_review(pr_number)
            self.github.remove_issue_label(issue_number, self.config.labels["in_progress"])
            handed_off = True
            current_head = self._git("rev-parse", "HEAD", cwd=worktree.path)
            print(
                f"FACTORY_BUILD_OK issue=#{issue_number} attempt={attempt} "
                f"pr=#{pr_number} head={current_head}"
            )
            return pr_number
        except NeedsHuman as exc:
            self._mark_issue_human(issue_number, str(exc))
            raise
        except Exception as exc:
            self._mark_issue_human(issue_number, f"builder failed closed: {exc}")
            raise
        finally:
            self.github.cwd = str(self.repo_root)
            if handed_off:
                remove(self.repo_root, worktree)

    def _attach_and_publish(
        self, paths: RunPaths, cwd: Path, env: Mapping[str, str], pr_number: int
    ) -> None:
        """Bind the proven artifacts to the exact PR head: contract and final proof in the PR
        body, the provenance pack in a Git note on the head object.

        The two attach programs edit the PR body through gh, so they keep GitHub scope. None of
        the three runs a model-authored command. The build path and the stale-base re-head are
        the only callers; both push the head first, because every program here refuses unless
        the local HEAD is the PR head GitHub reports.
        """
        self._exec(
            [
                "python", "scripts/factory_protocol.py", "attach",
                "--contract", str(paths.artifacts / "task-contract.json"),
                "--pr", str(pr_number),
            ],
            cwd=cwd,
            env=env,
            credential_scope="github",
            timeout=120,
        )
        self._exec(
            [
                "python", "scripts/factory_proof.py", "attach",
                "--proof", str(paths.artifacts / "final-green-proof.json"),
                "--pr", str(pr_number),
            ],
            cwd=cwd,
            env=env,
            credential_scope="github",
            timeout=180,
        )
        base_sha = str(env.get("FACTORY_BASE_SHA") or "")
        if not re.fullmatch(r"[0-9a-f]{40,64}", base_sha):
            raise RuntimeError(
                "provenance publish needs the exact base the branch was cut from "
                "(FACTORY_BASE_SHA); it never reads the base branch tip"
            )
        self._exec(
            [
                "python", "scripts/factory_provenance.py", "publish",
                "--pr", str(pr_number),
                "--artifacts", str(paths.artifacts),
                "--base", base_sha,
            ],
            cwd=cwd,
            env=env,
            credential_scope="github",
            timeout=240,
            transcript=paths.transcripts / "provenance-publish.log",
        )

    def _hand_to_review(self, pr_number: int) -> None:
        """The one place a PR is handed to independent validation."""
        self.github.add_pr_label(pr_number, self.config.labels["needs_review"])

    def _two_axis_review(
        self, worktree: Worktree, paths: RunPaths, env: Mapping[str, str], *, context: str = ""
    ) -> dict[str, Any]:
        """Spec and Standards are judged by separate fresh processes; the kernel aggregates.

        Each axis writes its own artifact. The deterministic aggregator refuses a missing,
        malformed or mislabelled artifact and fails the review if either axis fails.
        """
        # Reviewers have no shell and cannot compute a diff; the kernel supplies it (D-028).
        diff_context = self._diff_context(worktree.path, env)
        full_context = (context.strip() + "\n\n" + diff_context) if context.strip() else diff_context
        for axis in AXES:
            self._agent(ROLE_FOR_AXIS[axis], worktree.path, paths, context=full_context, env=env)
        try:
            outcome = aggregate(read_axes(paths.artifacts, self._read_json))
        except ReviewInvalid as exc:
            raise NeedsHuman(f"review worker returned an invalid artifact: {exc}") from exc
        result = outcome.as_dict()
        self._write_json(paths.artifacts / "code-review.json", result)
        return result

    def _observe_repro(self, artifacts: Path, worktree: Path, *, runner=default_runner) -> str:
        """Execute the investigate worker's repro; refuse unless it goes red for the named reason.

        The repro is model-authored and runs inside the builder's worktree, which the contract
        worker reads next. The worktree must therefore be byte-identical before and after
        (tracked and untracked files alike): a repro that edits a source file and then prints
        the symptom would otherwise contaminate every later reasoning stage.
        """
        executed = (artifacts / REPRO_ARTIFACT).is_file()
        deferred = (artifacts / DEFERRED_ARTIFACT).is_file()
        if executed and deferred:
            raise NeedsHuman("bug repro refused: investigate wrote both repro.json and repro-deferred.json")
        if deferred:
            # No existing runner can fail on the unchanged tree for this bug. The worker names
            # the symptom the acceptance tests will show; `_close_deferred_repro` verifies it
            # against the RED proof, so the red loop still gates the build, two stages later.
            try:
                record = deferred_record(load_deferred(artifacts / DEFERRED_ARTIFACT))
            except ReproRefused as exc:
                raise NeedsHuman(f"bug repro refused: {exc}") from exc
            self._write_json(artifacts / OBSERVED_ARTIFACT, record)
            return "REPRO DEFERRED TO RED (no existing command can fail on this tree; the acceptance tests must show this symptom):\n" + json.dumps(
                {k: record[k] for k in ("reason", "seam", "expected_symptom")}, sort_keys=True,
            )
        try:
            repro = load_repro(artifacts / REPRO_ARTIFACT)
            before = self._git("status", "--porcelain", "--untracked-files=all", cwd=worktree)
            observation = execute(repro, worktree=worktree, runner=runner)
            after = self._git("status", "--porcelain", "--untracked-files=all", cwd=worktree)
        except ReproRefused as exc:
            raise NeedsHuman(f"bug repro refused: {exc}") from exc
        if before != after:
            raise NeedsHuman("bug repro refused: repro modified the worktree")
        record = observed_record(repro, observation)
        self._write_json(artifacts / OBSERVED_ARTIFACT, record)
        return "REPRO OBSERVED (kernel-executed, deterministic):\n" + json.dumps(
            {k: record[k] for k in ("argv", "cwd", "rc", "matched_symptom", "output_sha256")},
            sort_keys=True,
        )

    def _deferred_symptom_brief(self, artifacts: Path) -> str:
        """The string the RED gate will demand of at least one checkpoint, told to its author.

        `_close_deferred_repro` refuses the build unless some checkpoint's failing output carries
        the deferred repro's `expected_symptom`; until D-030 the test author, the only worker that
        shapes that output, was never shown it."""
        observed = artifacts / OBSERVED_ARTIFACT
        if not observed.is_file():
            return ""
        record = self._read_json(observed)
        if record.get("mode") != "deferred":
            return ""
        symptom = str(record.get("expected_symptom") or "").strip()
        if not symptom:
            return ""
        return (
            "\n\nDEFERRED REPRO SYMPTOM (at least one checkpoint's failing output must contain "
            "this string verbatim, case-insensitive, within its last "
            f"{RED_TAIL_CHARS} characters):\n{symptom}"
        )

    def _close_deferred_repro(self, artifacts: Path) -> None:
        """After RED, a deferred repro's promised symptom must appear in a checkpoint's output."""
        observed = artifacts / OBSERVED_ARTIFACT
        if not observed.is_file():
            return  # not a bug issue
        record = self._read_json(observed)
        if record.get("mode") != "deferred":
            return  # executed mode was already observed before the contract
        try:
            match = verify_deferred_in_red(record, self._read_json(artifacts / "red-proof.json"))
        except ReproRefused as exc:
            raise NeedsHuman(f"bug repro refused: {exc}") from exc
        record["observed_in_red"] = match
        self._write_json(observed, record)

    def _review_and_repair(
        self, worktree: Worktree, paths: RunPaths, env: Mapping[str, str]
    ) -> None:
        review = self._two_axis_review(worktree, paths, env)
        if review["verdict"] == "pass":
            return
        self._agent(
            "repair",
            worktree.path,
            paths,
            context="Blocking review JSON:\n" + json.dumps(review, sort_keys=True),
            env=env,
        )
        self._exec(
            [
                "python", "scripts/factory_proof.py", "green",
                "--proof", str(paths.artifacts / "red-proof.json"),
                "--output", str(paths.artifacts / "green-after-repair.json"),
            ],
            cwd=worktree.path,
            env=env,
            credential_scope="none",
            timeout=600,
        )
        second = self._two_axis_review(
            worktree, paths, env, context="This is the fresh post-repair review."
        )
        if second["verdict"] != "pass":
            raise NeedsHuman("fresh post-repair review still contains blockers")

    # ---------- independent PR validator / merge authority ----------

    def validate_pr(self, pr_number: int, *, merge: bool = True) -> Path:
        self.check_stop()
        info = self.github.pr(pr_number, holdout_safe=True)
        if info.get("state") != "OPEN":
            raise NeedsHuman(f"PR #{pr_number} is not open")
        labels = self.github.labels(info)
        if self.config.labels["needs_review"] not in labels:
            raise NeedsHuman(f"PR #{pr_number} is not marked {self.config.labels['needs_review']}")
        head = str(info.get("headRefOid") or "")
        base = str(info.get("baseRefOid") or "")
        if not re.fullmatch(r"[0-9a-f]{40,64}", head) or not re.fullmatch(
            r"[0-9a-f]{40,64}", base
        ):
            raise NeedsHuman("PR lacks exact Git object identities")

        self._git("fetch", "origin", str(info["headRefName"]), self.config.default_branch)
        run_id = f"pr-{pr_number}-{uuid.uuid4().hex[:12]}"
        paths = RunPaths.create(self.config.runtime.work_root, run_id)
        worktree = create_detached(
            self.repo_root,
            head,
            base_dir=self.config.runtime.work_root / "validator-worktrees",
        )
        linked_issue = self._linked_issue_number(str(info.get("body") or ""))
        # The stage the validator is in when it refuses is what turns a refusal into a reason
        # code (factory_kernel/refusal.py); a bare exception class never could.
        stage = "security_guard"
        try:
            self._prepare_worktree(worktree.path, paths)
            env = self._run_env(paths, base_ref=base)
            # The guard is base-anchored: it runs from the kernel's own main checkout and reads
            # the PR head as data. The worktree at the PR head is where proposed code is tested,
            # never where the program that judges the PR comes from.
            self._exec(
                [
                    "python", "scripts/factory_security.py", "--pr", str(pr_number),
                    "--trusted-base", "--expect-head", head,
                    "--output", str(paths.artifacts / "security.json"),
                ],
                cwd=self.repo_root,
                env=env,
                credential_scope="github",
                timeout=180,
                transcript=paths.transcripts / "security.log",
            )

            stage = "attached_evidence"
            contract, proof = self._extract_attached(str(info.get("body") or ""))
            contract_issue = contract.get("issue")
            if isinstance(contract_issue, Mapping) and isinstance(contract_issue.get("number"), int):
                linked_issue = int(contract_issue["number"])
            self._write_json(paths.artifacts / "attached-contract.json", contract)
            self._write_json(paths.artifacts / "attached-proof.json", proof)
            patch = self._git("diff", "--binary", f"{base}...{head}", cwd=worktree.path)
            changed = self._git(
                "diff", "--name-only", f"{base}...{head}", cwd=worktree.path
            ).splitlines()
            policy = self._read_json(worktree.path / ".factory/architecture.json")

            holdout_context = {
                "contract": contract,
                "changed_files": sorted(x for x in changed if x),
                "diff_sha256": hashlib.sha256(patch.encode()).hexdigest(),
                "diff": patch,
                "proof_summary": {
                    "test_commit": proof.get("test_commit"),
                    "green_commit": proof.get("green_commit"),
                    "green_results": proof.get("green_results"),
                },
            }
            stage = "code_holdout"
            verdict = self._run_blinded_holdout(paths, holdout_context)
            if verdict.get("verdict") != "pass":
                raise NeedsHuman("blinded holdout rejected PR")

            stage = "provenance"
            # GitHub's baseRefOid is the current tip of main. The pack declares the base the
            # branch was actually cut from; when the two differ, main moved under the PR, and
            # that is a stale_base refusal here, at the earliest point it can be known, rather
            # than one gate later in the evidence spine (D-042).
            if isinstance(linked_issue, int):
                pack_base = self._pack_base(paths, head=head, issue=linked_issue)
                if pack_base != base:
                    raise ToolRefused(
                        ["scripts/factory_provenance.py", "fetch"],
                        rc=1,
                        output=(
                            f"builder provenance was built from a different base: pack "
                            f"{pack_base}, current {base}; main moved under the PR"
                        ),
                    )
            pack = self._builder_pack(paths, head=head, base=base, issue=linked_issue)
            stage = "architecture_holdout"
            architecture_holdout = self._run_architecture_holdout(
                paths,
                pack=pack,
                policy=policy,
                changed_files=sorted(x for x in changed if x),
                diff=patch,
            )
            stage = "certifier"
            self._certify_precode_claims(
                paths, pack=pack, head=head, base=base, issue=linked_issue
            )
            stage = "evidence_spine"
            self._write_json(
                paths.artifacts / "validator-verdict.json",
                {
                    "version": "1.0",
                    "verdict": "approve",
                    "holdout_sha256": self._json_sha(verdict),
                },
            )

            self._exec(
                [
                    "python", "scripts/factory_evidence.py", "--pr", str(pr_number),
                    "--verdict", str(paths.artifacts / "validator-verdict.json"),
                    "--architecture-verdict", str(architecture_holdout),
                    "--output", str(paths.artifacts / "evidence-bundle.json"),
                ],
                cwd=worktree.path,
                env=env,
                credential_scope="github+validation",
                timeout=2400,
                transcript=paths.transcripts / "evidence.log",
            )
            stage = "merge_preauth"
            self._exec(
                [
                    "python", "harness/merge_verify.py", "pre", "--pr", str(pr_number),
                    "--evidence", str(paths.artifacts / "evidence-bundle.json"),
                    "--output", str(paths.artifacts / "merge-authorization.json"),
                ],
                cwd=worktree.path,
                env=env,
                credential_scope="github",
                timeout=180,
                transcript=paths.transcripts / "merge-pre.log",
            )
            if not merge:
                print(f"FACTORY_VALIDATED pr=#{pr_number} head={head} merge=disabled")
                return paths.artifacts / "evidence-bundle.json"

            # Irreversible action: stop state and expected head are both rechecked immediately.
            self.check_stop()
            self.github.cwd = str(worktree.path)
            self.github.merge_squash(pr_number, expected_head=head)
            try:
                self._exec(
                    [
                        "python", "harness/merge_verify.py", "post", "--pr", str(pr_number),
                        "--evidence", str(paths.artifacts / "evidence-bundle.json"),
                        "--authorization", str(paths.artifacts / "merge-authorization.json"),
                        "--output", str(paths.artifacts / "merge-verification.json"),
                    ],
                    cwd=worktree.path,
                    env=env,
                    credential_scope="github",
                    timeout=240,
                    transcript=paths.transcripts / "merge-post.log",
                )
            except Exception as exc:  # the merge is already on main; this is an incident
                raise PostMergeUnverified(
                    f"post-merge verification failed for #{pr_number}: {exc}"
                ) from exc
            print(f"FACTORY_MERGED_VERIFIED pr=#{pr_number} evidenced_head={head}")
            return paths.artifacts / "merge-verification.json"
        except PostMergeUnverified as exc:
            self._raise_post_merge_incident(pr_number, linked_issue, exc)
            raise
        except Exception as exc:
            self._record_validation_failure(
                pr_number, linked_issue, exc, stage=stage, paths=paths, head=head, base=base
            )
            raise
        finally:
            self.github.cwd = str(self.repo_root)
            try:
                remove(self.repo_root, worktree)
            except RuntimeError:
                pass

    # ---------- model-free re-head ----------

    REHEAD_GREEN_LOG = "rehead-green-gate.log"
    REHEAD_FINAL_GREEN_LOG = "rehead-final-green-gate.log"

    def rehead_pr(self, pr_number: int) -> str:
        """Move a refused PR onto current main without a model, then hand it back to validation.

        This is not a repair. It runs only for a `stale_base` refusal -- the three programs that
        say main moved under the PR -- and it changes nothing the builder was certified on: the
        contract, context, design, governor verdict and RED-hashed acceptance tests travel
        through the verified provenance pack and must be byte-identical at the new head. What is
        recomputed is exactly what a new head invalidates: GREEN, impact, drift, conformance, the
        quick gate, the attached proof and the provenance note. Validation then runs in full from
        the new head and reuses nothing. One re-head per PR; a second stale refusal escalates.
        """
        self.check_stop()
        info = self.github.pr(pr_number, holdout_safe=True)
        if info.get("state") != "OPEN":
            raise NeedsHuman(f"PR #{pr_number} is not open")
        labels = self.github.labels(info)
        if self.config.labels["needs_fix"] not in labels:
            raise NeedsHuman(f"PR #{pr_number} is not marked {self.config.labels['needs_fix']}")
        comments = self.github.pr_comments(pr_number)
        if not rehead_eligible(comments):
            raise NeedsHuman(
                f"PR #{pr_number} is not a first stale-base refusal; re-head is not a repair"
            )
        head = str(info.get("headRefOid") or "")
        branch = str(info.get("headRefName") or "")
        if not re.fullmatch(r"[0-9a-f]{40,64}", head) or not branch or branch.startswith("-"):
            raise NeedsHuman("PR lacks exact Git object identities")
        linked_issue = self._linked_issue_number(str(info.get("body") or ""))
        default = self.config.default_branch

        self._git("fetch", "origin", branch, default)
        run_id = f"rehead-{pr_number}-{uuid.uuid4().hex[:12]}"
        paths = RunPaths.create(self.config.runtime.work_root, run_id)
        if not isinstance(linked_issue, int):
            raise NeedsHuman(f"PR #{pr_number} does not link an issue")
        # The base the pack was built from is read from the pack, not recomputed: the pack
        # declares it, the kernel verifies it is an ancestor of the head, and fetch then holds
        # the pack to exactly that binding (D-042).
        old_base = self._pack_base(paths, head=head, issue=linked_issue)
        # Blinded like a build worktree: the conformance worker runs here and must not see the
        # holdout. Validation, which does need it, starts its own unblinded worktree later.
        worktree = create_detached(
            self.repo_root,
            head,
            base_dir=self.config.runtime.work_root / "rehead-worktrees",
            blind=BUILDER_BLIND_PATHS,
        )
        try:
            self._git("checkout", "-b", branch, cwd=worktree.path)
            self._prepare_worktree(worktree.path, paths)
            pack = self._builder_pack(paths, head=head, base=old_base, issue=linked_issue)
            self._materialize_builder_artifacts(pack, paths.artifacts)

            try:
                self._git(*KERNEL_COMMIT_ARGS, "rebase", f"origin/{default}", cwd=worktree.path)
            except RuntimeError as exc:
                try:
                    self._git("rebase", "--abort", cwd=worktree.path)
                except RuntimeError:
                    pass
                raise NeedsHuman(f"rebase conflict; re-head needs a human: {exc}") from exc
            new_base = self._git("rev-parse", f"origin/{default}", cwd=worktree.path)
            new_head = self._git("rev-parse", "HEAD", cwd=worktree.path)
            self._verify_red_unchanged(pack, worktree.path)

            # The rebased branch is now cut from new_base; that, and only that, is what the
            # republished pack records (D-042).
            env = self._run_env(paths, base_ref=f"origin/{default}", base_sha=new_base)
            self._rehead_green(paths, worktree.path, env, output="green-proof.json", log=self.REHEAD_GREEN_LOG)
            self._agent(
                "conformance", worktree.path, paths,
                context=self._conformance_context(worktree.path, paths, env), env=env,
            )
            self._exec(
                [
                    "python", "scripts/factory_architecture.py", "conformance",
                    "--policy", ".factory/architecture.json",
                    "--input", str(paths.artifacts / "architecture-conformance.raw.json"),
                    "--contract", str(paths.artifacts / "task-contract.json"),
                    "--context", str(paths.artifacts / "context.json"),
                    "--design", str(paths.artifacts / "design.json"),
                    "--governor", str(paths.artifacts / "architecture-governor.json"),
                    "--output", str(paths.artifacts / "architecture-conformance.json"),
                    "--base-ref", f"origin/{default}",
                ],
                cwd=worktree.path,
                env=env,
                timeout=180,
                transcript=paths.transcripts / "rehead-conformance-gate.log",
            )
            self._rehead_green(
                paths, worktree.path, env, output="final-green-proof.json", log=self.REHEAD_FINAL_GREEN_LOG
            )
            self._exec(
                list(self.config.validation.quick_command),
                cwd=worktree.path,
                env=env,
                timeout=900,
                transcript=paths.transcripts / "rehead-quick-gate.log",
            )
            self._assert_clean(worktree.path)

            # The one legitimate non-fast-forward push in the kernel: the branch was rebased, so
            # its history is rewritten by construction. The lease names the head that was
            # judged, so a push that would overwrite anything else is refused by the remote.
            self.github.cwd = str(worktree.path)
            self.github.push_branch(branch, force_with_lease=head)
            self._attach_and_publish(paths, worktree.path, env, pr_number)
            marker = render_rehead_marker({
                "version": "1.0", "pr": pr_number, "old_head": head, "new_head": new_head,
                "old_base": old_base, "new_base": new_base, "timestamp": _utc_now(),
            })
            self.github.comment_pr(
                pr_number,
                marker + f"\nDark Factory re-headed this PR onto `{new_base}` without a model "
                f"(old head `{head}`, new head `{new_head}`). The certified contract, design and "
                "RED tests are unchanged; GREEN, conformance and the quick gate were re-run at the "
                "new head. Independent validation now runs again in full.",
            )
            self.github.remove_pr_label(pr_number, self.config.labels["needs_fix"])
            self._hand_to_review(pr_number)
            print(f"FACTORY_REHEAD_OK pr=#{pr_number} old_head={head} new_head={new_head} base={new_base}")
            return new_head
        except NeedsHuman as exc:
            self._mark_pr_human(pr_number, f"re-head stopped: {exc}")
            raise
        except Exception as exc:
            self._mark_pr_human(pr_number, f"re-head failed closed: {exc}")
            raise
        finally:
            self.github.cwd = str(self.repo_root)
            try:
                remove(self.repo_root, worktree, force=True, require_clean=False)
            except RuntimeError:
                pass

    # ---------- resume: finish a pushed-but-unpublished PR from its uploaded artifacts ----------

    # The factory opens PRs with the Actions token. GitHub's REST API reports that actor as
    # login `github-actions[bot]`, type `Bot`; the kernel already commits under that exact name
    # (worker_policy.KERNEL_COMMIT_NAME), so there is one spelling of the factory's identity.
    # The GraphQL spelling `app/github-actions` (what `gh pr view --json author` returns) is
    # never compared: canary run 33927106276 refused the factory's own PR #74 on it.
    FACTORY_PR_AUTHOR = {"login": KERNEL_COMMIT_NAME, "type": "Bot"}

    def resume_pr(self, pr_number: int, artifacts_dir: Path) -> str:
        """Finish a build that pushed its branch and opened its PR, then died before the
        attach/publish/handoff steps completed.

        Nothing is rebuilt and no model runs. The artifacts the ephemeral runner uploaded are the
        only honest input: every builder artifact must be present, the final GREEN proof must be
        bound to the exact PR head, and every RED-hashed acceptance test must be byte-identical
        at that head. Then the same `_attach_and_publish` the build path uses re-binds the
        contract, design, proof and provenance note to the head (each attach program replaces an
        existing block of its kind rather than duplicating it), the pr-handoff lease finishes,
        and the PR is handed to independent validation, which runs in full and reuses nothing.

        A human invokes this after retrieving the run artifacts; it is not a dispatch action.
        One resume per PR: a second attempt escalates, because a PR that cannot be finished from
        its own artifacts twice is not a recoverable run.
        """
        self.check_stop()
        artifacts_dir = Path(artifacts_dir).resolve()
        if not artifacts_dir.is_dir():
            raise NeedsHuman(f"resume artifacts directory does not exist: {artifacts_dir}")
        info = self.github.pr(pr_number, holdout_safe=True)
        if info.get("state") != "OPEN":
            raise NeedsHuman(f"PR #{pr_number} is not open")
        author = self.github.pr_author(pr_number)
        if author.get("type") != self.FACTORY_PR_AUTHOR["type"] or author.get("login") != self.FACTORY_PR_AUTHOR["login"]:
            raise NeedsHuman(
                f"PR #{pr_number} was not opened by the factory "
                f"(author {author.get('login')!r}, type {author.get('type')!r}); "
                "only an autonomous PR can be resumed"
            )
        head = str(info.get("headRefOid") or "")
        base = str(info.get("baseRefOid") or "")
        branch = str(info.get("headRefName") or "")
        if (
            not re.fullmatch(r"[0-9a-f]{40,64}", head)
            or not re.fullmatch(r"[0-9a-f]{40,64}", base)
            or not branch
            or branch.startswith("-")
        ):
            raise NeedsHuman("PR lacks exact Git object identities")
        linked_issue = self._linked_issue_number(str(info.get("body") or ""))
        if not isinstance(linked_issue, int):
            raise NeedsHuman(f"PR #{pr_number} does not link an issue")
        issue = self.github.issue(linked_issue)
        issue_labels = self.github.labels(issue)
        if not issue_labels & {self.config.labels["needs_human"], self.config.labels["accepted"]}:
            raise NeedsHuman(
                f"issue #{linked_issue} is neither {self.config.labels['needs_human']} nor "
                f"{self.config.labels['accepted']}; refusing to resume"
            )
        if resume_count(self.github.pr_comments(pr_number)) > 0:
            raise NeedsHuman(f"PR #{pr_number} was already resumed once; a second resume needs a human")

        self._verify_resume_artifacts(artifacts_dir, head=head)

        self._git("fetch", "origin", branch, self.config.default_branch)
        run_id = f"resume-{pr_number}-{uuid.uuid4().hex[:12]}"
        paths = RunPaths.create(self.config.runtime.work_root, run_id)
        # Blinded like a build worktree: no model runs here, but the attach programs run in it
        # and the worktree must look exactly like the one that produced the artifacts.
        worktree = create_detached(
            self.repo_root,
            head,
            base_dir=self.config.runtime.work_root / "resume-worktrees",
            blind=BUILDER_BLIND_PATHS,
        )
        try:
            self._git("checkout", "-b", branch, cwd=worktree.path)
            self._prepare_worktree(worktree.path, paths)
            self._copy_resume_artifacts(artifacts_dir, paths.artifacts)
            self._verify_red_unchanged(
                {"artifacts": {"red-proof": {"content": self._read_json(paths.artifacts / "red-proof.json")}}},
                worktree.path,
            )
            local = self._git("rev-parse", "HEAD", cwd=worktree.path)
            if local != head:
                raise NeedsHuman(f"resume worktree HEAD {local} is not the PR head {head}")

            # A resumed build never re-reads the base branch tip either: its base is the one
            # the uploaded final proof was cut from, the merge-base of the head and the base
            # branch as they were when the build ran, which the PR head's history still holds.
            cut_base = self._git("merge-base", f"origin/{self.config.default_branch}", head)
            if not self._is_ancestor(cut_base, head):
                raise NeedsHuman(f"resume cannot establish the base {head} was cut from")
            env = self._run_env(
                paths, base_ref=f"origin/{self.config.default_branch}", base_sha=cut_base
            )
            self.github.cwd = str(worktree.path)
            self._attach_and_publish(paths, worktree.path, env, pr_number)
            self._lease_heartbeat(
                "finish", linked_issue, "pr-handoff", paths, cwd=worktree.path, pr=pr_number
            )
            marker = render_resume_marker({
                "version": "1.0", "pr": pr_number, "head": head, "base": base,
                "issue": linked_issue, "timestamp": _utc_now(),
            })
            self.github.comment_pr(
                pr_number,
                marker + f"\nDark Factory resumed this PR from its uploaded build artifacts at head "
                f"`{head}`: the attached contract, design and proof were re-bound and the provenance "
                "note published without rebuilding or running a model. Independent validation now "
                "runs in full.",
            )
            self._hand_to_review(pr_number)
            # The failed run escalated the issue; put it back where a live handed-off build
            # leaves it (accepted, not in progress) so validation's outcome moves it next.
            self.github.remove_issue_label(linked_issue, self.config.labels["needs_human"])
            self.github.remove_issue_label(linked_issue, self.config.labels["in_progress"])
            self.github.add_issue_label(linked_issue, self.config.labels["accepted"])
            print(f"FACTORY_RESUMED pr=#{pr_number} head={head} issue=#{linked_issue}")
            return head
        except NeedsHuman as exc:
            self._mark_pr_human(pr_number, f"resume stopped: {exc}")
            raise
        except Exception as exc:
            self._mark_pr_human(pr_number, f"resume failed closed: {exc}")
            raise
        finally:
            self.github.cwd = str(self.repo_root)
            try:
                remove(self.repo_root, worktree, force=True, require_clean=False)
            except RuntimeError:
                pass

    def _verify_resume_artifacts(self, artifacts_dir: Path, *, head: str) -> None:
        """The supplied artifacts must be complete and belong to exactly this PR head."""
        missing = [
            rel for claim_id, rel in BUILDER_ARTIFACTS
            if claim_id != "architecture-policy" and not (artifacts_dir / rel).is_file()
        ]
        if missing:
            raise NeedsHuman(f"resume artifacts are incomplete; missing {sorted(missing)}")
        proof = self._read_json(artifacts_dir / "final-green-proof.json")
        if str(proof.get("green_commit") or "") != head:
            raise NeedsHuman(
                f"final GREEN proof is bound to {proof.get('green_commit')!r}, not the PR head {head}; "
                "these artifacts belong to a different build"
            )
        red = self._read_json(artifacts_dir / "red-proof.json")
        if not isinstance(red.get("files"), Mapping) or not red["files"]:
            raise NeedsHuman("RED proof in the resume artifacts has no immutable file map")

    @staticmethod
    def _copy_resume_artifacts(source: Path, target: Path) -> None:
        for claim_id, rel in BUILDER_ARTIFACTS:
            if claim_id == "architecture-policy":
                continue
            data = (source / rel).read_bytes()
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_bytes(data)
        # The lease record lets the pr-handoff heartbeat PATCH the original comment; without it
        # the heartbeat program starts a fresh lease, which is also correct.
        lease = source / "factory-lease.json"
        if lease.is_file():
            (target / "factory-lease.json").write_bytes(lease.read_bytes())

    def _rehead_green(
        self, paths: RunPaths, cwd: Path, env: Mapping[str, str], *, output: str, log: str
    ) -> None:
        self._exec(
            [
                "python", "scripts/factory_proof.py", "green",
                "--proof", str(paths.artifacts / "red-proof.json"),
                "--output", str(paths.artifacts / output),
            ],
            cwd=cwd,
            env=env,
            credential_scope="none",
            timeout=600,
            transcript=paths.transcripts / log,
        )

    @staticmethod
    def _materialize_builder_artifacts(pack: Mapping[str, Any], artifacts: Path) -> None:
        """Write the verified pack back under the file names the builder programs expect."""
        records = pack["artifacts"]
        for claim_id, rel in BUILDER_ARTIFACTS:
            if claim_id == "architecture-policy":
                continue  # read from the checkout's trust root, never from a pack
            target = artifacts / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(canonical_bytes(records[claim_id]["content"]))

    @staticmethod
    def _verify_red_unchanged(pack: Mapping[str, Any], worktree: Path) -> None:
        """The RED-hashed acceptance tests must be byte-identical at the re-headed tip."""
        files = pack["artifacts"]["red-proof"]["content"].get("files")
        if not isinstance(files, Mapping) or not files:
            raise NeedsHuman("RED proof in the provenance pack has no immutable file map")
        for rel, expected in files.items():
            target = worktree / str(rel)
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else ""
            if actual != expected:
                raise NeedsHuman(f"RED-hashed acceptance test differs after rebase: {rel}")

    def _mark_pr_human(self, pr_number: int, reason: str) -> None:
        try:
            self.github.cwd = str(self.repo_root)
            self.github.add_pr_label(pr_number, self.config.labels["needs_human"])
            self.github.comment_pr(pr_number, "Dark Factory " + reason[:1500])
        except Exception:
            pass

    # ---------- independent holdouts ----------

    def _run_blinded_holdout(
        self, paths: RunPaths, context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        with tempfile.TemporaryDirectory(prefix="dark-factory-holdout-") as tmp:
            prompt = prompt_text(
                self.config.prompt_path("holdout", self.repo_root),
                preamble="This invocation is intentionally isolated from the repository checkout.",
                context="HOLDOUT INPUT:\n" + json.dumps(context, sort_keys=True),
            )
            result = self.provider.run(
                AgentRequest(
                    role="holdout",
                    prompt=prompt,
                    cwd=tmp,
                    model=self.config.provider.model,
                    environment={},
                    structured_schema={"type": "object"},
                    max_turns=max_turns("holdout"),
                )
            )
            value = result.structured_output
            if not isinstance(value, Mapping) or value.get("version") != "1.0":
                raise NeedsHuman("blinded holdout returned invalid JSON")
            if not isinstance(value.get("findings"), list):
                raise NeedsHuman("blinded holdout findings are invalid")
            self._write_json(paths.artifacts / "holdout.json", dict(value))
            return value

    # Post-code architecture conformance is a *different* claim about a *different* artifact.
    # It is never a substitute for independently certifying the design the builder proposed or
    # the governor decision that authorized it, so those get their own blinded authorities.
    PRECODE_CERTIFIERS: tuple[tuple[str, str], ...] = (
        # The contract certifier is the only one that sees the issue, and it sees no diff: it
        # judges whether the compiled contract faithfully captures what was asked, which is the
        # opposite question from whether the implementation satisfies the contract.
        ("contract", "contract-certifier"),
        ("design", "design-certifier"),
        ("architecture-governor", "governor-certifier"),
    )

    CERTIFIER_QUESTIONS: dict[str, str] = {
        "contract": (
            "Judge only whether the compiled contract is a faithful, complete and correctly "
            "scoped capture of the supplied issue: every requirement the issue states is "
            "represented by an acceptance criterion, nothing is silently dropped or narrowed, "
            "and nothing is invented beyond what the issue asks. You are not reviewing any "
            "design or implementation, and you have deliberately not been shown one."
        ),
        "design": (
            "Judge only whether the supplied design is a sound, complete and policy-consistent "
            "answer to the supplied contract and context. You are not reviewing any "
            "implementation, and you have deliberately not been shown one."
        ),
        "architecture-governor": (
            "Judge only whether the governor's decision is warranted by the supplied policy, "
            "contract, context and design: whether the applicable principles, migrations and "
            "debts were correctly identified, and whether proceeding is justified rather than "
            "requiring a veto or a prefactor. You are not reviewing any implementation, and you "
            "have deliberately not been shown one."
        ),
    }

    def _pack_base(self, paths: RunPaths, *, head: str, issue: int) -> str:
        """The base the pack itself declares for `head`, verified as an ancestor of it.

        Consumers read the binding they are about to verify; none recomputes the base from the
        current branch tip. The first production re-head guessed it with merge-base and could
        not match a pack that had recorded the wrong base; a pack whose base is not an ancestor
        of its head is refused here as well as at publish (D-042).
        """
        peek_log = paths.transcripts / "provenance-peek.log"
        output = self._exec(
            [
                "python", "scripts/factory_provenance.py", "peek", "--head", head,
            ],
            cwd=self.repo_root,
            env={"FACTORY_REPO": self.config.repository},
            credential_scope="github",
            timeout=120,
            transcript=peek_log,
        )
        line = next((ln for ln in output.splitlines() if ln.startswith("{")), "")
        try:
            identity = json.loads(line)
        except json.JSONDecodeError as exc:
            raise NeedsHuman("builder provenance note identity is unreadable") from exc
        base = str(identity.get("base_sha") or "")
        if identity.get("head_sha") != head or identity.get("issue") != issue:
            raise NeedsHuman("builder provenance note is bound to a different head or issue")
        if not re.fullmatch(r"[0-9a-f]{40,64}", base):
            raise NeedsHuman("builder provenance note declares no exact base")
        if not self._is_ancestor(base, head):
            raise NeedsHuman(
                f"builder provenance base {base} is not an ancestor of its head {head}"
            )
        return base

    def _is_ancestor(self, base: str, head: str) -> bool:
        try:
            self._git("merge-base", "--is-ancestor", base, head)
        except RuntimeError:
            return False
        return True

    def _builder_pack(
        self, paths: RunPaths, *, head: str, base: str, issue: int | None
    ) -> Mapping[str, Any]:
        """Fetch and re-verify the exact-head builder provenance the authorities are judged on.

        `base` is what the caller expects; a pack recording a different base is refused with
        the producer string `refusal.py` classifies as `stale_base`."""
        if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
            raise NeedsHuman("cannot certify claims without a linked issue number")
        pack_dir = paths.artifacts / "spine"
        self._exec(
            [
                "python", "scripts/factory_provenance.py", "fetch",
                "--head", head, "--base", base, "--issue", str(issue),
                "--output-dir", str(pack_dir),
            ],
            cwd=self.repo_root,
            env={"FACTORY_REPO": self.config.repository},
            credential_scope="github",
            timeout=240,
            transcript=paths.transcripts / "provenance-fetch.log",
        )
        try:
            return verify_pack(
                self._read_json(pack_dir / "builder-provenance.json"),
                expected_head_sha=head,
                expected_base_sha=base,
                expected_issue=issue,
                is_ancestor=self._is_ancestor,
            )
        except ValueError as exc:
            raise NeedsHuman(f"builder provenance is unusable for certification: {exc}") from exc

    def _certify_precode_claims(
        self,
        paths: RunPaths,
        *,
        pack: Mapping[str, Any],
        head: str,
        base: str,
        issue: int | None,
    ) -> None:
        """Issue genuinely independent certificates for the pre-code claims.

        The builder cannot reach these authorities: they run on the validator, blinded to the
        builder transcript, and the kernel -- not the model -- fills in every binding.
        """
        if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
            raise NeedsHuman("cannot certify pre-code claims without a linked issue number")
        hashes = {claim: record["sha256"] for claim, record in pack["artifacts"].items()}
        values = {claim: record["content"] for claim, record in pack["artifacts"].items()}
        target = paths.artifacts / "independent"
        target.mkdir(parents=True, exist_ok=True)
        for claim_id, role in self.PRECODE_CERTIFIERS:
            # What the authority is shown comes from the protected registry, which refuses any
            # entry that is not shown every artifact its claim is bound to.
            seen, extra = authority_inputs((claim_id,))
            payload: dict[str, Any] = {key: values[key] for key in seen}
            if "issue" in extra:
                payload["issue"] = self._certification_issue(issue)
            judgement = self._run_precode_certifier(
                paths, claim_id=claim_id, role=role, inputs=payload
            )
            certificate = build_certificate(
                claim_id=claim_id,
                claim_hashes=hashes,
                head_sha=head,
                base_sha=base,
                judgement=judgement,
            )
            # Fail closed here as well as at closure: a certifier that echoed a builder artifact
            # back must never be written to disk as if it were independent evidence.
            verify_certificate(
                certificate,
                claim_id=claim_id,
                claim_hashes=hashes,
                builder_artifact_hashes=hashes,
                head_sha=head,
                base_sha=base,
            )
            self._write_json(target / f"{claim_id}.json", certificate)

    def _certification_issue(self, issue: int) -> dict[str, Any]:
        """The issue as the contract certifier sees it: what was asked, and nothing else."""
        record = self.github.issue(issue)
        return {
            "number": issue,
            "title": str(record.get("title") or ""),
            "body": str(record.get("body") or ""),
        }

    def _run_precode_certifier(
        self, paths: RunPaths, *, claim_id: str, role: str, inputs: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        skeleton = json.dumps(
            {
                "version": "1.0",
                "certifies": claim_id,
                "verdict": "pass|fail",
                "findings": [{"severity": "critical|high|medium|low", "description": "..."}],
            },
            sort_keys=True,
        )
        suffix = (
            "Return ONLY this JSON object, with `certifies` exactly as shown: "
            + skeleton
            + ". " + self.CERTIFIER_QUESTIONS[claim_id]
        )
        with tempfile.TemporaryDirectory(prefix=f"dark-factory-{role}-") as tmp:
            prompt = prompt_text(
                self.config.prompt_path("holdout", self.repo_root),
                preamble=(
                    f"You are the independent {claim_id} certifier, deliberately isolated from "
                    "the builder's reasoning and from the repository checkout. " + suffix
                ),
                context=(
                    f"{claim_id} CERTIFICATION INPUT:\n" + json.dumps(inputs, sort_keys=True)
                ),
            )
            result = self.provider.run(
                AgentRequest(
                    role=role,
                    prompt=prompt,
                    cwd=tmp,
                    model=self.config.provider.model,
                    environment={},
                    structured_schema={"type": "object"},
                    max_turns=max_turns(role),
                )
            )
            value = result.structured_output
            if not isinstance(value, Mapping) or value.get("version") != "1.0":
                raise NeedsHuman(f"independent {claim_id} certifier returned invalid JSON")
            if not isinstance(value.get("findings"), list):
                raise NeedsHuman(f"independent {claim_id} certifier findings are invalid")
            if value.get("certifies") != claim_id:
                raise NeedsHuman(
                    f"independent {claim_id} certifier did not declare its own subject"
                )
            if value.get("verdict") != "pass":
                raise NeedsHuman(f"independent {claim_id} certifier rejected the {claim_id} claim")
            self._write_json(paths.artifacts / f"{claim_id}-certification.json", dict(value))
            return value

    def _run_architecture_holdout(
        self,
        paths: RunPaths,
        *,
        pack: Mapping[str, Any],
        policy: Mapping[str, Any],
        changed_files: list[str],
        diff: str,
    ) -> Path:
        """One architecture authority, shown everything both of its claims are bound to.

        It certifies architecture-drift and architecture-conformance. Conformance asserts the
        implementation conforms to the design and the governor's decision, so an authority shown
        only the policy and the diff was never competent to certify it. The registry now says what
        this authority must see, and the input is built from that rather than from a hand-kept
        literal that could drift away from the claims.
        """
        values = {claim: record["content"] for claim, record in pack["artifacts"].items()}
        seen, _extra = authority_inputs(claims_for_authority("architecture-holdout"))
        context: dict[str, Any] = {
            name: policy if name == "architecture-policy" else values[name] for name in seen
        }
        context["changed_files"] = changed_files
        context["diff"] = diff
        # The evidence verifier requires the ID sets computed from changed_files; hand the
        # holdout the sets rather than the rule (D-030). The verifier still recomputes them.
        context["applicable_policy_ids"] = self._applicable_policy_ids(paths, files=changed_files)
        suffix = (
            "Return ONLY JSON with version 1.0; verdict pass|fail; convergence "
            "improves|neutral|regresses; principles, migrations, debts arrays containing exactly "
            "the policy IDs in applicable_policy_ids (copy them verbatim); and findings as objects "
            "with severity critical|high|medium|low and non-empty description."
        )
        with tempfile.TemporaryDirectory(prefix="dark-factory-arch-holdout-") as tmp:
            prompt = prompt_text(
                self.config.prompt_path("holdout", self.repo_root),
                preamble="You are the independent architecture holdout. " + suffix,
                context="ARCHITECTURE HOLDOUT INPUT:\n" + json.dumps(context, sort_keys=True),
            )
            result = self.provider.run(
                AgentRequest(
                    role="architecture-holdout",
                    prompt=prompt,
                    cwd=tmp,
                    model=self.config.provider.model,
                    environment={},
                    structured_schema={"type": "object"},
                    max_turns=max_turns("architecture-holdout"),
                )
            )
            value = result.structured_output
            if not isinstance(value, Mapping):
                raise NeedsHuman("architecture holdout returned invalid JSON")
            target = paths.artifacts / "architecture-holdout.json"
            self._write_json(target, dict(value))
            return target

    # ---------- helpers ----------

    def _prepare_worktree(self, cwd: Path, paths: RunPaths) -> None:
        """Every isolated worktree gets exact locked dependencies before any model/test work."""
        self._exec(
            ["uv", "sync", "--frozen", "--all-extras"],
            cwd=cwd / "app" / "backend",
            timeout=600,
            transcript=paths.transcripts / "backend-sync.log",
        )
        self._exec(
            ["bun", "install", "--frozen-lockfile"],
            cwd=cwd / "app" / "frontend",
            timeout=600,
            transcript=paths.transcripts / "frontend-sync.log",
        )

    def _agent(
        self,
        role: str,
        cwd: Path,
        paths: RunPaths,
        *,
        context: str = "",
        env: Mapping[str, str],
    ) -> None:
        self.check_stop()
        prompt = prompt_text(
            self.config.prompt_path(role, cwd),
            preamble=(
                "You are a replaceable reasoning worker inside Dark Factory. You are not a merge "
                "authority. Obey repository and artifact constraints exactly."
            ),
            context=context,
        )
        started = time.time()
        result = self.provider.run(
            AgentRequest(
                role=role,
                prompt=prompt,
                cwd=str(cwd),
                model=self.config.provider.model,
                environment=dict(env),
                max_turns=max_turns(role),
            )
        )
        self._record_agent(paths, role, result, started=started)

    def _record_agent(self, paths: RunPaths, role: str, result: Any, *, started: float) -> None:
        """Write the worker's text, its telemetry, and the stage's wall time."""
        ended = time.time()
        paths.transcripts.mkdir(parents=True, exist_ok=True)
        (paths.transcripts / f"agent-{role}.log").write_text(
            result.content + "\n", encoding="utf-8"
        )
        telemetry = {
            "role": role,
            "model": getattr(result, "model", None),
            "session_id": getattr(result, "session_id", None),
            "num_turns": getattr(result, "num_turns", None),
            "duration_ms": getattr(result, "duration_ms", None),
            "total_cost_usd": getattr(result, "cost_usd", None),
            "input_tokens": getattr(result, "input_tokens", None),
            "output_tokens": getattr(result, "output_tokens", None),
            "wall_seconds": round(ended - started, 3),
            # A stage retried after a transient provider error reports every process it took
            # and why; the counts above are summed across them (D-031).
            "attempts": getattr(result, "attempts", 1),
            "transient_errors": list(getattr(result, "transient_errors", ()) or ()),
        }
        (paths.transcripts / f"agent-{role}.json").write_text(
            json.dumps(telemetry, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        record_stage_timing(
            paths.transcripts, kind="agent", name=role, started=started, ended=ended,
            num_turns=telemetry["num_turns"], duration_ms=telemetry["duration_ms"],
        )

    def _record_failed_agent(
        self, paths: RunPaths, role: str, exc: BaseException, *, started: float
    ) -> None:
        """Write the same stage record for a worker that failed as for one that returned.

        A stage that ended in an error envelope, an exhausted retry budget, a timeout or a
        refused unwrap used to leave no `agent-<role>.json` and no timing row, so the two
        `test_author` stream drops had only the exception text as evidence (D-040, D-041).
        The error text is scrubbed of every secret shape the guard knows before it is written,
        because a provider error can echo the prompt and the prompt can echo an issue body.
        """
        ended = time.time()
        paths.transcripts.mkdir(parents=True, exist_ok=True)
        carried = getattr(exc, "telemetry", None)
        telemetry = {
            "role": role,
            "outcome": "failed",
            "error_class": type(exc).__name__,
            "error": scrub(str(exc))[-4000:],
            "attempts": getattr(exc, "attempts", 1),
            "transient_errors": [scrub(str(e)) for e in (getattr(exc, "transient_errors", ()) or ())],
            "timed_out": bool(getattr(exc, "timed_out", False)),
            "wall_seconds": round(ended - started, 3),
            **({k: v for k, v in carried.items()} if isinstance(carried, Mapping) else {}),
        }
        (paths.transcripts / f"agent-{role}.json").write_text(
            json.dumps(telemetry, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8"
        )
        record_stage_timing(
            paths.transcripts, kind="agent", name=role, started=started, ended=ended,
            outcome="failed", error_class=type(exc).__name__,
            num_turns=telemetry.get("num_turns"), timed_out=telemetry["timed_out"] or None,
        )

    # A merge-base diff handed to reviewers and the conformance authority. Bounded so a large
    # change cannot blow the prompt; the worker still has Read over the checkout for the rest.
    DIFF_CONTEXT_CHARS = 60_000
    STAGE_NOTES = {"plan": "plan.md", "investigate": "investigation.md"}

    def _require_stage_note(self, artifacts: Path, role: str) -> None:
        """A plan/investigation the contract worker will read must exist and say something."""
        name = self.STAGE_NOTES[role]
        path = artifacts / name
        if not path.is_file() or not path.read_text(encoding="utf-8", errors="replace").strip():
            raise NeedsHuman(f"{role} worker wrote no {name}")

    def _changed_files(self, worktree: Path, env: Mapping[str, str]) -> list[str]:
        """The merge-base..HEAD changed-file set, the basis the conformance compiler judges."""
        base_ref = str(env.get("FACTORY_BASE_REF") or "origin/main")
        out = self._git("diff", "--name-only", f"{base_ref}...HEAD", cwd=worktree)
        return sorted({line.strip() for line in out.splitlines() if line.strip()})

    def _conformance_context(self, worktree: Path, paths: RunPaths, env: Mapping[str, str]) -> str:
        """Diff plus the exact policy-ID sets the conformance compiler will require.

        The compiler computes applicability from the changed files, not from the governor's
        context/planned basis; a worker told the wrong basis fails the ID-set check by name
        (run 33914596611 did, one gate earlier). The kernel computes the sets and the worker
        copies them; the compiler still recomputes and refuses any mismatch (D-030)."""
        ids = self._applicable_policy_ids(paths, files=self._changed_files(worktree, env))
        return (
            self._diff_context(worktree, env)
            + "\n\nAPPLICABLE ARCHITECTURE POLICY IDS FOR THE CHANGED FILES (computed by the "
            "kernel exactly as the compiler will; copy verbatim into principles, migrations, "
            "debts):\n" + json.dumps(ids, sort_keys=True)
        )

    def _diff_context(self, worktree: Path, env: Mapping[str, str]) -> str:
        """The merge-base..HEAD diff as text, with a stat, truncated past DIFF_CONTEXT_CHARS."""
        base_ref = str(env.get("FACTORY_BASE_REF") or "origin/main")
        stat = self._git("diff", "--stat", f"{base_ref}...HEAD", cwd=worktree)
        diff = self._git("diff", f"{base_ref}...HEAD", cwd=worktree)
        limit = self.DIFF_CONTEXT_CHARS
        body = diff if len(diff) <= limit else (
            diff[:limit] + f"\n\n[diff truncated after {limit} characters; read the changed files]"
        )
        return (
            f"MERGE-BASE DIFF ({base_ref}...HEAD), supplied by the kernel:\n"
            f"{stat}\n\n{body}"
        )

    def _applicable_policy_ids(
        self, paths: RunPaths, *, files: list[str] | None = None
    ) -> dict[str, list[str]]:
        """The policy ID sets the architecture compiler will require, computed the same way.

        The governor worker cannot run the compiler; handing it the exact sets it must echo
        removes the guess. The compiler still recomputes and refuses any mismatch. The governor's
        basis is context files plus planned files; the conformance compiler's basis is the changed
        files of the diff, so callers on that path pass `files` explicitly (D-030)."""
        policy = self._read_json(self.repo_root / ".factory" / "architecture.json")
        if files is None:
            context = self._read_json(paths.artifacts / "context.json")
            design = self._read_json(paths.artifacts / "design.json")
            files = sorted(
                {str(x) for x in (context.get("files") or [])}
                | {str(x) for x in (design.get("planned_files") or [])}
            )
        else:
            files = sorted({str(x) for x in files})

        def overlaps(path: str, prefix: str) -> bool:
            p, q = path.rstrip("/"), prefix.rstrip("/")
            return p == q or p.startswith(q + "/") or q.startswith(p + "/")

        def applicable(entries: list, key: str, *, active_only: bool = False) -> list[str]:
            out = []
            for entry in entries or []:
                if active_only and not entry.get("active", False):
                    continue
                if any(overlaps(f, prefix) for f in files for prefix in entry.get(key, [])):
                    out.append(str(entry["id"]))
            return sorted(out)

        return {
            "principles": applicable(policy.get("principles"), "scope"),
            "migrations": applicable(policy.get("migrations"), "paths", active_only=True),
            "debts": applicable(policy.get("debt"), "paths"),
        }

    def _worker_brief(
        self,
        paths: RunPaths,
        *,
        contract_hash: str,
        issue_context: str,
        include_design: bool = False,
        include_applicable_policy: bool = False,
    ) -> str:
        """What a post-contract worker is told in its prompt, so it need not rediscover it.

        The hash comes first because it is the integrity binding the deterministic compilers
        re-verify. The validated contract and the original issue are the kernel's own artifacts;
        handing them over changes nothing about authority. With `include_design`, the compiled
        context file list and the design's planned files follow. Never the holdout.
        """
        contract_text = (paths.artifacts / "task-contract.json").read_text(encoding="utf-8")
        parts = [
            f"Validated contract sha256: {contract_hash}",
            issue_context,
            "VALIDATED CONTRACT (task-contract.json, deterministic-compiled):\n" + contract_text.strip(),
        ]
        if include_design:
            context_files = self._read_json(paths.artifacts / "context.json").get("files")
            design = self._read_json(paths.artifacts / "design.json")
            summary = {
                "context_files": context_files if isinstance(context_files, list) else [],
                "planned_files": design.get("planned_files"),
                "allowed_new_files": design.get("allowed_new_files"),
                "seams": design.get("seams"),
            }
            parts.append(
                "COMPILED CONTEXT AND DESIGN SUMMARY:\n" + json.dumps(summary, sort_keys=True)
            )
        if include_applicable_policy:
            parts.append(
                "APPLICABLE ARCHITECTURE POLICY IDS (computed by the kernel exactly as the "
                "compiler will; copy these sets verbatim into principles, migrations, debts):\n"
                + json.dumps(self._applicable_policy_ids(paths), sort_keys=True)
            )
        return "\n\n".join(parts)

    def _run_env(
        self, paths: RunPaths, *, base_ref: str, base_sha: str | None = None
    ) -> dict[str, str]:
        env = {
            "ARTIFACTS_DIR": str(paths.artifacts),
            "FACTORY_BASE_REF": base_ref,
            "FACTORY_REPO": self.config.repository,
            "FACTORY_WORKDIR": str(self.config.runtime.work_root),
        }
        if base_sha:
            # The exact commit the branch was cut from; the provenance pack records this and
            # nothing else as its base.
            env["FACTORY_BASE_SHA"] = base_sha
        return env

    def _fetch_main(self) -> None:
        self._git("fetch", "origin", self.config.default_branch)

    def _exec(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        credential_scope: str = "none",
        timeout: int = 300,
        transcript: Path | None = None,
    ) -> str:
        # A trust-root program is executed from the kernel's own checkout of main; the working
        # directory stays the tree under test, so main's code judges the PR's tree and the PR
        # head's copy of an authority is never run (D-036). The checkout is looked up only when
        # argv names such a program; git/uv/bun and inline python never touch it.
        argv = resolve_trusted_program(self._kernel_checkout, argv)
        merged = scoped_environment(env, scope=credential_scope)
        started = time.time()
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=merged,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if transcript is not None:
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(output, encoding="utf-8")
            # Only stages that keep a transcript are timed: those are the deterministic gates
            # and the per-stage picture is what the file is for.
            record_stage_timing(
                transcript.parent, kind="exec", name=transcript.stem,
                started=started, ended=time.time(), rc=proc.returncode,
            )
        if proc.returncode:
            raise ToolRefused(argv, rc=proc.returncode, output=output)
        return output

    def _kernel_checkout(self) -> Path:
        """Where the kernel's own trust-root programs live: the configured repository root,
        or, for a bare runtime with none, the checkout this module was loaded from. Both are
        the kernel's copy; neither is ever the subject's."""
        root = getattr(self, "repo_root", None)
        return Path(root) if root else Path(__file__).resolve().parents[1]

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        return self._exec(["git", *args], cwd=cwd or self.repo_root, timeout=180).strip()

    def _assert_clean(self, cwd: Path) -> None:
        status = self._git("status", "--porcelain", cwd=cwd)
        if status:
            raise RuntimeError("factory worker left the worktree dirty:\n" + status)

    def _next_build_attempt(self, issue_number: int) -> int:
        value = self.github.json(
            [
                "issue", "view", str(issue_number), "-R", self.config.repository,
                "--json", "comments",
            ]
        )
        comments = value.get("comments", []) if isinstance(value, Mapping) else []
        failures = 0
        if isinstance(comments, list):
            for comment in comments:
                if not isinstance(comment, Mapping):
                    continue
                body = comment.get("body")
                if isinstance(body, str) and self.VALIDATION_FAILURE_MARKER in body:
                    failures += 1
        return failures + 1

    def _record_validation_failure(
        self,
        pr_number: int,
        linked_issue: int | None,
        exc: Exception,
        *,
        stage: str,
        paths: RunPaths,
        head: str,
        base: str,
    ) -> None:
        """Make the refusal a durable fact: a reason code on the PR, a scrubbed record in the
        run's artifacts, and the issue's rebuild budget charged only when the build was at fault."""
        refusal = describe(stage, exc)
        record = refusal_record(
            refusal, pr=pr_number, head=head, base=base, stage=stage, timestamp=_utc_now()
        )
        try:
            self._write_json(paths.artifacts / "validation-refusal.json", record)
        except Exception:
            pass
        try:
            self.github.cwd = str(self.repo_root)
            self.github.remove_pr_label(pr_number, self.config.labels["needs_review"])
            self.github.add_pr_label(pr_number, self.config.labels["needs_fix"])
            reason_code = record["reason_code"]
            summary = (
                render_refusal_marker(record)
                + "\nDark Factory validation failed closed. No merge was authorized. "
                f"Refused by: {record['authority']} (`{reason_code}`, `{record['exception']}`). "
                "The scrubbed refusal record is `validation-refusal.json` in the run's uploaded "
                "artifacts."
            )
            if reason_code == "stale_base":
                second = rehead_count(self.github.pr_comments(pr_number)) >= 1
                if second:
                    summary += (
                        "\nmain moved under this PR again after a re-head. The re-head budget is "
                        "one per PR, so this needs a human."
                    )
                    self.github.add_pr_label(pr_number, self.config.labels["needs_human"])
                else:
                    summary += (
                        "\nmain moved under this PR. This is not the build's fault: the next "
                        "dispatch re-heads the branch onto current main without a model, and the "
                        "issue's rebuild budget is not charged."
                    )
            self.github.comment_pr(pr_number, summary)
            # A stale base is the repository's motion, not the build's defect; charging the
            # issue's rebuild budget for it would exhaust the budget on nothing.
            if linked_issue is not None and reason_code != "stale_base":
                self.github.comment_issue(
                    linked_issue,
                    self.VALIDATION_FAILURE_MARKER
                    + "\nDark Factory independent validation rejected the latest build "
                    f"(`{reason_code}`). The issue remains eligible for a bounded fresh rebuild "
                    "from current main.",
                )
        except Exception:
            pass

    def _raise_post_merge_incident(
        self, pr_number: int, linked_issue: int | None, exc: Exception
    ) -> None:
        """Stop the factory remotely, because main can no longer be trusted.

        Applying a needs-fix label to a merged pull request would be containment theatre: the
        worker wakes hourly from current main on a fresh runner and would build the next issue on
        top of the unverified commit. The only containment that survives a fresh runner is an open
        issue carrying the stop label, which factory-stop.sh reads from GitHub before every
        dispatch and fails closed when it cannot read at all.
        """
        detail = (
            f"POST-MERGE VERIFICATION FAILED for #{pr_number}.\n\n"
            "A squash merge was authorized and has already happened. Verifying it afterwards "
            "failed, so the commit now on main has not been shown to be the commit that was "
            "evidenced.\n\n"
            "- main must be treated as UNTRUSTED until a human reconciles it\n"
            "- the autonomous factory has been stopped and will not dispatch again\n"
            "- do NOT rebuild from main: a rebuild starts from the commit in doubt\n\n"
            f"Failure class: `{type(exc).__name__}`\n"
            f"Detail: {exc}\n\n"
            f"Resume by reconciling main, then closing this issue (or removing "
            f"`{self.config.labels['stop']}` from it)."
        )
        stopped = False
        try:
            with tempfile.TemporaryDirectory(prefix="dark-factory-incident-") as tmp:
                body = Path(tmp) / "incident.md"
                body.write_text(detail, encoding="utf-8")
                self.github.cwd = str(self.repo_root)
                self.github.create_issue(
                    title=f"POST-MERGE VERIFICATION FAILED: main is untrusted (PR #{pr_number})",
                    body_file=body,
                    labels=(self.config.labels["stop"],),
                )
            stopped = True
        except Exception:
            # Fall back to the local kill file. It does not survive a fresh runner, so it is a
            # second line rather than the containment, and the failure is still surfaced below.
            try:
                kill = self.config.runtime.work_root / ".factory-stop"
                kill.parent.mkdir(parents=True, exist_ok=True)
                kill.write_text(detail, encoding="utf-8")
            except Exception:
                pass
        try:
            self.github.add_pr_label(pr_number, self.config.labels["needs_human"])
            self.github.comment_pr(
                pr_number,
                detail
                + ("" if stopped else "\n\nWARNING: the stop issue could not be opened. "
                                      "Stop the factory by hand before it dispatches again."),
            )
            if linked_issue is not None:
                self.github.comment_issue(
                    linked_issue,
                    "Dark Factory merged this build and then failed to verify the merge. "
                    "This issue is NOT eligible for a rebuild: main is untrusted until a human "
                    "reconciles it.",
                )
        except Exception:
            pass
        print(f"FACTORY_POST_MERGE_INCIDENT pr=#{pr_number} stopped={'remote' if stopped else 'local-only'}")

    def _mark_issue_human(self, issue: int, reason: str) -> None:
        try:
            self.github.cwd = str(self.repo_root)
            self.github.remove_issue_label(issue, self.config.labels["in_progress"])
            self.github.remove_issue_label(issue, self.config.labels["accepted"])
            self.github.add_issue_label(issue, self.config.labels["needs_human"])
            self.github.comment_issue(
                issue,
                "Dark Factory stopped this run without merging. " + reason[:1500],
            )
        except Exception:
            pass

    def _issue_dispatch_key(self, issue: Mapping[str, Any]) -> tuple[int, str, int]:
        labels = self.github.labels(issue)
        priority = min((self.PRIORITY[label] for label in labels if label in self.PRIORITY), default=4)
        return priority, str(issue.get("updatedAt") or ""), int(issue["number"])

    @staticmethod
    def _is_bug(labels: set[str]) -> bool:
        return any(label.lower() in {"bug", "type:bug", "kind:bug"} for label in labels)

    @staticmethod
    def _oldest_number(items: list[Mapping[str, Any]]) -> int:
        item = min(items, key=lambda row: (str(row.get("updatedAt") or ""), int(row["number"])))
        return int(item["number"])

    @staticmethod
    def _linked_issue_number(body: str) -> int | None:
        match = re.search(r"(?im)^\s*(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+#([1-9][0-9]*)\b", body)
        return int(match.group(1)) if match else None

    def _issue_frontier(self, issue: Mapping[str, Any]) -> dict[str, Any]:
        """The issue plus the state of every `Blocked by: #N` issue it names, as GitHub reports
        them now. Written before any model stage; read by `factory_artifacts.py ticket`."""
        body = str(issue.get("body") or "")
        blockers = []
        for number in sorted({int(x) for x in BLOCKED_BY.findall(body)}):
            state = str(self.github.issue(number).get("state") or "OPEN").upper()
            blockers.append({"issue": number, "state": state})
        return {
            "version": "1.0",
            "issue": dict(issue),
            "blockers": blockers,
            "fetched_at": _utc_now(),
        }

    @staticmethod
    def _issue_context(issue: Mapping[str, Any]) -> str:
        labels = sorted(GitHubClient.labels(issue))
        return (
            "ORIGINAL ISSUE (source of truth):\n"
            + json.dumps(
                {
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "body": issue.get("body"),
                    "labels": labels,
                },
                sort_keys=True,
            )
        )

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path) -> Mapping[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read factory JSON {path}: {exc}") from exc
        if not isinstance(value, Mapping):
            raise RuntimeError(f"factory JSON must be an object: {path}")
        return value

    @staticmethod
    def _json_sha(value: object) -> str:
        payload = (
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _extract_attached(body: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        # One parser for attach and extract (factory_kernel.attached), so the block a script
        # verified on the way in is the block the validator reads on the way out (D-038).
        def one(kind: str) -> Mapping[str, Any]:
            try:
                return extract_block(body, kind)
            except ValueError as exc:
                raise NeedsHuman(str(exc)) from exc

        return one("contract"), one("proof")
