"""Read-only dispatch planning: observe once, decide purely, mutate nothing.

`KernelRuntime.choose_dispatch` decides what to do next only after it has run the stop check
and reaped stale claims, and the reap edits labels and posts comments. A workflow that must
know whether there is any work before it starts a database, installs a toolchain, mints an
App token or launches a model needs the same decision without those effects. This module
holds the pure priority decision (`select_dispatch`) and a read-only observer
(`DispatchPlanner`). Neither reaps, labels, comments, syncs a programme, launches a provider
or reserves budget. A lease that the reaper would release is reported as
`reconciliation_required`; it is never treated as free.

A plan is a proposal, not execution authority. The dispatcher re-observes everything through
`choose_dispatch` before it acts, and a forged workflow output cannot make it act on anything
its own observation does not show (WP00, R00, C04).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping

from .credential_env import scoped_environment
from .execution_fence import ExecutionFenced, fence_status
from .programme import ProgrammeRefused
from .programme_runtime import ProgrammeQueue
from .refusal import rehead_eligible

PLAN_SCHEMA = "dark-factory/dispatch-plan"
PLAN_SCHEMA_VERSION = "1.0"
STATUSES = ("ready", "idle", "blocked")
# Order matters: an earlier cause takes precedence over every later one, so a stopped factory
# with no budget is `stopped`, not `budget_required` (C04: a known stopped/fenced state takes
# precedence over budget; an unavailable control observer blocks).
BLOCK_CODES = ("control_unobserved", "stopped", "fenced", "reconciliation_required", "budget_required")
ACTIONS = ("validate-pr", "rehead-pr", "build-issue", "resume-pr", "materialize-programme")
# Actions that launch a model or judge and therefore need an execution allowance. A re-head is
# model-free; a resume finishes a pushed build from its artifacts; materialising a programme
# candidate creates an issue and calls no model. Idle never needs an allowance.
PAID_ACTIONS = frozenset({"validate-pr", "build-issue"})
PRIORITY = {"priority:critical": 0, "priority:high": 1, "priority:medium": 2, "priority:low": 3}
# The stop script's own words for "I could not read the stop state" (scripts/factory-stop.sh).
# Pinned by tests against the script; an unreadable stop state is not a stop and not a clear.
STOP_UNREADABLE = "cannot read the stop state"
REASONS = {
    "validate-pr": "PR validation has priority",
    "rehead-pr": "stale-base refusal; model-free re-head onto current main",
    "build-issue": "highest-priority accepted issue is idle",
    "resume-pr": "operator resume of a pushed-but-unpublished PR from its uploaded artifacts",
    "materialize-programme": "a current programme item awaits its candidate issue",
    None: "no review PR or accepted idle issue",
}


def labels_of(value: Mapping[str, Any]) -> set[str]:
    return {str(item.get("name")) for item in value.get("labels", []) if isinstance(item, Mapping)}


def issue_dispatch_key(issue: Mapping[str, Any]) -> tuple[int, str, int]:
    """Highest priority label first, then least recently updated, then lowest number."""
    labels = labels_of(issue)
    priority = min((PRIORITY[label] for label in labels if label in PRIORITY), default=4)
    return priority, str(issue.get("updatedAt") or ""), int(issue["number"])


def oldest_number(items: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> int:
    item = min(items, key=lambda row: (str(row.get("updatedAt") or ""), int(row["number"])))
    return int(item["number"])


@dataclass(frozen=True)
class DispatchObservation:
    """One trusted snapshot. Every GitHub read happened before the decision, never inside it."""
    control_observed: bool
    stopped: bool
    fenced: bool
    reconciliation_required: bool
    review: tuple[Mapping[str, Any], ...] = ()
    rehead: tuple[int, ...] = ()
    build: tuple[Mapping[str, Any], ...] = ()
    # True: an allowance is observed available. False: observed missing/exhausted. None: this
    # observer has no read-only view of the allowance (the worker learns it at reservation).
    budget: bool | None = None
    resume: int | None = None
    programme_ready: bool = False
    continuation: str = ""
    reconciliation: tuple[Mapping[str, Any], ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DispatchPlan:
    status: str
    reason_codes: tuple[str, ...]
    action: str | None
    subject: int | None
    reason: str
    mutation_count: int = 0
    observations: Mapping[str, Any] = field(default_factory=dict)

    def record(self, **inputs: Any) -> dict[str, Any]:
        return {"schema": PLAN_SCHEMA, "schema_version": PLAN_SCHEMA_VERSION, "status": self.status,
                "reason_codes": list(self.reason_codes), "action": self.action, "subject": self.subject,
                "reason": self.reason, "mutation_count": self.mutation_count,
                "observations": dict(self.observations), "inputs": dict(inputs),
                "authority": "proposal-only"}


def select_dispatch(observation: DispatchObservation, *,
                    issue_key: Callable[[Mapping[str, Any]], Any] = issue_dispatch_key) -> DispatchPlan:
    """The pure priority decision. No I/O, no clock, no mutation.

    Control before work: an unobserved control plane blocks, then stop, then the fence, then
    a lease the reaper would have to release. Work in the legacy order: review -> eligible
    stale re-head -> admitted accepted issue -> explicit resume/continuation or a programme
    item awaiting its issue -> idle. Only then does budget matter, and only for an action that
    launches a model.
    """
    o = observation
    summary = {
        "review": [int(row["number"]) for row in o.review],
        "rehead": list(o.rehead),
        "build": [int(row["number"]) for row in o.build],
        "budget": {True: "available", False: "unavailable", None: "unobserved"}[o.budget],
        "resume": o.resume, "continuation": o.continuation, "programme_ready": o.programme_ready,
        "reconciliation": [dict(row) for row in o.reconciliation], "notes": list(o.notes),
    }
    blocked = None
    if not o.control_observed:
        blocked = "control_unobserved"
    elif o.stopped:
        blocked = "stopped"
    elif o.fenced:
        blocked = "fenced"
    elif o.reconciliation_required:
        blocked = "reconciliation_required"
    if blocked is not None:
        return DispatchPlan("blocked", (blocked,), None, None, f"blocked: {blocked}", 0, summary)
    action: str | None
    subject: int | None
    if o.resume is not None:
        action, subject = "resume-pr", int(o.resume)
    elif o.review:
        action, subject = "validate-pr", oldest_number(o.review)
    elif o.rehead:
        action, subject = "rehead-pr", int(o.rehead[0])
    elif o.build:
        action, subject = "build-issue", int(min(o.build, key=issue_key)["number"])
    elif o.continuation or o.programme_ready:
        action, subject = "materialize-programme", None
    else:
        action, subject = None, None
    if action in PAID_ACTIONS and o.budget is False:
        return DispatchPlan("blocked", ("budget_required",), None, None,
                            f"{action} #{subject} needs an execution allowance", 0, summary)
    if action is None:
        return DispatchPlan("idle", (), None, None, REASONS[None], 0, summary)
    return DispatchPlan("ready", (), action, subject, REASONS[action], 0, summary)


def load_lease_policy(repo_root: Path) -> Any:
    """The reaper's own decision function, loaded from the script that owns it.

    `scripts/factory_lease.py:decide_reap` is the policy the effectful reap applies; the
    planner asks the same function the same question and reports its answer instead of
    acting on it. Loading the script rather than copying the rule keeps one policy.
    """
    path = Path(repo_root) / "scripts" / "factory_lease.py"
    spec = importlib.util.spec_from_file_location("factory_lease_policy", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"lease policy unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DispatchPlanner:
    """Read-only observer over the same GitHub reads `choose_dispatch` makes.

    `observe_work` is lazy in the legacy order so that, exactly as before, no accepted issue is
    listed while a review PR exists. `observe_programme` is off on the legacy path because the
    old decision never materialised programme candidates; the planning CLI turns it on so a
    programme item with no issue yet is planned work rather than an accidental idle.
    """

    def __init__(self, github: Any, config: Any, *, repo_root: Path, observe_programme: bool = True,
                 runner: Callable[..., Any] = subprocess.run,
                 lease_policy: Any | None = None, log: Callable[[str], None] | None = None) -> None:
        self.github = github
        self.config = config
        self.repo_root = Path(repo_root)
        self.observe_programme = observe_programme
        self.runner = runner
        self._lease_policy = lease_policy
        self.log = log or (lambda line: print(line, flush=True))

    # ---------- control ----------

    def observe_control(self) -> dict[str, Any]:
        """Stop file/issue and protected-main fence, each read tri-state: clear, set, unknown."""
        env = scoped_environment(
            {"FACTORY_REPO": self.config.repository, "FACTORY_WORKDIR": str(self.config.runtime.work_root)},
            scope="github",
        )
        try:
            proc = self.runner(
                ["bash", "scripts/factory-stop.sh"], cwd=self.repo_root, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"observed": False, "stopped": False, "fenced": False,
                    "detail": f"stop check did not run: {type(exc).__name__}"}
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode:
            if STOP_UNREADABLE in output or not output:
                return {"observed": False, "stopped": False, "fenced": False, "detail": output[-500:]}
            return {"observed": True, "stopped": True, "fenced": False, "detail": output[-500:]}
        try:
            fence = fence_status(self.github, self.config.default_branch)
        except ExecutionFenced as exc:
            return {"observed": False, "stopped": False, "fenced": False, "detail": str(exc)[:500]}
        return {"observed": True, "stopped": False, "fenced": fence["state"] != "clear",
                "detail": f"fence={fence['state']} source={fence['source_sha']}"}

    # ---------- reconciliation ----------

    def observe_reconciliation(self) -> dict[str, Any]:
        """What `scripts/factory_lease.py reap` would release right now, without releasing it."""
        try:
            policy = self._lease_policy or load_lease_policy(self.repo_root)
            in_progress = self.config.labels["in_progress"]
            issues = self.github.json([
                "issue", "list", "-R", self.config.repository, "--state", "open", "--label", in_progress,
                "--limit", "1000", "--json", "number,updatedAt,labels",
            ])
            prs = self.github.json([
                "pr", "list", "-R", self.config.repository, "--state", "open", "--limit", "1000",
                "--json", "number,body,labels,url",
            ])
            if not isinstance(issues, list) or not isinstance(prs, list):
                raise RuntimeError("lease inventory was not an array")
            now = policy.now_utc()
            needs = []
            for issue in issues:
                number = int(issue["number"])
                pages = self.github.json([
                    "api", "--paginate", "--slurp",
                    f"repos/{self.config.repository}/issues/{number}/comments?per_page=100",
                ])
                comments = [comment for page in pages for comment in page]
                lease, marker_seen, _comment_id = policy.latest_lease(comments)
                handoff = policy.pr_handoff(number, prs)
                action, reason = policy.decide_reap(
                    now, self.config.labels["accepted"] in labels_of(issue),
                    policy.parse_time(str(issue["updatedAt"])), lease, marker_seen, handoff,
                    self.config.runtime.active_lease_ttl_seconds, self.config.runtime.legacy_lease_ttl_seconds,
                )
                if action == "reap":
                    needs.append({"issue": number, "reason": reason, "handoff": handoff})
        except (RuntimeError, OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            return {"observed": False, "needs": [], "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
        return {"observed": True, "needs": needs, "detail": f"in_progress={len(issues)} would_reap={len(needs)}"}

    # ---------- work ----------

    def observe_work(self) -> dict[str, Any]:
        labels = self.config.labels
        review = tuple(self.github.list_prs(labels["needs_review"]))
        if review:
            return {"review": review, "rehead": (), "build": (), "programme_ready": False}
        # A refused PR whose only fault is that main moved under it is re-headed without a
        # model, before any new build starts: finishing certified work outranks starting more.
        # Every other refusal leaves the PR where it is (section 7). The first eligible PR in
        # the legacy order is the one the dispatcher would pick; later ones are not read.
        for pr in sorted(self.github.list_prs(labels["needs_fix"]),
                         key=lambda row: (str(row.get("updatedAt") or ""), int(row["number"]))):
            number = int(pr["number"])
            # The head decides whether a second re-head is the same certified work meeting a
            # base that moved again, or a pull request that has changed since (D-077).
            if rehead_eligible(self.github.pr_comments(number), head=str(pr.get("headRefOid") or "")):
                return {"review": (), "rehead": (number,), "build": (), "programme_ready": False}
        accepted = self.github.list_issues(labels["accepted"])
        idle = [issue for issue in accepted if labels["in_progress"] not in self.github.labels(issue)]
        # Programme candidates cannot consume model budget until their current binding and
        # predecessor outcomes are checked. Ordinary accepted issues keep their existing route.
        queue = ProgrammeQueue(self.github, self.config.default_branch)
        admitted = []
        for candidate in idle:
            issue = self.github.issue(int(candidate["number"]))
            try:
                queue.admit(issue)
            except ProgrammeRefused as exc:
                self.log(f"FACTORY_PROGRAMME_WAIT issue={issue['number']} reason={exc}")
                continue
            admitted.append(candidate)
        if admitted or not self.observe_programme:
            return {"review": (), "rehead": (), "build": tuple(admitted), "programme_ready": False}
        status = queue.status(labels)
        ready = any(item.get("status") == "ready-for-candidate" for item in status.get("items", []))
        return {"review": (), "rehead": (), "build": (), "programme_ready": ready}

    # ---------- the plan ----------

    def observe(self, *, resume: int | None = None, continuation: str = "") -> DispatchObservation:
        control = self.observe_control()
        notes = [f"control: {control['detail']}"]
        if not control["observed"] or control["stopped"] or control["fenced"]:
            return DispatchObservation(control["observed"], control["stopped"], control["fenced"], False,
                                       resume=resume, continuation=continuation, notes=tuple(notes))
        reconciliation = self.observe_reconciliation()
        notes.append(f"reconciliation: {reconciliation['detail']}")
        if not reconciliation["observed"]:
            return DispatchObservation(False, False, False, False, resume=resume,
                                       continuation=continuation, notes=tuple(notes))
        if reconciliation["needs"]:
            return DispatchObservation(True, False, False, True, resume=resume, continuation=continuation,
                                       reconciliation=tuple(reconciliation["needs"]), notes=tuple(notes))
        try:
            work = self.observe_work()
        except (RuntimeError, OSError, KeyError, TypeError, ValueError, ProgrammeRefused,
                subprocess.SubprocessError) as exc:
            notes.append(f"work: {type(exc).__name__}: {str(exc)[:300]}")
            return DispatchObservation(False, False, False, False, resume=resume,
                                       continuation=continuation, notes=tuple(notes))
        notes.append("budget: no read-only allowance observer; the reservation exchange decides")
        return DispatchObservation(True, False, False, False, review=work["review"], rehead=work["rehead"],
                                   build=work["build"], budget=None, resume=resume,
                                   programme_ready=work["programme_ready"], continuation=continuation,
                                   notes=tuple(notes))

    def plan(self, *, resume: int | None = None, continuation: str = "") -> DispatchPlan:
        return select_dispatch(self.observe(resume=resume, continuation=continuation))
