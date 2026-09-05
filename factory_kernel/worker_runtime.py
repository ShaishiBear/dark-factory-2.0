"""Least-privilege worker boundary layered over the core orchestration runtime."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping
import uuid

from .agents import AgentRequest
from .git_authority import commit_acceptance_tests, commit_planned_changes, dirty_paths
from .methods import method_block
from .prompt_render import literal_artifacts_dir_entries, render_prompt
from .providers import prompt_text
from .runtime import KernelRuntime as BaseKernelRuntime, NeedsHuman, RunPaths
from .static_gate import check_files
from .worker_policy import KERNEL_COMMIT_ARGS, allowed_tools, max_budget_usd, max_turns, may_change_repo
from .worktree import create_detached, remove


# One static retry per mutation stage. A lint failure in a file the worker just wrote is
# repairable exactly once, by that worker, before the kernel commits the file; after RED the
# acceptance tests are immutable and no later stage can touch them (D-043).
STATIC_RETRIES = 1


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
                ["git", *KERNEL_COMMIT_ARGS, "revert", "--no-edit", merge_sha],
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
        static_retry: int = 0,
    ) -> None:
        self.check_stop()
        # Workers load no plugins or skills (--bare). Whatever engineering discipline the role is
        # expected to follow arrives here as pinned, protected text from .factory/methods/.
        # Prompts name outputs as `$ARTIFACTS_DIR/<file>` by contract. Nothing on the worker's
        # side expands that (no shell), so the kernel renders every placeholder to the absolute
        # run path here and refuses any placeholder it cannot render (D-026). Only the text the
        # kernel itself wrote (preamble, role prompt, pinned methods) is rendered: the context
        # carries untrusted material (the issue body, repro output, review JSON) that may mention
        # `$PATH` or `$GITHUB_TOKEN` and must reach the worker verbatim, not refuse the run (D-028).
        prompt = render_prompt(
            prompt_text(
                self.config.prompt_path(role, cwd),
                preamble=(
                    "You are a replaceable reasoning worker inside Dark Factory. You are not a "
                    "merge, Git, test-execution or external-system authority. Obey repository and "
                    "artifact constraints exactly. The kernel owns Git commits and all command "
                    "execution."
                ),
                methods=method_block(cwd, role),
            ),
            env,
        )
        if context.strip():
            prompt = prompt.rstrip("\n") + "\n\n" + context.strip() + "\n"
        # The stage is timed and recorded by the base runtime's single funnel, whether the
        # worker returns or raises: the record is evidence, never a verdict (D-041, D-050).
        self._agent_stage(
            paths,
            AgentRequest(
                role=role,
                prompt=prompt,
                cwd=str(cwd),
                model=self.config.provider.model,
                environment=dict(env),
                allowed_tools=allowed_tools(role),
                # A bounded loop: the CLI stops the worker at the role's cap and the provider
                # turns that into a failed stage (D-020).
                max_turns=max_turns(role),
                max_budget_usd=max_budget_usd(role),
            ),
            # A transient provider error is retried by the provider with a fresh process. A
            # mutation role may have half-written the checkout before the stream dropped, so
            # the kernel restores the worktree first; the provider itself never touches Git.
            before_retry=lambda attempt: self._restore_worktree_before_retry(role, cwd, attempt),
        )

        if role == "test_author":
            # The files are still uncommitted, so the author that wrote them is the one that can
            # still fix them. The gate runs BEFORE commit_acceptance_tests, because after the RED
            # commit those files are hashed and immutable for every later stage (D-043).
            if self._static_gate_or_retry(
                "test_author", cwd, paths, env, static_retry=static_retry, context=context,
            ):
                return
            commit_acceptance_tests(cwd, paths.artifacts / "test-spec.json")
            return
        if role in {"implement", "repair"}:
            contract = self._read_json(paths.artifacts / "task-contract.json")
            issue = contract.get("issue")
            issue_number = issue.get("number") if isinstance(issue, Mapping) else None
            if not isinstance(issue_number, int) or isinstance(issue_number, bool):
                raise RuntimeError("compiled contract lacks issue number for kernel Git authority")
            # Same gate on the production files before the design-envelope commit. A failure
            # here is repaired by ONE fresh `repair` worker briefed with the lint output; the
            # final quick gate over the whole tree remains the authority.
            if self._static_gate_or_retry(
                role, cwd, paths, env, static_retry=static_retry, context=context,
            ):
                return
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
        self._refuse_literal_artifacts_dir(cwd)
        self._assert_clean(cwd)

    def _static_gate_or_retry(
        self,
        role: str,
        cwd: Path,
        paths: RunPaths,
        env: Mapping[str, str],
        *,
        static_retry: int,
        context: str,
    ) -> bool:
        """Run the scoped static checks on the worker's uncommitted files.

        Returns True when a retry was dispatched and has already completed the commit (so the
        caller must not commit again), False when the files are clean and the caller commits.
        Raises NeedsHuman when the bounded retry is exhausted. The failed attempt's files stay in
        the checkout, uncommitted: the retried worker edits them in place rather than starting
        from a restored tree, because the defect is in those very files and nothing has been
        committed yet (contrast the transient-retry restore, D-031, where the tree is
        half-written by a dropped stream).
        """
        files = dirty_paths(cwd)
        result = self._scoped_static(cwd, files)
        record = {
            "role": role, "attempt": static_retry + 1, "files": files,
            "checks": list(result.checks), "ok": result.ok, "skipped": list(result.skipped),
            "output": result.output,
        }
        self._write_json(paths.artifacts / f"static-gate-{role}-{static_retry + 1}.json", record)
        if result.ok:
            return False
        if static_retry >= STATIC_RETRIES:
            raise NeedsHuman(
                f"{role} files do not pass the repository's static checks after "
                f"{STATIC_RETRIES + 1} attempts:\n{result.output[-2000:]}"
            )
        retry_role = "repair" if role in {"implement", "repair"} else role
        brief = (
            "STATIC CHECK FAILURE (kernel-run, deterministic). The files you wrote are still "
            "uncommitted; they must pass the repository's static checks before the kernel will "
            "commit them. Fix only lint/format problems in these files and change nothing else:\n"
            + "\n".join(files) + "\n\n" + result.output[-3000:]
        )
        merged = (context.strip() + "\n\n" + brief) if context.strip() else brief
        self._agent(retry_role, cwd, paths, context=merged, env=env, static_retry=static_retry + 1)
        return True

    def _scoped_static(self, cwd: Path, files: list[str]):
        return check_files(cwd, files)

    def _restore_worktree_before_retry(self, role: str, cwd: Path, attempt: int) -> None:
        """Put the checkout back to the pre-attempt state before a transient retry.

        A retried `test_author`/`implement`/`repair` process must start from the same tree the
        first one did, or the kernel's commit envelope would be judging the union of two
        half-finished attempts. Tracked edits are discarded and untracked files removed, within
        this worktree only; the run's ARTIFACTS_DIR lives outside it and is left for the worker
        to overwrite. A non-mutation role must not have changed anything, so for those the
        restore is an assertion, not a cleanup (D-031).
        """
        if may_change_repo(role):
            self._git("checkout", "--", ".", cwd=cwd)
            self._git("clean", "-fd", "--", ".", cwd=cwd)
        self._assert_clean(cwd)

    def _refuse_literal_artifacts_dir(self, cwd: Path) -> None:
        """Name the failure class when a worker wrote to a literal `$ARTIFACTS_DIR` path."""
        status = self._git("status", "--porcelain", "--untracked-files=all", cwd=cwd)
        hits = literal_artifacts_dir_entries(status)
        if hits:
            raise RuntimeError(
                "worker wrote to a literal $ARTIFACTS_DIR path; the kernel must substitute the "
                "artifacts directory into the prompt: " + ", ".join(hits)
            )
