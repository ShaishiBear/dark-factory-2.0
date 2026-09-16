"""Tool-less, capped exploration calls over the existing encrypted proposal transport."""
from __future__ import annotations

import tempfile
from dataclasses import replace

from .agents import AgentRequest
from .canonical import canonical_bytes
from .exploration_policy import number
from .frontdoor_intent import IntentRefused, _shape
from .programme import parse_json
from .worker_policy import PREFLIGHT_ROLES, allowed_tools, effort, max_budget_usd, max_turns, stage_timeout_seconds

SCHEMA = "dark-factory/hosted-exploration-v1"


def validate_limits(role, limits):
    _shape(limits, {"max_usd", "max_turns", "timeout_seconds"})
    if (role not in PREFLIGHT_ROLES or not 0 < number(limits["max_usd"]) <= min(1, max_budget_usd(role))
            or type(limits["max_turns"]) is not int or limits["max_turns"] != min(5, max_turns(role))
            or type(limits["timeout_seconds"]) is not int
            or limits["timeout_seconds"] != min(338, stage_timeout_seconds(role))):
        raise IntentRefused("hosted exploration exceeds protected role limits")
    return limits


def request_limits(request):
    if (request.role not in PREFLIGHT_ROLES or request.allowed_tools != allowed_tools(request.role)
            or request.environment or request.model is not None or request.structured_schema is not None
            or request.path_scope is not None or request.effort != effort(request.role)):
        raise IntentRefused("hosted exploration cannot add tools, credentials or model overrides")
    return validate_limits(request.role, {"max_usd": request.max_budget_usd,
        "max_turns": request.max_turns, "timeout_seconds": request.timeout_seconds})


def execute(payload, provider, check_stop):
    """Called only after the existing worker has authenticated/decrypted/replay-checked the job."""
    role = payload["role"]
    limits = validate_limits(role, payload["limits"])
    check_stop()
    with tempfile.TemporaryDirectory(prefix="factory-hosted-exploration-") as directory:
        request = AgentRequest(role=role, prompt=payload["prompt"], cwd=directory,
            allowed_tools=allowed_tools(role), environment={}, effort=effort(role),
            max_turns=max_turns(role), max_budget_usd=max_budget_usd(role),
            timeout_seconds=stage_timeout_seconds(role))
        request = replace(request, max_turns=limits["max_turns"], max_budget_usd=limits["max_usd"],
                          timeout_seconds=limits["timeout_seconds"])
        result = provider.run(request)
    # Preserve reported spend even when the host must refuse stale/over-budget output.
    raw = canonical_bytes(result.structured_output) if result.structured_output is not None else result.content.encode()
    if len(raw) > 50000:
        raise IntentRefused("hosted exploration response exceeds bound")
    return parse_json(raw.decode()), {"model": result.model, "cost_usd": result.cost_usd}
