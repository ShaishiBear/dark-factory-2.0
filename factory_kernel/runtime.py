"""Repo-owned orchestration for Dark Factory.

The runtime may ask model workers to investigate, design, test, implement and review. It never
accepts a worker's confidence as authority: existing deterministic compilers, replay gates,
holdouts, the full harness and exact merged-SHA verifier remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any, Mapping

from .agents import AgentRequest
from .config import KernelConfig
from .github_cli import GitHubClient
from .providers import ClaudeCliProvider, prompt_text
from .worktree import Worktree, create_detached, remove


class FactoryStopped(RuntimeError):
    pass


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
    def __init__(self, *, repo_root: Path, config: KernelConfig):
        self.repo_root = repo_root.resolve()
        self.config = config
        self.provider = ClaudeCliProvider(config.provider)
        self.github = GitHubClient(config.repository, cwd=self.repo_root)

    # ---------- control plane ----------

    def check_stop(self) -> None:
        env = dict(os.environ)
        env["FACTORY_REPO"] = self.config.repository
        env.setdefault("FACTORY_WORKDIR", str(self.config.runtime.work_root))
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
            timeout=180,
        )

    def choose_dispatch(self) -> DispatchDecision:
        """Priority is part of the kernel contract: stop -> reap -> review -> build."""
        self.check_stop()
        self.reap_stale_claims()

        review = self.github.list_prs(self.config.labels["needs_review"])
        if review:
            number = self._oldest_number(review)
            return DispatchDecision("validate-pr", number, "PR validation has priority")

        accepted = self.github.list_issues(self.config.labels["accepted"])
        for issue in sorted(accepted, key=lambda item: str(item.get("updatedAt") or "")):
            labels = self.github.labels(issue)
            if self.config.labels["in_progress"] not in labels:
                return DispatchDecision("build-issue", int(issue["number"]), "accepted issue is idle")
        return DispatchDecision("idle", reason="no review PR or accepted idle issue")

    def dispatch_once(self, *, merge: bool = True) -> DispatchDecision:
        decision = self.choose_dispatch()
        if decision.kind == "validate-pr" and decision.number is not None:
            self.validate_pr(decision.number, merge=merge)
        elif decision.kind == "build-issue" and decision.number is not None:
            self.build_issue(decision.number)
        return decision

    # ---------- builder ----------

    def build_issue(self, issue_number: int) -> int:
        self.check_stop()
        issue = self.github.issue(issue_number)
        labels = self.github.labels(issue)
        if self.config.labels["accepted"] not in labels:
            raise NeedsHuman(f"issue #{issue_number} is not {self.config.labels['accepted']}")
        if self.config.labels["in_progress"] in labels:
            raise NeedsHuman(f"issue #{issue_number} already has an active factory claim")

        self._fetch_main()
        base_sha = self._git("rev-parse", f"origin/{self.config.default_branch}")
        run_id = f"issue-{issue_number}-{uuid.uuid4().hex[:12]}"
        paths = RunPaths.create(self.config.runtime.work_root, run_id)
        worktree = create_detached(
            self.repo_root,
            base_sha,
            base_dir=self.config.runtime.work_root / "worktrees",
        )
        branch = f"factory/issue-{issue_number}-{run_id.rsplit('-', 1)[-1]}"
        handed_off = False
        self.github.add_issue_label(issue_number, self.config.labels["in_progress"])
        try:
            self._git("checkout", "-b", branch, cwd=worktree.path)
            self._write_json(paths.artifacts / "issue.json", issue)
            env = self._run_env(paths, base_ref=f"origin/{self.config.default_branch}")
            issue_context = self._issue_context(issue)

            role = "investigate" if self._is_bug(labels) else "plan"
            self._agent(role, worktree.path, paths, context=issue_context, env=env)
            self._agent("contract", worktree.path, paths, context=issue_context, env=env)
            self._exec(
                [
                    "python", "scripts/factory_protocol.py", "contract",
                    "--input", str(paths.artifacts / "task-contract.raw.json"),
                    "--output", str(paths.artifacts / "task-contract.json"),
                    "--hash-output", str(paths.artifacts / "task-contract.sha256"),
                    "--issue", str(issue_number),
                ],
                cwd=worktree.path, env=env, timeout=120,
                transcript=paths.transcripts / "contract-gate.log",
            )

            contract_hash = (paths.artifacts / "task-contract.sha256").read_text(encoding="utf-8").strip()
            self._agent(
                "context", worktree.path, paths,
                context=f"Validated contract sha256: {contract_hash}", env=env,
            )
            self._exec(
                [
                    "python", "scripts/factory_protocol.py", "context",
                    "--input", str(paths.artifacts / "context.raw.json"),
                    "--contract", str(paths.artifacts / "task-contract.json"),
                    "--output", str(paths.artifacts / "context.json"),
                ],
                cwd=worktree.path, env=env, timeout=180,
                transcript=paths.transcripts / "context-gate.log",
            )

            self._agent("architecture", worktree.path, paths, env=env)
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
                cwd=worktree.path, env=env, timeout=120,
                transcript=paths.transcripts / "architecture-gate.log",
            )
            governor = self._read_json(paths.artifacts / "architecture-governor.json")
            if governor.get("decision") != "proceed":
                raise NeedsHuman(
                    f"architecture governor returned {governor.get('decision')}: "
                    + "; ".join(governor.get("required_changes") or [])
                )
            self._exec(
                [
                    "python", "scripts/factory_architecture.py", "scope",
                    "--governor", str(paths.artifacts / "architecture-governor.json"),
                    "--action", "implement",
                ],
                cwd=worktree.path, env=env, timeout=60,
            )

            # Fresh process = independent test author. The deterministic RED compiler is authority.
            self._agent("test_author", worktree.path, paths, env=env)
            self._exec(
                [
                    "python", "scripts/factory_proof.py", "red",
                    "--spec", str(paths.artifacts / "test-spec.json"),
                    "--output", str(paths.artifacts / "red-proof.json"),
                ],
                cwd=worktree.path, env=env, timeout=600,
                transcript=paths.transcripts / "red-gate.log",
            )

            self._agent(
                "implement", worktree.path, paths,
                context=f"Dispatched issue number is #{issue_number}.", env=env,
            )
            self._exec(
                [
                    "python", "scripts/factory_proof.py", "green",
                    "--proof", str(paths.artifacts / "red-proof.json"),
                    "--output", str(paths.artifacts / "green-proof.json"),
                ],
                cwd=worktree.path, env=env, timeout=600,
                transcript=paths.transcripts / "green-gate.log",
            )

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
                cwd=worktree.path, env=env, timeout=180,
                transcript=paths.transcripts / "conformance-gate.log",
            )
            self._exec(
                [
                    "python", "scripts/factory_proof.py", "green",
                    "--proof", str(paths.artifacts / "red-proof.json"),
                    "--output", str(paths.artifacts / "final-green-proof.json"),
                ],
                cwd=worktree.path, env=env, timeout=600,
                transcript=paths.transcripts / "final-green-gate.log",
            )

            # Quick gate before publishing; full/holdout/mutations are validator-owned.
            self._exec(
                list(self.config.validation.quick_command),
                cwd=worktree.path, env=env, timeout=900,
                transcript=paths.transcripts / "quick-gate.log",
            )
            self._assert_clean(worktree.path)
            self.github.cwd = str(worktree.path)
            self.github.push_branch(branch)

            body = paths.root / "pr-body.md"
            body.write_text(
                f"Fixes #{issue_number}\n\n"
                "Generated by the repo-owned Dark Factory kernel. Model workers are not merge "
                "authorities; deterministic and independent validation still follows.\n",
                encoding="utf-8",
            )
            pr = self.github.create_pr(
                head=branch,
                base=self.config.default_branch,
                title=f"factory: {str(issue.get('title') or '').strip()}",
                body_file=body,
            )
            pr_number = int(pr["number"])
            self._exec(
                [
                    "python", "scripts/factory_protocol.py", "attach",
                    "--contract", str(paths.artifacts / "task-contract.json"),
                    "--pr", str(pr_number),
                ],
                cwd=worktree.path, env=env, timeout=120,
            )
            self._exec(
                [
                    "python", "scripts/factory_proof.py", "attach",
                    "--proof", str(paths.artifacts / "final-green-proof.json"),
                    "--pr", str(pr_number),
                ],
                cwd=worktree.path, env=env, timeout=180,
            )
            self.github.add_pr_label(pr_number, self.config.labels["needs_review"])
            self.github.remove_issue_label(issue_number, self.config.labels["in_progress"])
            handed_off = True
            print(f"FACTORY_BUILD_OK issue=#{issue_number} pr=#{pr_number} head={self._git('rev-parse', 'HEAD', cwd=worktree.path)}")
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

    def _review_and_repair(self, worktree: Worktree, paths: RunPaths, env: Mapping[str, str]) -> None:
        self._agent("review", worktree.path, paths, env=env)
        review = self._read_json(paths.artifacts / "code-review.json")
        if review.get("verdict") == "pass":
            return
        if review.get("verdict") != "fail":
            raise NeedsHuman("review worker returned an invalid verdict")
        findings = review.get("findings")
        if not isinstance(findings, list):
            raise NeedsHuman("review worker returned malformed findings")
        self._agent(
            "repair", worktree.path, paths,
            context="Blocking review JSON:\n" + json.dumps(review, sort_keys=True), env=env,
        )
        self._exec(
            [
                "python", "scripts/factory_proof.py", "green",
                "--proof", str(paths.artifacts / "red-proof.json"),
                "--output", str(paths.artifacts / "green-after-repair.json"),
            ],
            cwd=worktree.path, env=env, timeout=600,
        )
        self._agent("review", worktree.path, paths, context="This is the fresh post-repair review.", env=env)
        second = self._read_json(paths.artifacts / "code-review.json")
        if second.get("verdict") != "pass":
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
        if not re.fullmatch(r"[0-9a-f]{40,64}", head) or not re.fullmatch(r"[0-9a-f]{40,64}", base):
            raise NeedsHuman("PR lacks exact Git object identities")

        self._git("fetch", "origin", str(info["headRefName"]), self.config.default_branch)
        run_id = f"pr-{pr_number}-{uuid.uuid4().hex[:12]}"
        paths = RunPaths.create(self.config.runtime.work_root, run_id)
        worktree = create_detached(
            self.repo_root, head, base_dir=self.config.runtime.work_root / "validator-worktrees"
        )
        try:
            env = self._run_env(paths, base_ref=base)
            self._exec(
                ["python", "scripts/factory_security.py", "--pr", str(pr_number),
                 "--output", str(paths.artifacts / "security.json")],
                cwd=worktree.path, env=env, timeout=180,
                transcript=paths.transcripts / "security.log",
            )

            contract, proof = self._extract_attached(info.get("body") or "")
            self._write_json(paths.artifacts / "attached-contract.json", contract)
            self._write_json(paths.artifacts / "attached-proof.json", proof)
            patch = self._git("diff", "--binary", f"{base}...{head}", cwd=worktree.path)
            changed = self._git("diff", "--name-only", f"{base}...{head}", cwd=worktree.path).splitlines()
            policy = self._read_json(worktree.path / ".factory/architecture.json")

            # Blinded code/behavior holdout runs outside the source checkout.
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

            architecture_holdout = self._run_architecture_holdout(
                paths, policy=policy, changed_files=sorted(x for x in changed if x),
                diff=patch, builder_architecture=proof.get("architecture_builder"),
            )
            self._write_json(paths.artifacts / "validator-verdict.json", {
                "version": "1.0", "verdict": "approve",
                "holdout_sha256": self._json_sha(verdict),
            })

            self._exec(
                [
                    "python", "scripts/factory_evidence.py", "--pr", str(pr_number),
                    "--verdict", str(paths.artifacts / "validator-verdict.json"),
                    "--architecture-verdict", str(architecture_holdout),
                    "--output", str(paths.artifacts / "evidence-bundle.json"),
                ],
                cwd=worktree.path, env=env, timeout=2400,
                transcript=paths.transcripts / "evidence.log",
            )
            self._exec(
                [
                    "python", "harness/merge_verify.py", "pre", "--pr", str(pr_number),
                    "--evidence", str(paths.artifacts / "evidence-bundle.json"),
                    "--output", str(paths.artifacts / "merge-authorization.json"),
                ],
                cwd=worktree.path, env=env, timeout=180,
                transcript=paths.transcripts / "merge-pre.log",
            )
            if not merge:
                print(f"FACTORY_VALIDATED pr=#{pr_number} head={head} merge=disabled")
                return paths.artifacts / "evidence-bundle.json"

            # One last emergency-stop read immediately before the irreversible action.
            self.check_stop()
            self.github.cwd = str(worktree.path)
            self.github.merge_squash(pr_number, expected_head=head)
            self._exec(
                [
                    "python", "harness/merge_verify.py", "post", "--pr", str(pr_number),
                    "--evidence", str(paths.artifacts / "evidence-bundle.json"),
                    "--authorization", str(paths.artifacts / "merge-authorization.json"),
                    "--output", str(paths.artifacts / "merge-verification.json"),
                ],
                cwd=worktree.path, env=env, timeout=240,
                transcript=paths.transcripts / "merge-post.log",
            )
            print(f"FACTORY_MERGED_VERIFIED pr=#{pr_number} evidenced_head={head}")
            return paths.artifacts / "merge-verification.json"
        except Exception as exc:
            try:
                self.github.cwd = str(self.repo_root)
                self.github.remove_pr_label(pr_number, self.config.labels["needs_review"])
                self.github.add_pr_label(pr_number, self.config.labels["needs_fix"])
                self.github.comment_pr(
                    pr_number,
                    "Dark Factory validation failed closed. No merge was authorized. "
                    f"Failure class: `{type(exc).__name__}`. Inspect the validator host transcript.",
                )
            except Exception:
                pass
            raise
        finally:
            self.github.cwd = str(self.repo_root)
            try:
                remove(self.repo_root, worktree)
            except RuntimeError:
                pass

    # ---------- holdouts ----------

    def _run_blinded_holdout(self, paths: RunPaths, context: Mapping[str, Any]) -> Mapping[str, Any]:
        with tempfile.TemporaryDirectory(prefix="dark-factory-holdout-") as tmp:
            cwd = Path(tmp)
            prompt = prompt_text(
                self.config.prompt_path("holdout", self.repo_root),
                preamble="This invocation is intentionally isolated from the repository checkout.",
                context="HOLDOUT INPUT:\n" + json.dumps(context, sort_keys=True),
            )
            result = self.provider.run(
                AgentRequest(
                    role="holdout", prompt=prompt, cwd=str(cwd), model=self.config.provider.model,
                    environment={}, structured_schema={"type": "object"},
                )
            )
            value = result.structured_output
            if not isinstance(value, Mapping) or value.get("version") != "1.0":
                raise NeedsHuman("blinded holdout returned invalid JSON")
            findings = value.get("findings")
            if not isinstance(findings, list):
                raise NeedsHuman("blinded holdout findings are invalid")
            self._write_json(paths.artifacts / "holdout.json", dict(value))
            return value

    def _run_architecture_holdout(
        self, paths: RunPaths, *, policy: Mapping[str, Any], changed_files: list[str],
        diff: str, builder_architecture: object,
    ) -> Path:
        context = {
            "architecture_policy": policy,
            "changed_files": changed_files,
            "diff": diff,
            "builder_architecture": builder_architecture,
        }
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
                    role="architecture-holdout", prompt=prompt, cwd=tmp,
                    model=self.config.provider.model, environment={},
                    structured_schema={"type": "object"},
                )
            )
            value = result.structured_output
            if not isinstance(value, Mapping):
                raise NeedsHuman("architecture holdout returned invalid JSON")
            target = paths.artifacts / "architecture-holdout.json"
            self._write_json(target, dict(value))
            return target

    # ---------- helpers ----------

    def _agent(
        self, role: str, cwd: Path, paths: RunPaths, *, context: str = "",
        env: Mapping[str, str],
    ) -> None:
        self.check_stop()
        prompt = prompt_text(
            self.config.prompt_path(role, cwd),
            preamble=(
                "You are a replaceable reasoning worker inside Dark Factory. You are not a merge "
                "authority. Obey the repository and artifact constraints exactly."
            ),
            context=context,
        )
        result = self.provider.run(
            AgentRequest(
                role=role,
                prompt=prompt,
                cwd=str(cwd),
                model=self.config.provider.model,
                environment=dict(env),
            )
        )
        (paths.transcripts / f"agent-{role}.log").write_text(result.content + "\n", encoding="utf-8")

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
        self, argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None,
        timeout: int = 300, transcript: Path | None = None,
    ) -> str:
        merged = dict(os.environ)
        if env:
            merged.update(env)
        proc = subprocess.run(
            argv, cwd=cwd, env=merged, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if transcript is not None:
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(output, encoding="utf-8")
        if proc.returncode:
            raise RuntimeError(f"{' '.join(argv)} failed rc={proc.returncode}: {output[-4000:]}")
        return output

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        return self._exec(["git", *args], cwd=cwd or self.repo_root, timeout=180).strip()

    def _assert_clean(self, cwd: Path) -> None:
        status = self._git("status", "--porcelain", cwd=cwd)
        if status:
            raise RuntimeError("factory worker left the worktree dirty:\n" + status)

    def _mark_issue_human(self, issue: int, reason: str) -> None:
        try:
            self.github.cwd = str(self.repo_root)
            self.github.remove_issue_label(issue, self.config.labels["in_progress"])
            self.github.add_issue_label(issue, self.config.labels["needs_human"])
            self.github.comment_issue(
                issue,
                "Dark Factory stopped this run without merging. " + reason[:1500],
            )
        except Exception:
            pass

    @staticmethod
    def _is_bug(labels: set[str]) -> bool:
        return any(label.lower() in {"bug", "type:bug", "kind:bug"} for label in labels)

    @staticmethod
    def _oldest_number(items: list[Mapping[str, Any]]) -> int:
        item = min(items, key=lambda row: str(row.get("updatedAt") or ""))
        return int(item["number"])

    @staticmethod
    def _issue_context(issue: Mapping[str, Any]) -> str:
        labels = sorted(GitHubClient.labels(issue))
        return (
            "ORIGINAL ISSUE (source of truth):\n"
            + json.dumps(
                {
                    "number": issue.get("number"), "title": issue.get("title"),
                    "body": issue.get("body"), "labels": labels,
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
