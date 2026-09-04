"""Concrete model-CLI workers for the repo-owned factory.

Workers are deliberately untrusted: successful model execution never equals engineering authority.
The deterministic kernel, harness, holdouts and merge verifier remain the judges.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .agents import AgentRequest, AgentResult, ProviderCapabilities
from .config import ProviderConfig


class ClaudeCliProvider:
    provider_id = "claude-cli"
    capabilities = ProviderCapabilities(
        structured_output=True,
        session_resume=False,
        session_fork=False,
        tool_restrictions=True,
        web_search=False,
    )
    REQUEST_ENV = frozenset(
        {"ARTIFACTS_DIR", "FACTORY_BASE_REF", "FACTORY_REPO", "FACTORY_WORKDIR"}
    )

    def __init__(self, config: ProviderConfig):
        if config.provider_id != self.provider_id:
            raise ValueError(
                f"provider configuration id {config.provider_id!r} does not match {self.provider_id!r}"
            )
        self.config = config

    @classmethod
    def _worker_env(cls, extra: Mapping[str, str]) -> dict[str, str]:
        """Do not leak GitHub/application validation secrets into model subprocesses.

        Claude authentication/provider variables remain available, as do ordinary process/runtime
        variables required to launch the CLI. Repository/GitHub and application credentials stay
        with deterministic kernel authorities. Request-local environment is separately whitelisted
        so a future caller cannot accidentally punch a secret through this boundary.
        """
        exact = {
            "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP",
            "LANG", "LC_ALL", "TERM", "CI", "NO_COLOR", "XDG_CONFIG_HOME",
        }
        prefixes = (
            "ANTHROPIC_", "CLAUDE_", "AWS_", "GOOGLE_", "AZURE_",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key in exact or key.startswith(prefixes)
        }
        env.update(
            {
                str(key): str(value)
                for key, value in extra.items()
                if key in cls.REQUEST_ENV
            }
        )
        return env

    def run(self, request: AgentRequest) -> AgentResult:
        # The final semantic architecture holdout deliberately uses a different model family
        # from ordinary build/review workers. It is still an untrusted model judgment; the
        # deterministic architecture guard and Evidence Bundle remain authoritative.
        model = (
            self.config.architecture_model
            if request.role == "architecture-holdout" and self.config.architecture_model
            else request.model or self.config.model
        )
        tools = tuple(request.allowed_tools or ())
        tool_names = ",".join(tools)
        argv = [
            self.config.binary,
            "--bare",
            "-p", request.prompt,
            "--model", model,
            "--permission-mode", "dontAsk",
            "--tools", tool_names,
            "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands",
        ]
        # Run artifacts live outside the checkout. Explicitly grant only that one additional
        # directory so workers can emit their requested JSON/Markdown without broad filesystem
        # write access. Claude's normal working-directory boundary still applies to repository
        # files; kernel Git authority rejects any repo mutation outside the role's exact envelope.
        artifacts = str(request.environment.get("ARTIFACTS_DIR", "")).strip()
        if artifacts:
            artifact_path = Path(artifacts)
            if not artifact_path.is_dir():
                raise RuntimeError(f"worker artifact directory does not exist: {artifacts}")
            argv.extend(["--add-dir", str(artifact_path)])
        if tools:
            argv.extend(["--allowedTools", tool_names])
        env = self._worker_env(request.environment)
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


def prompt_text(path: Path, *, preamble: str = "", methods: str = "", context: str = "") -> str:
    """Preamble, role prompt, pinned method text, then run context. Methods sit between the
    role prompt and the context so the discipline is stated before the specifics it applies to."""
    body = path.read_text(encoding="utf-8")
    parts = [part.strip() for part in (preamble, body, methods, context) if part.strip()]
    return "\n\n".join(parts) + "\n"
