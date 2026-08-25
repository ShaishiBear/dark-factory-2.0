"""Least-privilege worker boundary layered over the core orchestration runtime."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .agents import AgentRequest
from .git_authority import commit_acceptance_tests, commit_planned_changes
from .providers import prompt_text
from .runtime import KernelRuntime as BaseKernelRuntime, RunPaths
from .worker_policy import allowed_tools, may_change_repo


class WorkerControlledRuntime(BaseKernelRuntime):
    """Core runtime with model filesystem/tool authority narrowed by role.

    The base runtime owns lifecycle semantics. This focused layer owns only the model boundary:
    tool availability, dirty-tree enforcement and kernel-created commits.
    """

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
