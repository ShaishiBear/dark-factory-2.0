"""Concrete model-CLI workers for the repo-owned factory.

Workers are deliberately untrusted: successful model execution never equals engineering authority.
The deterministic kernel, harness, holdouts and merge verifier remain the judges.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .agents import AgentRequest, AgentResult, ProviderCapabilities
from .config import ProviderConfig


class ClaudeCliProvider:
    provider_id = "claude-cli"
    capabilities = ProviderCapabilities(
        structured_output=True,
        session_resume=False,
        session_fork=False,
        tool_restrictions=False,
        web_search=False,
    )

    def __init__(self, config: ProviderConfig):
        if config.provider_id != self.provider_id:
            raise ValueError(
                f"provider configuration id {config.provider_id!r} does not match {self.provider_id!r}"
            )
        self.config = config

    def run(self, request: AgentRequest) -> AgentResult:
        # The final semantic architecture holdout deliberately uses a different model family
        # from ordinary build/review workers. It is still an untrusted model judgment; the
        # deterministic architecture guard and Evidence Bundle remain authoritative.
        model = (
            self.config.architecture_model
            if request.role == "architecture-holdout" and self.config.architecture_model
            else request.model or self.config.model
        )
        argv = [self.config.binary, "-p", request.prompt, "--model", model]
        env = dict(os.environ)
        env.update(request.environment)
        proc = subprocess.run(
            argv,
            cwd=request.cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.config.timeout_seconds,
        )
        output = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode:
            detail = (output + "\n" + stderr)[-4000:]
            raise RuntimeError(
                f"agent worker failed role={request.role!r} rc={proc.returncode}: {detail}"
            )

        structured: Any | None = None
        if request.structured_schema is not None:
            structured = _extract_json(output)
            if structured is None:
                raise RuntimeError(
                    f"agent worker role={request.role!r} did not return parseable JSON"
                )
        return AgentResult(
            provider_id=self.provider_id,
            model=model,
            content=output,
            structured_output=structured,
        )


def _extract_json(text: str) -> Any | None:
    candidates = [text]
    if "```" in text:
        pieces = text.split("```")
        candidates.extend(piece.removeprefix("json").strip() for piece in pieces[1::2])
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        start = min(
            [index for index in (candidate.find("{"), candidate.find("[")) if index >= 0],
            default=-1,
        )
        if start < 0:
            continue
        for end in range(len(candidate), start, -1):
            try:
                return json.loads(candidate[start:end])
            except json.JSONDecodeError:
                continue
    return None


def prompt_text(path: Path, *, preamble: str = "", context: str = "") -> str:
    body = path.read_text(encoding="utf-8")
    parts = [part.strip() for part in (preamble, body, context) if part.strip()]
    return "\n\n".join(parts) + "\n"
