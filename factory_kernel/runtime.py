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
from .provenance import verify_pack
from .repro import (
    DEFERRED_ARTIFACT, OBSERVED_ARTIFACT, REPRO_ARTIFACT, ReproRefused, default_runner,
    deferred_record, execute, load_deferred, load_repro, observed_record, verify_deferred_in_red,
)
from .review import AXES, ROLE_FOR_AXIS, ReviewInvalid, aggregate, read_axes
from .pr_body import render_pr_body
from .worker_policy import BUILDER_BLIND_PATHS, max_turns
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
            env = self._run_env(paths, base_ref=f"origin/{self.config.default_branch}")
            issue_context = self._issue_context(issue)

            role = "investigate" if self._is_bug(labels) else "plan"
            self._agent(role, worktree.path, paths, context=issue_context, env=env)
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
                    include_design=True,
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
                ),
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
            self._agent("conformance", worktree.path, paths, env=env)
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
            # The two attach programs edit the PR body through gh, so they keep GitHub scope.
            # Neither runs a model-authored command: attach binds already-proven artifacts.
            self._exec(
                [
                    "python", "scripts/factory_protocol.py", "attach",
                    "--contract", str(paths.artifacts / "task-contract.json"),
                    "--pr", str(pr_number),
                ],
                cwd=worktree.path,
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
                cwd=worktree.path,
                env=env,
                credential_scope="github",
                timeout=180,
            )
            self._exec(
                [
                    "python", "scripts/factory_provenance.py", "publish",
                    "--pr", str(pr_number),
                    "--artifacts", str(paths.artifacts),
                ],
                cwd=worktree.path,
                env=env,
                credential_scope="github",
                timeout=240,
                transcript=paths.transcripts / "provenance-publish.log",
            )
            self._lease_heartbeat(
                "finish", issue_number, "pr-handoff", paths, cwd=worktree.path, pr=pr_number
            )
            self.github.add_pr_label(pr_number, self.config.labels["needs_review"])
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

    def _two_axis_review(
        self, worktree: Worktree, paths: RunPaths, env: Mapping[str, str], *, context: str = ""
    ) -> dict[str, Any]:
        """Spec and Standards are judged by separate fresh processes; the kernel aggregates.

        Each axis writes its own artifact. The deterministic aggregator refuses a missing,
        malformed or mislabelled artifact and fails the review if either axis fails.
        """
        for axis in AXES:
            self._agent(ROLE_FOR_AXIS[axis], worktree.path, paths, context=context, env=env)
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
            verdict = self._run_blinded_holdout(paths, holdout_context)
            if verdict.get("verdict") != "pass":
                raise NeedsHuman("blinded holdout rejected PR")

            pack = self._builder_pack(paths, head=head, base=base, issue=linked_issue)
            architecture_holdout = self._run_architecture_holdout(
                paths,
                pack=pack,
                policy=policy,
                changed_files=sorted(x for x in changed if x),
                diff=patch,
            )
            self._certify_precode_claims(
                paths, pack=pack, head=head, base=base, issue=linked_issue
            )
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
            self._record_validation_failure(pr_number, linked_issue, exc)
            raise
        finally:
            self.github.cwd = str(self.repo_root)
            try:
                remove(self.repo_root, worktree)
            except RuntimeError:
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

    def _builder_pack(
        self, paths: RunPaths, *, head: str, base: str, issue: int | None
    ) -> Mapping[str, Any]:
        """Fetch and re-verify the exact-head builder provenance the authorities are judged on."""
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
        suffix = (
            f"Return ONLY JSON with version 1.0; certifies {claim_id!r}; verdict pass|fail; and "
            "findings as objects with severity critical|high|medium|low and non-empty "
            "description. " + self.CERTIFIER_QUESTIONS[claim_id]
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
        suffix = (
            "Return ONLY JSON with version 1.0; verdict pass|fail; convergence "
            "improves|neutral|regresses; principles, migrations, debts arrays containing exactly "
            "the policy IDs applicable to changed_files; and findings as objects with severity "
            "critical|high|medium|low and non-empty description."
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
        }
        (paths.transcripts / f"agent-{role}.json").write_text(
            json.dumps(telemetry, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        record_stage_timing(
            paths.transcripts, kind="agent", name=role, started=started, ended=ended,
            num_turns=telemetry["num_turns"], duration_ms=telemetry["duration_ms"],
        )

    def _worker_brief(
        self,
        paths: RunPaths,
        *,
        contract_hash: str,
        issue_context: str,
        include_design: bool = False,
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
        return "\n\n".join(parts)

    def _run_env(self, paths: RunPaths, *, base_ref: str) -> dict[str, str]:
        return {
            "ARTIFACTS_DIR": str(paths.artifacts),
            "FACTORY_BASE_REF": base_ref,
            "FACTORY_REPO": self.config.repository,
            "FACTORY_WORKDIR": str(self.config.runtime.work_root),
        }

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
            raise RuntimeError(f"{' '.join(argv)} failed rc={proc.returncode}: {output[-4000:]}")
        return output

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
        self, pr_number: int, linked_issue: int | None, exc: Exception
    ) -> None:
        try:
            self.github.cwd = str(self.repo_root)
            self.github.remove_pr_label(pr_number, self.config.labels["needs_review"])
            self.github.add_pr_label(pr_number, self.config.labels["needs_fix"])
            self.github.comment_pr(
                pr_number,
                "Dark Factory validation failed closed. No merge was authorized. "
                f"Failure class: `{type(exc).__name__}`. The validator transcript remains on the host.",
            )
            if linked_issue is not None:
                self.github.comment_issue(
                    linked_issue,
                    self.VALIDATION_FAILURE_MARKER
                    + "\nDark Factory independent validation rejected the latest build. "
                    "The issue remains eligible for a bounded fresh rebuild from current main.",
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
        def one(kind: str) -> Mapping[str, Any]:
            pattern = re.compile(
                rf"<!-- factory-{kind}:start -->\s*```factory-{kind}\s*(\{{.*?\}})\s*```",
                re.S,
            )
            match = pattern.search(body)
            if not match:
                raise NeedsHuman(f"PR is missing attached factory-{kind} evidence")
            try:
                value = json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                raise NeedsHuman(f"attached factory-{kind} is invalid JSON") from exc
            if not isinstance(value, Mapping):
                raise NeedsHuman(f"attached factory-{kind} must be an object")
            return value

        return one("contract"), one("proof")
