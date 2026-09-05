"""Concrete model-CLI workers for the repo-owned factory.

Workers are deliberately untrusted: successful model execution never equals engineering authority.
The deterministic kernel, harness, holdouts and merge verifier remain the judges.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping

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

    def run(
        self,
        request: AgentRequest,
        *,
        before_retry: Callable[[int], None] | None = None,
    ) -> AgentResult:
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
            # The CLI returns one JSON envelope: the worker's text under `result`, plus
            # `is_error`, `num_turns`, `duration_ms` and `total_cost_usd`. The kernel unwraps
            # it below, so every consumer of AgentResult.content sees exactly the text it saw
            # before; the telemetry is what makes a slow stage measurable (D-020).
            "--output-format", "json",
        ]
        # A worker is a bounded loop, never an open-ended one. Without this cap the only backstop
        # was the subprocess timeout; the CLI stops the session at the cap and reports it in the
        # envelope, which the unwrap below turns into a failed stage.
        if request.max_turns is not None:
            argv.extend(["--max-turns", str(request.max_turns)])
        # A turn cap bounds iterations; the cost is in the conversation resent every turn, which
        # grows with the turn count. The dollar cap bounds that directly. The CLI stops the
        # session and returns an error envelope, which the unwrap below refuses (D-025).
        if request.max_budget_usd is not None:
            argv.extend(["--max-budget-usd", f"{request.max_budget_usd:g}"])
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

        # A dropped stream is not a verdict. The tenth canary defect (D-031) was a `test_author`
        # worker that returned `API Error: stream closed before completion` after 12 seconds of
        # API time, refused as a failed stage, and cost the whole 50-minute build. Only the
        # explicit TRANSIENT patterns below are retried, each attempt a fresh CLI process with
        # the same prompt; a cap, a budget stop, a missing model or any other error stays
        # terminal and is refused exactly as before. The dollar budget is a per-process CLI flag,
        # so the effective ceiling for a stage is max_budget_usd * (1 + transient_retries).
        # Before a retry the kernel calls `before_retry` (the worker runtime restores the
        # worktree for mutation roles); this provider never touches Git itself.
        transient_errors: list[str] = []
        spent = _Spent()
        attempts_allowed = 1 + self.config.transient_retries
        for attempt in range(1, attempts_allowed + 1):
            if attempt > 1:
                if before_retry is not None:
                    before_retry(attempt)
                _sleep(TRANSIENT_BACKOFF_SECONDS[min(attempt - 2, len(TRANSIENT_BACKOFF_SECONDS) - 1)])
            try:
                stdout = self._launch(argv, request)
                envelope = unwrap_result_envelope(stdout, role=request.role)
            except TransientProviderError as exc:
                spent.add(exc.envelope)
                transient_errors.append(exc.detail)
                if attempt < attempts_allowed:
                    continue
                raise RuntimeError(
                    f"agent worker role={request.role!r} failed on a transient provider error "
                    f"{attempt} time(s) (transient_retries={self.config.transient_retries}): "
                    f"{exc.detail}"
                ) from exc
            spent.add(envelope)
            break
        output = envelope.content

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
            session_id=envelope.session_id,
            input_tokens=spent.input_tokens,
            output_tokens=spent.output_tokens,
            cost_usd=spent.cost_usd,
            num_turns=spent.num_turns,
            duration_ms=spent.duration_ms,
            cache_creation_input_tokens=spent.cache_creation_input_tokens,
            cache_read_input_tokens=spent.cache_read_input_tokens,
            attempts=attempt,
            transient_errors=tuple(transient_errors),
        )

    def _launch(self, argv: list[str], request: AgentRequest) -> str:
        """One CLI process. Returns its stdout; a non-zero exit or a timeout is terminal."""
        env = self._worker_env(request.environment)
        started = time.monotonic()
        try:
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
        except subprocess.TimeoutExpired as exc:
            # A timeout used to escape as a bare TimeoutExpired: no envelope, no telemetry, and
            # the stage that most needed measuring recorded nothing. Name the role, the timeout
            # and what the worker had printed so far; the turn cap is meant to fire before this.
            elapsed = round(time.monotonic() - started, 1)
            partial = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
            raise RuntimeError(
                f"agent worker timed out role={request.role!r} after {elapsed}s "
                f"(timeout_seconds={self.config.timeout_seconds}, max_turns={request.max_turns}); "
                f"partial output: {(partial or '').strip()[-1500:]}"
            ) from exc
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode:
            # The CLI exits non-zero when the session ended in error, and it still prints its
            # result envelope on stdout. The eighteenth canary defect (D-040) was this branch
            # raising the generic failure before that envelope was classified, so a dropped
            # stream that the retry loop is built for never reached it. Classify first; only
            # an envelope the classifier calls transient is handed to the retry loop, anything
            # else (a terminal envelope, or stdout that is not an envelope) is refused as before.
            transient = _transient_from_stdout(stdout, role=request.role)
            if transient is not None:
                raise transient
            detail = (stdout + "\n" + stderr)[-4000:]
            raise RuntimeError(
                f"agent worker failed role={request.role!r} rc={proc.returncode}: {detail}"
            )
        return stdout


# Retried only when the CLI's error text matches one of these. The list is deliberately short
# and literal: anything not on it is a verdict about the worker, not the network, and is refused.
TRANSIENT_ERROR_PATTERNS: tuple[str, ...] = (
    "stream closed before completion",
    "overloaded",
    "rate limit",
    "429",
    "502",
    "503",
    "504",
    "ECONNRESET",
    "ETIMEDOUT",
    "socket hang up",
)
# Backoff before attempt 2, then attempt 3 (and any further, capped at the last value).
TRANSIENT_BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def is_transient_error(detail: str, subtype: str) -> bool:
    """A stream drop or a provider-side capacity error, never a cap, budget or model error."""
    if subtype.startswith("error"):
        return False
    lowered = detail.lower()
    return any(pattern.lower() in lowered for pattern in TRANSIENT_ERROR_PATTERNS)


def _transient_from_stdout(stdout: str, *, role: str) -> "TransientProviderError | None":
    """The transient error a non-zero-exit CLI process printed, if that is what it printed."""
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, Mapping) or "is_error" not in raw or "result" not in raw:
        return None
    subtype = str(raw.get("subtype") or "")
    detail = str(raw.get("result") or "")[-1500:]
    if raw.get("is_error") is not True and not subtype.startswith("error"):
        return None
    if not is_transient_error(detail, subtype):
        return None
    return TransientProviderError(
        f"agent worker role={role!r} ended in error "
        f"(subtype={subtype or 'unknown'} num_turns={raw.get('num_turns')}): {detail}",
        ResultEnvelope(raw),
    )


class TransientProviderError(RuntimeError):
    """An error envelope the provider may retry. Carries the envelope so its cost is counted."""

    def __init__(self, detail: str, envelope: "ResultEnvelope") -> None:
        super().__init__(detail)
        self.detail = detail
        self.envelope = envelope


class _Spent:
    """Telemetry summed across every attempt of one stage, so a retried stage reports what
    it actually cost rather than only its final successful process."""

    def __init__(self) -> None:
        self.num_turns = 0
        self.duration_ms = 0
        self.cost_usd = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0

    def add(self, envelope: "ResultEnvelope") -> None:
        self.num_turns += envelope.num_turns or 0
        self.duration_ms += envelope.duration_ms or 0
        self.cost_usd += envelope.cost_usd or 0.0
        self.input_tokens += envelope.input_tokens or 0
        self.output_tokens += envelope.output_tokens or 0
        self.cache_creation_input_tokens += envelope.cache_creation_input_tokens or 0
        self.cache_read_input_tokens += envelope.cache_read_input_tokens or 0


class ResultEnvelope:
    """The fields the kernel keeps from the CLI's `--output-format json` result."""

    __slots__ = (
        "content", "session_id", "num_turns", "duration_ms", "cost_usd",
        "input_tokens", "output_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "subtype",
    )

    def __init__(self, raw: Mapping[str, Any]) -> None:
        usage = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
        self.content = str(raw.get("result") or "").strip()
        self.session_id = _optional_str(raw.get("session_id"))
        self.num_turns = _optional_int(raw.get("num_turns"))
        self.duration_ms = _optional_int(raw.get("duration_ms"))
        self.cost_usd = _optional_float(raw.get("total_cost_usd"))
        self.input_tokens = _optional_int(usage.get("input_tokens"))
        self.output_tokens = _optional_int(usage.get("output_tokens"))
        # Whether each turn resends the whole conversation uncached, or the prefix is served
        # from cache, decides what a turn cap is worth in money. These two fields answer it;
        # dropping them was why the first telemetry could not (D-025).
        self.cache_creation_input_tokens = _optional_int(usage.get("cache_creation_input_tokens")) or 0
        self.cache_read_input_tokens = _optional_int(usage.get("cache_read_input_tokens")) or 0
        self.subtype = _optional_str(raw.get("subtype"))

    def telemetry(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "num_turns": self.num_turns,
            "duration_ms": self.duration_ms,
            "total_cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "subtype": self.subtype,
        }


def unwrap_result_envelope(stdout: str, *, role: str) -> ResultEnvelope:
    """Refuse anything that is not a non-error CLI result envelope.

    A worker that hit its turn cap, ran out of budget or died on an API error still exits 0 with
    an envelope whose `is_error` is true or whose `subtype` starts with `error`; that is a failed
    stage, not a result to parse. Output that is not an envelope at all means the CLI was not
    launched the way the kernel launches it, and is refused for the same reason.
    """
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"agent worker role={role!r} did not return a JSON result envelope: {stdout[-600:]}"
        ) from exc
    if not isinstance(raw, Mapping) or "is_error" not in raw or "result" not in raw:
        raise RuntimeError(f"agent worker role={role!r} did not return a JSON result envelope")
    subtype = str(raw.get("subtype") or "")
    if raw.get("is_error") is True or subtype.startswith("error"):
        detail = str(raw.get("result") or "")[-1500:]
        message = (
            f"agent worker role={role!r} ended in error "
            f"(subtype={subtype or 'unknown'} num_turns={raw.get('num_turns')}): {detail}"
        )
        if is_transient_error(detail, subtype):
            raise TransientProviderError(message, ResultEnvelope(raw))
        raise RuntimeError(message)
    return ResultEnvelope(raw)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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
