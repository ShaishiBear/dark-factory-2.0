"""Concrete model-CLI workers for the repo-owned factory.

Workers are deliberately untrusted: successful model execution never equals engineering authority.
The deterministic kernel, harness, holdouts and merge verifier remain the judges.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
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

    def wall_seconds(self, request: AgentRequest) -> int:
        """The wall clock for one process of this request: the role's own wall, never above
        the configured maximum (D-054)."""
        if request.timeout_seconds is None:
            return self.config.timeout_seconds
        return min(request.timeout_seconds, self.config.timeout_seconds)

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
            # The CLI prints one JSON object per line as the session runs (`system`/`init`,
            # then `assistant` and `user` messages, then the final `result`), and the `result`
            # event carries exactly the fields the single `--output-format json` envelope did:
            # `is_error`, `subtype`, `result`, `num_turns`, `duration_ms`, `total_cost_usd`,
            # `usage`. The kernel reads the lines as they arrive, so a stage's progress is
            # visible while it runs, a process that has gone silent can be told from one that
            # is working, and a stage killed at its wall still reports the turns and cost it
            # had shown; the `json` format printed nothing until the end, which is why the
            # timed-out `test_author` of run 33987381035 left no telemetry at all (D-054).
            # The final `result` event is unwrapped below exactly as the envelope was (D-020).
            # In print mode the CLI refuses `stream-json` without `--verbose`.
            "--output-format", "stream-json",
            "--verbose",
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
        #
        # A hang (no stream event for `idle_timeout_seconds`) is retried the same way, once:
        # the process is killed, what it showed is counted, and a second hang is terminal
        # whatever the transient budget still allows (D-054).
        transient_errors: list[str] = []
        spent = _Spent()
        attempts_allowed = 1 + self.config.transient_retries
        hangs = 0
        for attempt in range(1, attempts_allowed + 1):
            if attempt > 1:
                if before_retry is not None:
                    before_retry(attempt)
                _sleep(TRANSIENT_BACKOFF_SECONDS[min(attempt - 2, len(TRANSIENT_BACKOFF_SECONDS) - 1)])
            try:
                launched = self._launch(argv, request)
                envelope = unwrap_result_envelope(
                    launched.envelope_text, role=request.role, events_seen=launched.events_seen
                )
            except WorkerHungError as exc:
                spent.add(exc.envelope)
                transient_errors.append(exc.detail)
                hangs += 1
                if hangs <= HANG_RETRIES and attempt < attempts_allowed:
                    continue
                raise ProviderStageError(
                    f"agent worker role={request.role!r} hung {hangs} time(s) "
                    f"(idle_timeout_seconds={self.config.idle_timeout_seconds}, "
                    f"hang_retries={HANG_RETRIES}): {exc.detail}",
                    telemetry={**spent.telemetry(), **exc.telemetry},
                    attempts=attempt,
                    transient_errors=tuple(transient_errors),
                ) from exc
            except TransientProviderError as exc:
                spent.add(exc.envelope)
                transient_errors.append(exc.detail)
                if attempt < attempts_allowed:
                    continue
                raise ProviderStageError(
                    f"agent worker role={request.role!r} failed on a transient provider error "
                    f"{attempt} time(s) (transient_retries={self.config.transient_retries}): "
                    f"{exc.detail}",
                    telemetry=spent.telemetry(),
                    attempts=attempt,
                    transient_errors=tuple(transient_errors),
                ) from exc
            except ProviderStageError as exc:
                # A terminal refusal from `_launch` (timeout, non-transient exit) knows nothing
                # of earlier attempts; its own counts are summed with what this stage already
                # spent across them, and its observations ride on top.
                if exc.envelope is not None:
                    spent.add(exc.envelope)
                exc.telemetry = {**spent.telemetry(), **exc.telemetry}
                exc.attempts = attempt
                exc.transient_errors = tuple(transient_errors)
                raise
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
            events_seen=spent.events_seen,
        )

    def _launch(self, argv: list[str], request: AgentRequest) -> CliRun:
        """One CLI process, read as it runs.

        Returns the run when the process exited zero; its `envelope_text` is the final
        `result` event (or the raw stdout when there was none, which the unwrap refuses). A
        wall timeout is terminal and carries what the stream showed; a hang is a
        `WorkerHungError` the retry loop may retry once; a non-zero exit is classified from
        its `result` event as before (D-040).
        """
        env = self._worker_env(request.environment)
        wall = self.wall_seconds(request)
        run = _stream_cli(
            argv,
            cwd=request.cwd,
            env=env,
            wall_seconds=wall,
            idle_seconds=self.config.idle_timeout_seconds,
        )
        # What the events showed, for a process that never printed its `result`. The counts
        # (turns, tokens, events) travel on the envelope so the retry loop sums them across
        # attempts like any other attempt's; the observations below are about this process.
        partial = ResultEnvelope.from_events(run)
        observed = {
            "last_event_age_s": run.last_event_age,
            "wall_seconds_last_attempt": run.elapsed,
            "partial_output": run.tail(),
        }
        if run.hung:
            raise WorkerHungError(
                f"agent worker hung role={request.role!r}: no event for {run.last_event_age}s "
                f"(idle_timeout_seconds={self.config.idle_timeout_seconds}) after {run.elapsed}s, "
                f"events_seen={run.events_seen} turns_seen={partial.num_turns}; "
                f"last events: {run.tail()}",
                partial,
                telemetry={"hang": True, **observed},
            )
        if run.timed_out:
            # A timeout used to escape as a bare TimeoutExpired: no envelope, no telemetry, and
            # the stage that most needed measuring recorded nothing. Name the role, the wall
            # and what the stream showed; the turn cap is meant to fire before this.
            raise ProviderStageError(
                f"agent worker timed out role={request.role!r} after {run.elapsed}s "
                f"(timeout_seconds={wall}, max_turns={request.max_turns}, "
                f"events_seen={run.events_seen}, turns_seen={partial.num_turns}, "
                f"last_event_age_s={run.last_event_age}); last events: {run.tail()}",
                telemetry=observed,
                timed_out=True,
                envelope=partial,
            )
        stdout = run.envelope_text
        stderr = run.stderr.strip()
        if run.returncode:
            # The CLI exits non-zero when the session ended in error, and it still prints its
            # result envelope on stdout. The eighteenth canary defect (D-040) was this branch
            # raising the generic failure before that envelope was classified, so a dropped
            # stream that the retry loop is built for never reached it. Classify first; only
            # an envelope the classifier calls transient is handed to the retry loop, anything
            # else (a terminal envelope, or stdout that is not an envelope) is refused as before.
            transient = _transient_from_stdout(
                stdout, role=request.role, events_seen=run.events_seen
            )
            if transient is not None:
                raise transient
            detail = (stdout + "\n" + stderr)[-4000:]
            envelope, telemetry = _terminal_envelope(stdout, run, partial)
            raise ProviderStageError(
                f"agent worker failed role={request.role!r} rc={run.returncode}: {detail}",
                telemetry=telemetry,
                envelope=envelope,
            )
        return run


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
# A hung process is relaunched this many times; the next hang is terminal (D-054).
HANG_RETRIES = 1
# How much of the stream's tail a refusal quotes: the last event lines, capped.
PARTIAL_OUTPUT_CHARS = 1500
PARTIAL_OUTPUT_LINES = 5


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def is_transient_error(detail: str, subtype: str) -> bool:
    """A stream drop or a provider-side capacity error, never a cap, budget or model error."""
    if subtype.startswith("error"):
        return False
    lowered = detail.lower()
    return any(pattern.lower() in lowered for pattern in TRANSIENT_ERROR_PATTERNS)


@dataclass
class CliRun:
    """One CLI process as the kernel saw it: every stdout line, parsed as stream-json events,
    with how it ended. `returncode` is `None` when the kernel killed it (`timed_out`: the
    wall passed; `hung`: no event for the idle timeout)."""

    returncode: int | None
    stdout: str
    stderr: str = ""
    # The stream-json events in `stdout`, in order. Parsed from `stdout` when not given, so
    # a run built from a completed process's text (as the tests build them) is the same
    # shape as one the reader assembled line by line.
    events: list[dict[str, Any]] = field(default_factory=list)
    elapsed: float = 0.0
    timed_out: bool = False
    hung: bool = False
    last_event_age: float = 0.0

    def __post_init__(self) -> None:
        if not self.events and self.stdout:
            self.events = parse_events(self.stdout)

    @property
    def events_seen(self) -> int:
        return len(self.events)

    @property
    def result_event(self) -> dict[str, Any] | None:
        """The final `result` event, the one that carries the envelope fields."""
        for event in reversed(self.events):
            if event.get("type") == "result":
                return event
        return None

    @property
    def envelope_text(self) -> str:
        """The `result` event as the JSON text `unwrap_result_envelope` reads, or the raw
        stdout when the process printed no `result` (which the unwrap refuses by name)."""
        event = self.result_event
        return json.dumps(event) if event is not None else self.stdout.strip()

    def tail(self, limit: int = PARTIAL_OUTPUT_CHARS) -> str:
        """The last few stream lines, capped: what the process was doing when it ended."""
        lines = [line for line in self.stdout.splitlines() if line.strip()]
        return "\n".join(lines[-PARTIAL_OUTPUT_LINES:])[-limit:]


def parse_event(line: str) -> dict[str, Any] | None:
    """One stream-json line as an event, or `None` for anything that is not a JSON object
    with a string `type`. The CLI prints nothing else on stdout; tolerance is for a line the
    kernel did not expect, which is not evidence of progress."""
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, Mapping) or not isinstance(raw.get("type"), str):
        return None
    return dict(raw)


def parse_events(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        event = parse_event(line)
        if event is not None:
            events.append(event)
    return events


def _stream_cli(
    argv: list[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    wall_seconds: float,
    idle_seconds: float,
) -> CliRun:
    """Run one CLI process and read its stdout line by line as it runs.

    Two clocks. The wall clock kills the process `wall_seconds` after launch. The idle clock
    kills it when no stream event has arrived for `idle_seconds`: a working CLI prints an
    event per model turn and per tool call, so a silence longer than one model call is a
    process that will not finish, not one that is slow. Either kill returns a `CliRun` that
    says which clock fired and everything the process printed before it (D-054). The prompt
    travels on argv, so stdin is closed: the worker never reads it, and an open pipe is one
    more way for a print-mode CLI to wait forever.
    """
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def pump(stream: Any, tag: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                lines.put((tag, line))
        finally:
            lines.put((tag, None))

    for stream, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
        threading.Thread(target=pump, args=(stream, tag), daemon=True).start()

    out: list[str] = []
    err: list[str] = []
    events: list[dict[str, Any]] = []
    open_streams = 2
    last_event = started
    timed_out = hung = False
    while open_streams:
        now = time.monotonic()
        wall_left = wall_seconds - (now - started)
        idle_left = idle_seconds - (now - last_event)
        if wall_left <= 0:
            timed_out = True
            break
        if idle_left <= 0:
            hung = True
            break
        try:
            tag, line = lines.get(timeout=min(wall_left, idle_left))
        except queue.Empty:
            continue
        if line is None:
            open_streams -= 1
            continue
        if tag == "err":
            err.append(line)
            continue
        out.append(line)
        event = parse_event(line)
        if event is not None:
            events.append(event)
            last_event = time.monotonic()
    returncode: int | None = None
    if timed_out or hung:
        _kill(proc)
    else:
        # Both pipes closed. A process that closed them and then lingers is held to the
        # same wall as one that kept printing.
        try:
            returncode = proc.wait(timeout=max(0.0, wall_seconds - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill(proc)
    ended = time.monotonic()
    return CliRun(
        returncode=returncode,
        stdout="".join(out),
        stderr="".join(err),
        events=events,
        elapsed=round(ended - started, 1),
        timed_out=timed_out,
        hung=hung,
        last_event_age=round(ended - last_event, 1),
    )


def _kill(proc: subprocess.Popen[str]) -> None:
    try:
        proc.kill()
    except OSError:
        return
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


def _terminal_envelope(
    stdout: str, run: CliRun, partial: ResultEnvelope
) -> tuple[ResultEnvelope, dict[str, Any]]:
    """The counts of a process that exited non-zero: its result envelope's, with the
    envelope's subtype, if it printed one; else what its events showed."""
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError:
        return partial, {}
    if not isinstance(raw, Mapping) or "is_error" not in raw:
        return partial, {}
    envelope = ResultEnvelope(raw, events_seen=run.events_seen)
    return envelope, {"subtype": str(raw.get("subtype") or "")}


def _transient_from_stdout(
    stdout: str, *, role: str, events_seen: int | None = None
) -> TransientProviderError | None:
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
        ResultEnvelope(raw, events_seen=events_seen),
    )


class TransientProviderError(RuntimeError):
    """An error envelope the provider may retry. Carries the envelope so its cost is counted."""

    def __init__(self, detail: str, envelope: ResultEnvelope) -> None:
        super().__init__(detail)
        self.detail = detail
        self.envelope = envelope


class WorkerHungError(TransientProviderError):
    """A process the kernel killed because its stream went silent for the idle timeout.

    Retried like a dropped stream (the worktree is restored first, the attempt is counted),
    but only `HANG_RETRIES` times: a process that hangs twice on the same prompt is not
    suffering the network. `envelope` is what the events showed before the silence, so the
    turns and tokens of a hung attempt are summed into the stage like any other attempt;
    `telemetry` is the hang itself (`hang`, `events_seen`, `last_event_age_s`,
    `partial_output`) for the record of a stage that ends on it (D-054).
    """

    def __init__(
        self, detail: str, envelope: ResultEnvelope, *, telemetry: Mapping[str, Any]
    ) -> None:
        super().__init__(detail, envelope)
        self.telemetry = dict(telemetry)


class ProviderStageError(RuntimeError):
    """A stage the provider refused, carrying what the stage cost before it failed.

    Two `test_author` stream drops (runs 33918953996 and 33933101233) left only the exception
    text as evidence, because the kernel recorded telemetry only for a stage that returned
    (D-040). Every terminal refusal now carries `telemetry` (the summed envelope counts, if any
    envelope was ever parsed), `attempts`, `transient_errors` and `timed_out`, so the kernel
    can write the same `agent-<role>.json` record it writes for a success. The message and the
    classification are exactly what they were; only the attributes are new.
    """

    def __init__(
        self,
        message: str,
        *,
        telemetry: Mapping[str, Any] | None = None,
        attempts: int = 1,
        transient_errors: tuple[str, ...] = (),
        timed_out: bool = False,
        envelope: ResultEnvelope | None = None,
    ) -> None:
        super().__init__(message)
        self.telemetry = dict(telemetry or {})
        self.attempts = attempts
        self.transient_errors = tuple(transient_errors)
        self.timed_out = timed_out
        # The counts of the attempt that ended in this refusal (its result envelope, or what
        # its events showed when it was killed); the retry loop sums them into the stage's
        # telemetry with every earlier attempt's (D-054).
        self.envelope = envelope


class _Spent:
    """Telemetry summed across every attempt of one stage, so a retried stage reports what
    it actually cost rather than only its final successful process."""

    def __init__(self) -> None:
        self.num_turns = 0
        self.duration_ms = 0
        # Dollars are known only from a `result` event. A stage whose only attempt was killed
        # before one reports no cost rather than a false zero; an attempt that did return one
        # makes the sum known from then on.
        self.cost_usd: float | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0
        self.events_seen = 0

    def add(self, envelope: ResultEnvelope) -> None:
        self.num_turns += envelope.num_turns or 0
        self.duration_ms += envelope.duration_ms or 0
        if envelope.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + envelope.cost_usd
        self.input_tokens += envelope.input_tokens or 0
        self.output_tokens += envelope.output_tokens or 0
        self.cache_creation_input_tokens += envelope.cache_creation_input_tokens or 0
        self.cache_read_input_tokens += envelope.cache_read_input_tokens or 0
        self.events_seen += envelope.events_seen or 0

    def telemetry(self) -> dict[str, Any]:
        return {
            "num_turns": self.num_turns,
            "duration_ms": self.duration_ms,
            "total_cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "events_seen": self.events_seen,
        }


class ResultEnvelope:
    """The fields the kernel keeps from the CLI's final `result` event (the same fields the
    `--output-format json` envelope carried), plus how many stream events preceded it."""

    __slots__ = (
        "content", "session_id", "num_turns", "duration_ms", "cost_usd",
        "input_tokens", "output_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "subtype", "events_seen",
    )

    def __init__(self, raw: Mapping[str, Any], *, events_seen: int | None = None) -> None:
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
        self.events_seen = events_seen

    @classmethod
    def from_events(cls, run: CliRun) -> ResultEnvelope:
        """What a process that never printed its `result` had shown: one turn per distinct
        `assistant` message (the CLI may print a message once per content block), its usage
        summed per message, the session id from any event, and the wall it ran. Its dollar
        cost is not in the stream before the `result`, so it is left unknown rather than
        guessed (D-054)."""
        turns: dict[str, Mapping[str, Any]] = {}
        session_id: str | None = None
        for index, event in enumerate(run.events):
            if session_id is None:
                session_id = _optional_str(event.get("session_id"))
            if event.get("type") != "assistant":
                continue
            message = event.get("message") if isinstance(event.get("message"), Mapping) else {}
            key = _optional_str(message.get("id")) or f"event-{index}"
            usage = message.get("usage") if isinstance(message.get("usage"), Mapping) else {}
            turns[key] = usage
        raw: dict[str, Any] = {
            "result": "",
            "session_id": session_id,
            "num_turns": len(turns),
            "duration_ms": int(run.elapsed * 1000),
            "usage": {
                name: sum(_optional_int(usage.get(name)) or 0 for usage in turns.values())
                for name in (
                    "input_tokens", "output_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens",
                )
            },
        }
        return cls(raw, events_seen=run.events_seen)

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
            "events_seen": self.events_seen,
        }


def unwrap_result_envelope(
    stdout: str, *, role: str, events_seen: int | None = None
) -> ResultEnvelope:
    """Refuse anything that is not a non-error CLI result envelope.

    A worker that hit its turn cap, ran out of budget or died on an API error still exits 0 with
    an envelope whose `is_error` is true or whose `subtype` starts with `error`; that is a failed
    stage, not a result to parse. Output that is not an envelope at all means the CLI was not
    launched the way the kernel launches it, and is refused for the same reason. `events_seen`
    is stamped on the envelope so a stage's record says how much stream preceded it.
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
            raise TransientProviderError(message, ResultEnvelope(raw, events_seen=events_seen))
        raise RuntimeError(message)
    return ResultEnvelope(raw, events_seen=events_seen)


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
