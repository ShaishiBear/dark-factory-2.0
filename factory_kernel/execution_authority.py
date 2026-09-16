"""Read canonical worker provenance independently before issuing a spending capability."""
from pathlib import Path

from .exploration_repository import observe_protected_files
from .frontdoor_intent import IntentRefused
from .programme import compile_programme
from .publication_observation import _complete
from .publication_source import observe_publication_source
from . import publication_policy as policy

WORKFLOW = "dark-factory-worker.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW
PROGRAMS = ("execution_authority.py", "execution_exchange.py", "execution_budget.py",
            "worker_policy.py", "frontdoor_intent.py", "frontdoor_http.py", "canonical.py",
            "programme.py", "programme_runtime.py", "publication_source.py", "execution_fence.py",
            "exploration_repository.py", "frontdoor_control.py", "publication_observation.py",
            "exploration_records.py", "publication_policy.py", "programme_strategy.py",
            "strategy_rules.py", "manifest.py")


class ExecutionAuthority:
    def __init__(self, github):
        self.github = github
        self._verified = set()

    def _programs(self, revision):
        if revision in self._verified:
            return  # Immutable Git objects; this authority instance belongs to one host process.
        paths = ["factory_kernel/" + name for name in PROGRAMS]
        def compare(current, _paths, read):
            if current != revision:
                raise IntentRefused("execution authority source changed")
            for name in PROGRAMS:
                raw = read("factory_kernel/" + name, 100000)
                local = Path(__file__).with_name(name).read_bytes()
                if raw.replace(b"\r\n", b"\n") != local.replace(b"\r\n", b"\n"):
                    raise IntentRefused("execution authority differs from protected source")
        observe_protected_files(self.github, paths, (), compare)
        self._verified.add(revision)

    def __call__(self, call, phase):
        github = self.github
        if github.repository != policy.REPOSITORY:
            raise IntentRefused("execution authority repository refused")
        prefix = "repos/" + github.repository
        workflow = github.json(["api", f"{prefix}/actions/workflows/{WORKFLOW}"])
        run = github.json(["api", f"{prefix}/actions/runs/{call['run_id']}"])
        allowed_status = {"in_progress", "completed"} if phase == "observe" else {"in_progress"}
        if (type(workflow.get("id")) is not int or workflow["id"] <= 0
                or workflow.get("path") != WORKFLOW_PATH or workflow.get("state") != "active"
                or type(run.get("id")) is not int or run["id"] != call["run_id"]
                or type(run.get("workflow_id")) is not int or run["workflow_id"] != workflow["id"]
                or run.get("path") != WORKFLOW_PATH or run.get("head_branch") != "main"
                or run.get("head_sha") != call["source_sha"] or run.get("run_attempt") != 1
                or type(run.get("run_attempt")) is not int or call["run_attempt"] != 1
                or run.get("event") not in {"schedule", "workflow_dispatch"}
                or run.get("status") not in allowed_status
                or run.get("head_repository", {}).get("full_name") != github.repository
                or run.get("actor", {}).get("login") not in {policy.OWNER, policy.APP_LOGIN}
                or run.get("triggering_actor", {}).get("login") not in {policy.OWNER, policy.APP_LOGIN}):
            raise IntentRefused("execution workflow, run, source or actor provenance refused")
        jobs = _complete(github.json(["api", f"{prefix}/actions/runs/{call['run_id']}/attempts/1/jobs?per_page=100"]), "jobs")
        dispatch = [row for row in jobs if row.get("name") == "dispatch"]
        if (len(dispatch) != 1 or dispatch[0].get("status") not in allowed_status
                or dispatch[0].get("run_id") != call["run_id"]
                or dispatch[0].get("head_sha") != call["source_sha"]):
            raise IntentRefused("execution dispatch job provenance is missing or ambiguous")
        if phase == "observe":
            # Stop or main drift cannot erase charges. The exchange additionally requires
            # this exact previously reserved and started call, and never refunds its amount.
            return None
        source = observe_publication_source(github)
        if (source["main_sha"] != call["source_sha"] or source["stop"] != {"state": "clear", "issues": []}
                or source["active_input"] is None
                or compile_programme(source["active_input"], repository=github.repository).sha256 != call["programme_sha256"]):
            raise IntentRefused("execution worker does not name the current clear programme and source")
        self._programs(source["main_sha"])
        # The job may have ended or been cancelled while source facts were read.
        latest = github.json(["api", f"{prefix}/actions/runs/{call['run_id']}"])
        if any(latest.get(key) != run.get(key) for key in
               ("id", "workflow_id", "path", "head_branch", "head_sha", "run_attempt", "event", "status",
                "head_repository", "actor", "triggering_actor")):
            raise IntentRefused("execution run changed during observation")
        return source
