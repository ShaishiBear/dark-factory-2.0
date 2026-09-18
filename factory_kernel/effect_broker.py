"""The effect broker: fixed effects behind a grant, with a durable journal (SPECIFICATION 5, C05).

Boundary of this module today, stated so nobody reads more into it: the broker is an object in
the same process as the orchestration that asks it. It is NOT the separate-OS-identity service
SPEC 5 requires for the reduced-trust claim; no TCB reduction is claimed and legacy
orchestration stays classified trusted (`.factory/tcb.json`). What it does establish now is the
protocol: a caller presents a grant (`capabilities.authorize`) and the broker re-observes the
subject and the controls itself, consumes the grant exactly once in a durable journal, performs
exactly one fixed remote operation bound to the expected head, and records what it observed.
Timeouts stay `uncertain`; nothing replays.

Fixed operations: `merge_exact_head` (wraps `GitHubClient.merge_squash`), `publish_candidate`
(wraps `push_branch` + `create_pr` for a branch the broker re-derives from its worktree: head,
tree, branch name and a clean status, through an injected git reader) and `observe` (read-only).
The other three names of the contract (`commit_acceptance`, `commit_implementation`,
`publish_transition_data`) are reserved here and refuse with `operation_not_served`; their call
sites still spend through `git_authority` and `GitHubClient` directly, as the TCB record says.

Journal sequence for one effect (C05):
  refused  -> the grant or the re-observed subject failed a gate; no remote call
  started  -> grant consumed, durably, before any remote call
  observed_failure -> a control changed between start and call (no remote call), or the call
                      raised before/without acceptance (the remote may still have acted; the
                      record says `remote_state: unverified` and recovery observes the remote)
  observed_success -> the call returned and the independent observation shows the effect
  uncertain -> the call timed out or the observation could not be made; never replayed
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping

from .canonical import sha256_value
from .capabilities import FIXED_OPERATIONS, Grant, Refusal, validate_grant

SERVED_OPERATIONS = ("merge_exact_head", "publish_candidate", "observe")
STATES = ("refused", "started", "observed_success", "observed_failure", "uncertain")


class EffectRefused(RuntimeError):
    """The broker did not perform the effect. Carries the reason codes."""

    def __init__(self, reason_codes: tuple[str, ...], detail: str = "") -> None:
        super().__init__(f"effect refused: {','.join(reason_codes)}" + (f" ({detail})" if detail else ""))
        self.reason_codes = reason_codes
        self.detail = detail


class EffectUncertain(RuntimeError):
    """The remote call may or may not have happened. Recovery observes the remote; nothing replays."""


class EffectJournal:
    """An append-only JSON-lines journal of grant consumption and observation, on disk before
    the remote call (`started`) and after it. Replays are answered from the journal: an
    identical request that was observed returns its observation without executing; a started
    or uncertain one refuses."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def rows(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def _append(self, row: Mapping[str, Any]) -> dict:
        record = dict(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
        return record

    def state_of(self, grant_id: str) -> tuple[str | None, dict | None]:
        """The latest state recorded for a grant and that row, or (None, None)."""
        latest: dict | None = None
        for row in self.rows():
            if row.get("grant_id") == grant_id:
                latest = row
        return (latest.get("state"), latest) if latest else (None, None)

    def uses(self, grant_id: str) -> int:
        return sum(1 for row in self.rows() if row.get("grant_id") == grant_id and row.get("state") == "started")

    def record(self, state: str, grant: Grant, *, at: int, **fields: Any) -> dict:
        if state not in STATES:
            raise ValueError(f"unknown journal state {state!r}")
        return self._append({"schema": "dark-factory/effect-journal", "schema_version": "1.0", "state": state, "at": at,
                             "grant_id": grant.grant_id, "semantic_operation": grant.semantic_operation, "request_id": grant.request_id,
                             "request_sha256": grant.request_sha256, "subject": dict(grant.subject), "grant": grant.to_dict(), **fields})


@dataclass
class EffectResult:
    state: str
    grant_id: str
    observation: dict
    replayed: bool = False

    def to_dict(self) -> dict:
        return {"state": self.state, "grant_id": self.grant_id, "observation": dict(self.observation), "replayed": self.replayed}


class EffectBroker:
    """`github` is the existing `GitHubClient` (its `pr` read and `merge_squash` spend);
    `stop_check` is the caller's fresh control check (raises when stopped or fenced);
    `epoch` is the current revocation epoch; `clock` returns integer seconds."""

    def __init__(self, github: Any, journal: EffectJournal, *, stop_check: Callable[[], None], epoch: str,
                 clock: Callable[[], int] | None = None, git: Callable[..., str] | None = None) -> None:
        self.github = github
        self.journal = journal
        self.stop_check = stop_check
        self.epoch = epoch
        self.clock = clock or (lambda: int(time.time()))
        # `git(*args, cwd=Path) -> str` reads the worktree a candidate is published from; the broker
        # re-derives the candidate's identity with it and never takes the caller's word.
        self.git = git

    # ---- gates ----------------------------------------------------------------------------

    def _observe_pr(self, grant: Grant) -> dict:
        info = self.github.pr(int(grant.subject["pr_number"]), holdout_safe=True)
        if not isinstance(info, Mapping):
            raise EffectRefused(("subject_unobservable",), "PR view returned no record")
        return {"state": info.get("state"), "headRefOid": info.get("headRefOid"), "baseRefOid": info.get("baseRefOid"),
                "mergeCommit": (info.get("mergeCommit") or {}).get("oid") if isinstance(info.get("mergeCommit"), Mapping) else info.get("mergeCommit"),
                "mergedAt": info.get("mergedAt")}

    def _observe_candidate(self, grant: Grant, cwd: Any) -> dict:
        """The candidate as the worktree reports it now: head, tree, branch, cleanliness."""
        if self.git is None:
            raise EffectRefused(("candidate_unobservable",), "no git reader was given to the broker")
        try:
            head = self.git("rev-parse", "HEAD", cwd=cwd)
            tree = self.git("rev-parse", "HEAD^{tree}", cwd=cwd)
            branch = self.git("branch", "--show-current", cwd=cwd)
            status = self.git("status", "--porcelain", cwd=cwd)
        except Exception as exc:
            raise EffectRefused(("candidate_unobservable",), f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        return {"head_sha": str(head).strip(), "head_tree_sha": str(tree).strip(), "branch": str(branch).strip(),
                "dirty": bool(str(status).strip())}

    def _gate(self, grant: Grant, operation: str, *, expected_head: str | None, cwd: Any = None) -> dict:
        """Everything that must hold before the grant is consumed. Refusals are journaled."""
        now = self.clock()
        reasons: list[str] = []
        if operation not in FIXED_OPERATIONS:
            reasons.append("operation_not_fixed")
        elif operation not in SERVED_OPERATIONS:
            reasons.append("operation_not_served")
        refusal = validate_grant(grant, now=now, epoch=self.epoch, uses=self.journal.uses(grant.grant_id), operation=operation)
        if isinstance(refusal, Refusal):
            reasons.extend(refusal.reason_codes)
        if expected_head is not None and expected_head != grant.subject["head_sha"]:
            reasons.append("expected_head_differs_from_grant")
        state, prior = self.journal.state_of(grant.grant_id)
        if state in ("started", "uncertain"):
            reasons.append("prior_operation_pending_or_uncertain")
        observed = {}
        if not reasons and operation == "publish_candidate":
            try:
                observed = self._observe_candidate(grant, cwd)
            except EffectRefused as exc:
                reasons.extend(exc.reason_codes)
            else:
                if observed["head_sha"] != grant.subject["head_sha"]:
                    reasons.append("candidate_head_differs")
                if observed["head_tree_sha"] != grant.subject["head_tree_sha"]:
                    reasons.append("candidate_tree_differs")
                if observed["branch"] != grant.subject["branch"]:
                    reasons.append("candidate_branch_differs")
                if observed["dirty"]:
                    reasons.append("candidate_worktree_dirty")
        elif not reasons:
            try:
                observed = self._observe_pr(grant)
            except EffectRefused as exc:
                reasons.extend(exc.reason_codes)
            else:
                if operation == "merge_exact_head":
                    if observed["state"] != "OPEN":
                        reasons.append("subject_not_open")
                    if observed["headRefOid"] != grant.subject["head_sha"]:
                        reasons.append("subject_head_moved")
        if reasons:
            self.journal.record("refused", grant, at=now, reason_codes=sorted(set(reasons)), observation=observed)
            raise EffectRefused(tuple(sorted(set(reasons))))
        return observed

    # ---- operations -----------------------------------------------------------------------

    def observe(self, grant: Grant) -> EffectResult:
        """Read-only: the subject as the platform reports it now. Consumes a use like any effect
        so an observer grant cannot be handed around indefinitely."""
        observed = self._gate(grant, "observe", expected_head=None)
        now = self.clock()
        self.journal.record("started", grant, at=now)
        self.journal.record("observed_success", grant, at=now, observation=observed, remote_call=False)
        return EffectResult("observed_success", grant.grant_id, observed)

    def publish_candidate(self, grant: Grant, *, cwd: Any, base_branch: str, title: str, body_file: Any) -> EffectResult:
        """Push the candidate branch and open its PR, once. The branch, head and tree are the
        grant's, re-derived from the worktree by the broker immediately before the push; the
        PR is opened against `base_branch` and re-observed at the granted head."""
        state, prior = self.journal.state_of(grant.grant_id)
        if (state == "observed_success" and prior is not None and prior.get("semantic_operation") == "publish_candidate"
                and prior.get("request_sha256") == grant.request_sha256):
            return EffectResult(state, grant.grant_id, dict(prior.get("observation") or {}), replayed=True)
        observed_before = self._gate(grant, "publish_candidate", expected_head=None, cwd=cwd)
        if not isinstance(base_branch, str) or not base_branch.strip() or base_branch.startswith("-"):
            self.journal.record("refused", grant, at=self.clock(), reason_codes=["base_branch_invalid"], observation=observed_before)
            raise EffectRefused(("base_branch_invalid",))
        self.journal.record("started", grant, at=self.clock(), observation=observed_before)
        try:
            self.stop_check()
        except Exception as exc:
            self.journal.record("observed_failure", grant, at=self.clock(), remote_call=False, remote_state="not_called",
                                detail=f"control changed before the call: {type(exc).__name__}: {str(exc)[:300]}")
            raise
        branch = grant.subject["branch"]
        head = grant.subject["head_sha"]
        try:
            self.github.push_branch(branch)
        except subprocess.TimeoutExpired as exc:
            self.journal.record("uncertain", grant, at=self.clock(), remote_call=True, remote_state="push_unknown",
                                detail=f"push timed out: {str(exc)[:300]}")
            raise EffectUncertain(f"push of {branch} at {head} timed out; the remote may hold the branch. Observe, do not replay.") from exc
        except Exception as exc:
            self.journal.record("observed_failure", grant, at=self.clock(), remote_call=True, remote_state="push_unverified",
                                detail=f"{type(exc).__name__}: {str(exc)[:300]}")
            raise
        try:
            created = self.github.create_pr(head=branch, base=base_branch, title=title, body_file=body_file)
        except subprocess.TimeoutExpired as exc:
            self.journal.record("uncertain", grant, at=self.clock(), remote_call=True, remote_state="pushed_pr_unknown",
                                detail=f"PR creation timed out: {str(exc)[:300]}")
            raise EffectUncertain(f"PR creation for {branch} timed out after the push; observe, do not replay.") from exc
        except Exception as exc:
            self.journal.record("observed_failure", grant, at=self.clock(), remote_call=True, remote_state="pushed_pr_unverified",
                                detail=f"{type(exc).__name__}: {str(exc)[:300]}")
            raise
        observation = {"pr_number": created.get("number") if isinstance(created, Mapping) else None,
                       "headRefOid": created.get("headRefOid") if isinstance(created, Mapping) else None,
                       "baseRefOid": created.get("baseRefOid") if isinstance(created, Mapping) else None,
                       "state": created.get("state") if isinstance(created, Mapping) else None,
                       "url": created.get("url") if isinstance(created, Mapping) else None, "branch": branch}
        if type(observation["pr_number"]) is not int or observation["pr_number"] <= 0:
            self.journal.record("uncertain", grant, at=self.clock(), remote_call=True, remote_state="pushed_pr_unobserved",
                                detail="PR creation returned no number", observation=observation)
            raise EffectUncertain(f"PR creation for {branch} returned without a PR number; observe, do not replay.")
        if observation["headRefOid"] not in (None, head):
            self.journal.record("observed_failure", grant, at=self.clock(), remote_call=True, remote_state="pr_at_other_head",
                                detail=f"created PR head {observation['headRefOid']} is not the granted head", observation=observation)
            raise EffectRefused(("created_pr_head_differs",), f"PR #{observation['pr_number']} is at {observation['headRefOid']}, granted {head}")
        self.journal.record("observed_success", grant, at=self.clock(), remote_call=True, remote_state="published",
                            observation=observation, observation_sha256=sha256_value(observation))
        return EffectResult("observed_success", grant.grant_id, observation)

    def merge_exact_head(self, grant: Grant, *, expected_head: str) -> EffectResult:
        """The one squash merge, bound to the exact authorised head three times: the grant's
        subject, the caller's expected head, and `merge_squash`'s own re-read plus GitHub's
        `--match-head-commit`."""
        state, prior = self.journal.state_of(grant.grant_id)
        if (state == "observed_success" and prior is not None and prior.get("semantic_operation") == "merge_exact_head"
                and prior.get("request_sha256") == grant.request_sha256 and expected_head == grant.subject["head_sha"]):
            # An identical replay of THIS operation's observed success references what was observed; it
            # never executes again. Every other prior state goes through the gates and is refused there.
            return EffectResult(state, grant.grant_id, dict(prior.get("observation") or {}), replayed=True)
        observed_before = self._gate(grant, "merge_exact_head", expected_head=expected_head)
        started_at = self.clock()
        self.journal.record("started", grant, at=started_at, observation=observed_before)
        # Fresh controls immediately before the remote call: a stop or fence observed now means
        # no call, recorded as a failure that spent nothing remote.
        try:
            self.stop_check()
        except Exception as exc:
            self.journal.record("observed_failure", grant, at=self.clock(), remote_call=False, remote_state="not_called",
                                detail=f"control changed before the call: {type(exc).__name__}: {str(exc)[:300]}")
            raise
        head = grant.subject["head_sha"]
        number = int(grant.subject["pr_number"])
        try:
            self.github.merge_squash(number, expected_head=head)
        except subprocess.TimeoutExpired as exc:
            self.journal.record("uncertain", grant, at=self.clock(), remote_call=True, remote_state="unknown",
                                detail=f"merge call timed out: {str(exc)[:300]}")
            raise EffectUncertain(f"merge of PR #{number} at {head} timed out; the remote may have merged. Observe, do not replay.") from exc
        except Exception as exc:
            self.journal.record("observed_failure", grant, at=self.clock(), remote_call=True, remote_state="unverified",
                                detail=f"{type(exc).__name__}: {str(exc)[:300]}")
            raise
        try:
            observed_after = self._observe_pr(grant)
        except Exception as exc:  # the merge returned; only the observation is missing
            self.journal.record("uncertain", grant, at=self.clock(), remote_call=True, remote_state="returned_unobserved",
                                detail=f"observation failed: {type(exc).__name__}: {str(exc)[:300]}")
            raise EffectUncertain(f"merge of PR #{number} returned but could not be observed: {exc}") from exc
        merged = observed_after.get("state") == "MERGED" or bool(observed_after.get("mergedAt")) or bool(observed_after.get("mergeCommit"))
        self.journal.record("observed_success", grant, at=self.clock(), remote_call=True,
                            remote_state="merged" if merged else "returned_not_yet_visible", observation=observed_after,
                            observation_sha256=sha256_value(observed_after))
        return EffectResult("observed_success", grant.grant_id, observed_after)


__all__ = ["SERVED_OPERATIONS", "STATES", "EffectBroker", "EffectJournal", "EffectRefused", "EffectResult", "EffectUncertain"]
