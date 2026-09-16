"""Opt-in lab adapter, separate from production kernel request construction.

Production requests keep their role-policy bounds. Experimental reservations are
explicit, independently checked here, and cannot change production role policy.
"""
from __future__ import annotations

from dataclasses import replace
import math
import tempfile

from factory_kernel.agents import AgentRequest
from factory_kernel.config import load_config
from factory_kernel.providers import ClaudeCliProvider


class LiveWorker:
    def __init__(self, config_path):
        # No automatic retries: a missing response retains the full reservation.
        config = replace(load_config(config_path).provider, transient_retries=0)
        self.provider = ClaudeCliProvider(config)
        self.model = config.model_overrides.get("implement", config.model)

    def __call__(self, prompt, *, turns, dollars, seconds):
        if type(turns) is not int or not 1 <= turns <= 12:
            raise ValueError("invalid lab turn reservation")
        for value, bound in ((dollars, 5), (seconds, 600)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= bound:
                raise ValueError("invalid lab cost/wall reservation")
        with tempfile.TemporaryDirectory(prefix="feedback-worker-") as cwd:
            reply = self.provider.run(AgentRequest(
                role="implement", prompt=prompt, cwd=cwd, allowed_tools=(), environment={},
                max_turns=turns, max_budget_usd=dollars, timeout_seconds=max(1, math.floor(seconds)),
                effort="low", structured_schema={"type": "object"}))
        value = reply.structured_output
        if not isinstance(value, dict) or set(value) != {"code"}:
            raise ValueError("worker must return exactly one code field")
        return {"code": value["code"], "cost_usd": reply.cost_usd}
