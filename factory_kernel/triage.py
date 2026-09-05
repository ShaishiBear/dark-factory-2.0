"""Bounded issue triage owned by the Python kernel."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .agents import AgentRequest
from .providers import prompt_text
from .runtime import KernelRuntime
from .worker_policy import allowed_tools, max_budget_usd, max_turns, stage_timeout_seconds


PRIORITIES = {"critical", "high", "medium", "low"}
CLASSIFICATIONS = {"bug", "enhancement", "chore", "docs"}
VERDICTS = {"accept", "reject"}

# The labels triage can apply, derived from the same sets the decision validator enforces. The
# worker preflight requires every one of these to exist before dispatching, reading this list from
# the kernel rather than from a second hand-typed list: the second canary dispatch accepted an
# issue and then crashed on `--add-label priority:medium` because the repository had only the
# eight factory:* control labels the workflow checked for (run 33880138411, D-011).
PRIORITY_LABEL_PREFIX = "priority:"
TYPE_LABEL_PREFIX = "type:"
PRIORITY_LABELS = tuple(PRIORITY_LABEL_PREFIX + value for value in sorted(PRIORITIES))
TYPE_LABELS = tuple(TYPE_LABEL_PREFIX + value for value in sorted(CLASSIFICATIONS))


def priority_label(priority: str) -> str:
    if priority not in PRIORITIES:
        raise ValueError(f"priority outside the triage vocabulary: {priority!r}")
    return PRIORITY_LABEL_PREFIX + priority


def type_label(classification: str) -> str:
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"classification outside the triage vocabulary: {classification!r}")
    return TYPE_LABEL_PREFIX + classification


def label_vocabulary(control_labels: Mapping[str, str]) -> tuple[str, ...]:
    """Every label the kernel can apply: the configured factory:* controls plus priority/type."""
    return tuple(sorted(set(control_labels.values()))) + PRIORITY_LABELS + TYPE_LABELS
# How much of each issue body the triage worker sees. Triage judges accept/reject on this window
# and FACTORY_RULES section 1 tells it to reject what looks underspecified, so a window shorter
# than a well-specified issue turns good issues into rejections. 12000 characters holds a long
# issue with background, Given/When/Then criteria, file list and out-of-scope section several
# times over; no prompt or provider budget in this kernel argues for less.
TRIAGE_BODY_CHARS = 12000


class TriageEngine:
    DAILY_NON_OWNER_CAP = 3
    BATCH = 10

    def __init__(self, runtime: KernelRuntime):
        self.runtime = runtime
        self.github = runtime.github
        self.config = runtime.config
        self.repo_root = runtime.repo_root

    def run_once(self) -> int:
        self.runtime.check_stop()
        open_issues = self._issues("open", 100)
        self._refresh_rate_limits(open_issues)
        self._apply_daily_cap()

        # Re-read after rate-limit mutations. Any factory:* label removes an issue from triage.
        open_issues = self._issues("open", 100)
        candidates = [
            self._bounded_issue(issue)
            for issue in open_issues
            if not any(label.startswith("factory:") for label in self._labels(issue))
        ]
        candidates = self._frontier(candidates)[: self.BATCH]
        if not candidates:
            return 0

        open_prs = self.github.json([
            "pr", "list", "-R", self.config.repository, "--state", "open", "--limit", "20",
            "--json", "number,title,body,headRefName",
        ])
        if not isinstance(open_prs, list):
            raise RuntimeError("triage open PR inventory was not an array")
        for pr in open_prs:
            if isinstance(pr, dict):
                pr["body"] = str(pr.get("body") or "")[:800]

        mission = (self.repo_root / "MISSION.md").read_text(encoding="utf-8")
        rules = (self.repo_root / "FACTORY_RULES.md").read_text(encoding="utf-8")
        context = {
            "mission": mission,
            "factory_rules": rules,
            "candidate_issues": candidates,
            "open_prs": open_prs,
        }
        prompt = prompt_text(
            self.config.prompt_path("triage", self.repo_root),
            preamble=(
                "You are a replaceable triage worker. The Python kernel validates the entire "
                "decision set before mutating GitHub."
            ),
            context="TRIAGE INPUT:\n" + json.dumps(context, sort_keys=True),
        )
        # Triage decides from what is in its prompt; it gets no tools, and it is bounded in
        # turns and dollars like every other model call the kernel makes (D-052). It has no run
        # directory, so it is the one call that does not pass through `_agent_stage`.
        result = self.runtime.provider.run(
            AgentRequest(
                role="triage", prompt=prompt, cwd=str(self.repo_root),
                model=self.config.provider.model, environment={},
                structured_schema={"type": "object"},
                allowed_tools=allowed_tools("triage"),
                max_turns=max_turns("triage"),
                max_budget_usd=max_budget_usd("triage"),
                timeout_seconds=stage_timeout_seconds("triage"),
            )
        )
        decisions = self._validate_decisions(result.structured_output, candidates)
        self.runtime.check_stop()
        for decision in decisions:
            self._apply(decision)
        print(f"TRIAGE_OK decisions={len(decisions)}")
        return len(decisions)

    def _issues(self, state: str, limit: int) -> list[Mapping[str, Any]]:
        value = self.github.json([
            "issue", "list", "-R", self.config.repository, "--state", state,
            "--limit", str(limit), "--json", "number,title,body,author,createdAt,labels,state",
        ])
        if not isinstance(value, list):
            raise RuntimeError("triage issue inventory was not an array")
        return value

    def _refresh_rate_limits(self, issues: list[Mapping[str, Any]]) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        label = self.config.labels["rate_limited"]
        for issue in issues:
            created = str(issue.get("createdAt") or "").split("T", 1)[0]
            if label in self._labels(issue) and created and created < today:
                self.github.remove_issue_label(int(issue["number"]), label)

    def _apply_daily_cap(self) -> None:
        owner_raw = self.github.json(["repo", "view", self.config.repository, "--json", "owner"])
        try:
            owner = str(owner_raw["owner"]["login"])
        except (TypeError, KeyError) as exc:
            raise RuntimeError("cannot resolve repository owner for triage rate limit") from exc
        today = datetime.now(timezone.utc).date().isoformat()
        issues = self._issues("all", 200)
        by_author: dict[str, list[Mapping[str, Any]]] = {}
        for issue in issues:
            created = str(issue.get("createdAt") or "")
            author_raw = issue.get("author")
            author = str(author_raw.get("login") or "") if isinstance(author_raw, Mapping) else ""
            if not author or author == owner or not created.startswith(today):
                continue
            by_author.setdefault(author, []).append(issue)

        rate_label = self.config.labels["rate_limited"]
        for author_issues in by_author.values():
            ordered = sorted(author_issues, key=lambda item: str(item.get("createdAt") or ""))
            for issue in ordered[self.DAILY_NON_OWNER_CAP :]:
                if str(issue.get("state") or "").upper() != "OPEN":
                    continue
                number = int(issue["number"])
                if rate_label in self._labels(issue):
                    continue
                self.github.add_issue_label(number, rate_label)
                self.github.comment_issue(
                    number,
                    "**Dark Factory triage:** rate-limited. Non-owner accounts are capped at "
                    f"{self.DAILY_NON_OWNER_CAP} issues per UTC day. This issue will be eligible "
                    "for triage after UTC midnight.",
                )

    def _frontier(self, candidates: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        proc = subprocess.run(
            ["python", "scripts/frontier_filter.py"], cwd=self.repo_root,
            input=json.dumps(candidates), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if proc.returncode:
            raise RuntimeError("frontier filter failed closed: " + (proc.stderr or "")[-2000:])
        try:
            value = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("frontier filter returned invalid JSON") from exc
        if not isinstance(value, list):
            raise RuntimeError("frontier filter output was not an array")
        return value

    def _validate_decisions(
        self, value: object, candidates: list[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if not isinstance(value, Mapping) or value.get("version") != "1.0":
            raise RuntimeError("triage worker returned invalid version")
        raw = value.get("decisions")
        if not isinstance(raw, list):
            raise RuntimeError("triage decisions must be an array")
        expected = {int(issue["number"]) for issue in candidates}
        seen: set[int] = set()
        decisions: list[dict[str, Any]] = []
        for decision in raw:
            if not isinstance(decision, Mapping):
                raise RuntimeError("triage decision must be an object")
            number = decision.get("issue_number")
            if not isinstance(number, int) or isinstance(number, bool) or number not in expected or number in seen:
                raise RuntimeError("triage decision issue identity is missing, unknown or duplicated")
            seen.add(number)
            verdict = decision.get("verdict")
            priority = decision.get("priority")
            classification = decision.get("classification")
            reason = decision.get("reason")
            duplicate = decision.get("duplicate_of")
            if verdict not in VERDICTS or priority not in PRIORITIES or classification not in CLASSIFICATIONS:
                raise RuntimeError(f"triage decision for #{number} has invalid enum values")
            if not isinstance(reason, str) or not reason.strip():
                raise RuntimeError(f"triage decision for #{number} has no reason")
            if duplicate is not None and (
                not isinstance(duplicate, int) or isinstance(duplicate, bool) or duplicate <= 0
            ):
                raise RuntimeError(f"triage decision for #{number} has invalid duplicate_of")
            decisions.append({
                "issue_number": number, "verdict": verdict, "priority": priority,
                "classification": classification, "reason": reason.strip(),
                "duplicate_of": duplicate,
            })
        if seen != expected:
            raise RuntimeError(f"triage did not decide every candidate: missing={sorted(expected - seen)}")
        return decisions

    def _apply(self, decision: Mapping[str, Any]) -> None:
        number = int(decision["issue_number"])
        if decision["verdict"] == "accept":
            for label in (
                self.config.labels["accepted"],
                priority_label(str(decision["priority"])),
                type_label(str(decision["classification"])),
            ):
                self.github.add_issue_label(number, label)
            self.github.comment_issue(
                number,
                "**Dark Factory triage:** accepted\n\n"
                + str(decision["reason"])
                + f"\n\n_Priority: {decision['priority']} | Type: {decision['classification']}_",
            )
            return

        duplicate = decision.get("duplicate_of")
        prefix = (
            f"**Dark Factory triage:** rejected as duplicate of #{duplicate}"
            if duplicate is not None else "**Dark Factory triage:** rejected"
        )
        self.github.comment_issue(number, prefix + "\n\n" + str(decision["reason"]))
        self.github.add_issue_label(number, self.config.labels["rejected"])
        self.github.run([
            "issue", "close", str(number), "-R", self.config.repository, "--reason", "not planned"
        ])

    @staticmethod
    def _labels(issue: Mapping[str, Any]) -> set[str]:
        raw = issue.get("labels", [])
        if not isinstance(raw, list):
            return set()
        return {
            str(item.get("name")) for item in raw
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }

    @staticmethod
    def _bounded_issue(issue: Mapping[str, Any]) -> dict[str, Any]:
        author_raw = issue.get("author")
        author = str(author_raw.get("login") or "") if isinstance(author_raw, Mapping) else ""
        return {
            "number": int(issue["number"]),
            "title": str(issue.get("title") or ""),
            "body": str(issue.get("body") or "")[:TRIAGE_BODY_CHARS],
            "author": author,
            "createdAt": str(issue.get("createdAt") or ""),
            "labels": sorted(TriageEngine._labels(issue)),
        }
