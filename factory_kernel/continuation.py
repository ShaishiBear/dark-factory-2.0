"""Bounded scheduler continuation, never product or proof authority."""
from __future__ import annotations

import re
from typing import Mapping

from .programme import ProgrammeRefused

WORKFLOW = "dark-factory-worker.yml"
MAX_CONTINUATIONS = 8


def pulse_context(*, programme: str, remaining: str, parent: str) -> dict:
    """Validate and record inputs before candidate creation or paid worker setup."""
    if not re.fullmatch(r"[0-8]", remaining):
        raise ProgrammeRefused("continuation remaining must be an integer from 0 through 8")
    if programme and not re.fullmatch(r"[0-9a-f]{64}", programme):
        raise ProgrammeRefused("continuation programme must be a canonical hash")
    if parent and not re.fullmatch(r"[1-9][0-9]{0,19}", parent):
        raise ProgrammeRefused("continuation parent must be a positive run ID")
    return {"programme": programme or None, "remaining": int(remaining), "parent_run": parent or None}


def continue_programme(queue, check_stop, *, programme: str, remaining: str,
                       advanced: str, dispatch_result: str, merge_result: str,
                       context: Mapping[str, str]) -> dict:
    """Dispatch once, or explain why not. No retry after an uncertain POST response.

    The successor enters the same global workflow lock and the same admission/proof path.
    Its scope hash and decreasing counter travel as inputs. Labels and status projections
    never grant merge authority here; this command cannot push code or create product issues.
    """
    if not re.fullmatch(r"[0-8]", remaining):
        raise ProgrammeRefused("continuation remaining must be an integer from 0 through 8")
    count = int(remaining)
    if count > MAX_CONTINUATIONS:
        raise ProgrammeRefused("continuation exceeds the configured hard limit")
    repo, branch = queue.github.repository, queue.default_branch
    if (context.get("GITHUB_REPOSITORY") != repo
            or context.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}
            or context.get("GITHUB_REF") != f"refs/heads/{branch}"
            or context.get("GITHUB_WORKFLOW_REF") != f"{repo}/.github/workflows/{WORKFLOW}@refs/heads/{branch}"
            or not re.fullmatch(r"[1-9][0-9]*", context.get("GITHUB_RUN_ID", ""))):
        raise ProgrammeRefused("continuation requires the canonical protected-main worker run")
    if dispatch_result != "success" or merge_result not in {"success", "skipped"}:
        return {"status": "stopped", "reason": "prior-action-not-successful"}
    if advanced != "true":
        return {"status": "stopped", "reason": "no-progress"}
    if count == 0:
        return {"status": "stopped", "reason": "continuation-limit"}
    if not re.fullmatch(r"[0-9a-f]{64}", programme):
        return {"status": "stopped", "reason": "no-bound-programme"}
    check_stop()
    current = queue.current()
    if current is None or current.sha256 != programme:
        return {"status": "stopped", "reason": "programme-changed"}
    check_stop()
    queue.github.run([
        "workflow", "run", WORKFLOW, "-R", repo, "--ref", branch,
        "-f", f"continuation_programme={programme}",
        "-f", f"continuation_remaining={count - 1}",
        "-f", f"continuation_parent={context['GITHUB_RUN_ID']}",
    ])
    return {"status": "dispatched", "programme": programme, "remaining": count - 1,
            "parent_run": context["GITHUB_RUN_ID"]}
