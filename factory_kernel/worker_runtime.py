"""Least-privilege worker boundary layered over the core orchestration runtime."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping
import uuid

from .agents import AgentRequest
from .git_authority import commit_acceptance_tests, commit_planned_changes
from .providers import prompt_text
from .runtime import KernelRuntime as BaseKernelRuntime, NeedsHuman, RunPaths
from .worker_policy import allowed_tools, may_change_repo
from .worktree import create_detached, remove


class WorkerControlledRuntime(BaseKernelRuntime):
    """Core runtime with model filesystem/tool authority narrowed by role.

    The base runtime owns lifecycle semantics. This focused layer owns the model boundary plus
    post-merge operational authority: tool availability, dirty-tree enforcement, kernel-created
    commits, exact-main revalidation and safe revert-PR creation.
    """

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
        """Upgrade the generic v5 evidence call to the production 100%-spine authority.

        The base runtime deliberately remains usable for focused lifecycle tests. Production CLI
        commands instantiate this class, so no autonomous merge can fall back to the legacy
        Evidence Bundle path: the outer authority must close all protected spine claims first.
        """
        routed = list(argv)
        if len(routed) >= 2 and routed[0] == "python" and routed[1] == "scripts/factory_evidence.py":
            routed[1] = "scripts/factory_evidence_spine.py"
        return super()._exec(
            routed,
            cwd=cwd,
            env=env,
            credential_scope=credential_scope,
            timeout=timeout,
            transcript=transcript,
        )

    def validate_pr(self, pr_number: int, *, merge: bool = True) -> Path:
        result = super().validate_pr(pr_number, merge=merge)
        if not merge:
            return result

        post_output = result.with_name("post-merge.json")
        transcript = result.parent.parent / "transcripts" / "post-merge.log"
        try:
            self._exec(
                [
                    "python",
                    "harness/post_merge.py",
                    "--merge-verification",
                    str(result),
                    "--output",
                    str(post_output),
                ],
                cwd=self.repo_root,
                env={"FACTORY_WORKDIR": str(self.config.runtime.work_root)},
                credential_scope="validation",
                timeout=4800,
                transcript=transcript,
            )
        except Exception as exc:
            revert_pr = self._create_safe_revert_pr(pr_number, result, exc)
            suffix = f"; safe revert PR #{revert_pr} opened" if revert_pr is not None else ""
            raise NeedsHuman(f"post-merge validation failed{suffix}") from exc

        print(f"FACTORY_POST_MERGE_VERIFIED pr=#{pr_number} output={post_output}")
        return post_output

    def _create_safe_revert_pr(
        self, original_pr: int, merge_verification: Path, failure: Exception
    ) -> int | None:
        """Open a revert PR only when the failed merge is still the exact main tip.

        This intentionally never auto-merges the revert. If any later commit has landed, the
        situation is no longer mechanically reversible without human judgement and we only
        escalate the original PR.
        """
        merge = self._read_json(merge_verification)
        merge_sha = str(merge.get("merge_sha") or "")
        if merge.get("version") != "1.0" or merge.get("verdict") != "verified":
            raise RuntimeError("cannot create revert PR without verified merge evidence")

        self._fetch_main()
        current_main = self._git("rev-parse", f"origin/{self.config.default_branch}")
        if current_main != merge_sha:
            self.github.cwd = str(self.repo_root)
            self.github.add_pr_label(original_pr, self.config.labels["needs_human"])
            self.github.comment_pr(
                original_pr,
                "Post-merge validation failed, but main has moved since the verified merge. "
                "Automatic revert is unsafe; human intervention is required. "
                f"Failure class: `{type(failure).__name__}`.",
            )
            return None

        worktree = create_detached(
            self.repo_root,
            merge_sha,
            base_dir=self.config.runtime.work_root / "revert-worktrees",
            prefix="revert",
        )
        branch = f"factory/revert-{merge_sha[:12]}-{uuid.uuid4().hex[:8]}"
        try:
            self._git("checkout", "-b", branch, cwd=worktree.path)
            self._exec(
                [
                    "git",
                    "-c",
                    "user.name=Dark Factory",
                    "-c",
                    "user.email=dark-factory@users.noreply.github.com",
                    "revert",
                    "--no-edit",
                    merge_sha,
                ],
                cwd=worktree.path,
                timeout=180,
            )
            self._assert_clean(worktree.path)
            self.github.cwd = str(worktree.path)
            self.github.push_branch(branch)
            body = worktree.path / ".factory-revert-pr.md"
            body.write_text(
                "Post-merge validation failed on the exact merge commit.\n\n"
                f"Reverts verified merge `{merge_sha}` from PR #{original_pr}.\n\n"
                "This PR was created automatically only because that merge was still the exact "
                f"tip of `{self.config.default_branch}`. It is deliberately **not** auto-merged; "
                "human review is required before any rollback.\n\n"
                f"Failure class: `{type(failure).__name__}`.\n",
                encoding="utf-8",
            )
            created = self.github.create_pr(
                head=branch,
                base=self.config.default_branch,
                title=f"revert(factory): post-merge failure for #{original_pr}",
                body_file=body,
            )
            revert_number = int(created["number"])
            self.github.add_pr_label(revert_number, self.config.labels["needs_human"])
            self.github.add_pr_label(original_pr, self.config.labels["needs_human"])
            self.github.comment_pr(
                original_pr,
                "Post-merge validation failed on the exact merged main commit. "
                f"Safe revert PR #{revert_number} was opened and requires human review. "
                f"Failure class: `{type(failure).__name__}`.",
            )
            return revert_number
        finally:
            self.github.cwd = str(self.repo_root)
            try:
                remove(self.repo_root, worktree)
            except RuntimeError:
                remove(self.repo_root, worktree, force=True, require_clean=False)

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
                "You are a replaceable reasoning worker inside Dark Factory. You are not a merge, "
                "Git, test-execution or external-system authority. Obey repository and artifact "
                "constraints exactly. The kernel owns Git commits and all command execution."
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
                allowed_tools=allowed_tools(role),
            )
        )
        (paths.transcripts / f"agent-{role}.log").write_text(
            result.content + "\n", encoding="utf-8"
        )

        if role == "test_author":
            commit_acceptance_tests(cwd, paths.artifacts / "test-spec.json")
            return
        if role in {"implement", "repair"}:
            contract = self._read_json(paths.artifacts / "task-contract.json")
            issue = contract.get("issue")
            issue_number = issue.get("number") if isinstance(issue, Mapping) else None
            if not isinstance(issue_number, int) or isinstance(issue_number, bool):
                raise RuntimeError("compiled contract lacks issue number for kernel Git authority")
            subject = (
                f"fix(factory): satisfy issue #{issue_number}"
                if role == "implement"
                else f"fix(factory): repair issue #{issue_number}"
            )
            commit_planned_changes(
                cwd,
                design_path=paths.artifacts / "design.json",
                red_proof_path=paths.artifacts / "red-proof.json",
                subject=subject,
                issue_number=issue_number,
            )
            return

        if may_change_repo(role):
            raise RuntimeError(f"unhandled repository-mutation role: {role}")
        self._assert_clean(cwd)
