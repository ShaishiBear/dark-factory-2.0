"""Account for each diagnostic process before it starts; never retry an uncertain call."""
import os
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from .execution_client import ExecutionClient
from .frontdoor_intent import IntentRefused
from .github_cli import GitHubClient
from .providers import ClaudeCliProvider, parse_events
from . import publication_policy as policy


@dataclass(frozen=True)
class ProbeRequest:
    """Identity of an already rendered diagnostic command, not a provider AgentRequest."""
    role: str
    argv: list[str]
    cwd: str
    timeout: float | None
    thinking_cap: str | None
    max_budget_usd: int = 1


class ProbeRunner:
    def __init__(self, role, client, *, runner=None):
        if role not in {"diagnostic-route", "diagnostic-effort", "diagnostic-thinking", "diagnostic-scope"}:
            raise IntentRefused("unknown diagnostic role")
        self.role, self.client = role, client
        self.runner = runner or subprocess.run

    @classmethod
    def from_environment(cls, role):
        ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
        # The daily maintenance measurements have their own existing spending scope.
        # No arbitrary Actions workflow, rerun or missing worker key can use that exception.
        maintenance = policy.REPOSITORY + "/.github/workflows/dark-factory-main-regression.yml@refs/heads/main"
        if (not os.environ.get("GITHUB_ACTIONS") and not ref) or ref == maintenance:
            os.environ.pop("FRONTDOOR_AGE_IDENTITY", None)
            return cls(role, None)
        github = GitHubClient(policy.REPOSITORY, cwd=Path.cwd())
        return cls(role, ExecutionClient.from_environment(github))

    def __call__(self, argv, **kwargs):
        # Strip host, GitHub and validation authority, including from the thinking probe's
        # explicitly supplied environment. Retain only the existing model allowlist and
        # the two probe controls intentionally passed by protected probe code.
        supplied = kwargs.get("env", os.environ)
        allowed = ClaudeCliProvider._worker_env({})
        env = {key: value for key, value in supplied.items() if key in allowed}
        for key in ("MAX_THINKING_TOKENS", "ARTIFACTS_DIR"):
            if key in supplied:
                env[key] = supplied[key]
        kwargs = {**kwargs, "env": env}
        if self.client is None:
            return self.runner(argv, **kwargs)
        # The bound on the launched binary must match the one charged to the ledger.
        if (not isinstance(argv, list) or argv.count("--max-budget-usd") != 1
                or argv[argv.index("--max-budget-usd") + 1:] == []
                or argv[argv.index("--max-budget-usd") + 1] != "1"):
            raise IntentRefused("diagnostic requires its protected one-dollar CLI bound")
        request = ProbeRequest(role=self.role, argv=list(argv), cwd=str(kwargs.get("cwd", Path.cwd())),
            timeout=kwargs.get("timeout"), thinking_cap=env.get("MAX_THINKING_TOKENS"))
        result = None

        def run(_request, **_options):
            nonlocal result
            result = self.runner(argv, **kwargs)
            events = parse_events(result.stdout or "")
            receipts = [row for row in events if row.get("type") == "result"]
            # Missing/ambiguous/failed telemetry blocks the next reservation. Probe output
            # can still report a failed measurement; it cannot turn unknown spend into zero.
            cost = receipts[0].get("total_cost_usd") if len(receipts) == 1 and result.returncode == 0 else None
            return SimpleNamespace(cost_usd=cost)

        self.client.run(SimpleNamespace(run=run), request)
        return result


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] != ["--"] or len(args) < 3:
        raise IntentRefused("expected -- followed by the bounded diagnostic command")
    result = ProbeRunner.from_environment("diagnostic-route")(
        args[1:], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=190)
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
