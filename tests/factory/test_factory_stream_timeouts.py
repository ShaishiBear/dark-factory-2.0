"""A worker's timeout is measured on its event stream, and leaves telemetry (D-054).

Build run 33987381035 (issue #103) died in `test_author` after 1200 s with `num_turns=0`,
`duration_ms=0`, `total_cost_usd=0` and an empty partial output: with `--output-format json`
the CLI prints nothing until the end, so a hung process could not be told from a slow, working
one, and the stage that cost the most left no evidence at all. The same run measured
`investigate` at 40.4 s per turn against a stated ceiling of 35, which put the single 1200 s
wall exactly on a 30-turn worker's turn budget.

The provider now reads the CLI's `stream-json` events as they arrive. A process that prints
no event for `provider.idle_timeout_seconds` is hung: killed, recorded (`hang`, `events_seen`,
`last_event_age_s`, the turns and tokens its events showed, the last event lines) and retried
once through the existing transient path; a second hang is terminal. Each role has its own
wall, `ceil(max_turns * OBSERVED_SECONDS_PER_TURN_CEILING * 1.5)`, carried on the request;
`provider.timeout_seconds` is the maximum every wall must fit under. A stage killed at its
wall records the turns, cost and events it had shown, and its stage line says so.

The end-to-end cases here run a real subprocess: a fake CLI (a Python script the test writes)
that emits stream-json lines with controllable pacing, launched through the provider's own
argv and read by the provider's own reader. The event shapes are the ones the installed CLI
printed to a probe (`system/init`, `assistant` with `message.id` and `message.usage`, `user`
tool results, and the final `result` carrying the same fields the json envelope did).
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import providers as providers_module  # noqa: E402
from factory_kernel.agents import AgentRequest  # noqa: E402
from factory_kernel.config import (  # noqa: E402
    IDLE_TIMEOUT_SECONDS_DEFAULT,
    ProviderConfig,
    load_config,
)
from factory_kernel.providers import (  # noqa: E402
    HANG_RETRIES,
    PARTIAL_OUTPUT_CHARS,
    ClaudeCliProvider,
    CliRun,
    ProviderStageError,
    ResultEnvelope,
    WorkerHungError,
    parse_events,
    unwrap_result_envelope,
)
from factory_kernel.runtime import STAGE_TIMINGS, KernelRuntime, RunPaths, stage_line  # noqa: E402
from factory_kernel.worker_policy import (  # noqa: E402
    OBSERVED_SECONDS_PER_TURN_CEILING,
    ROLE_MAX_TURNS,
    STAGE_WALL_HEADROOM,
    assert_caps_fit_timeout,
    effort,
    max_turns,
    stage_budget_seconds,
    stage_timeout_seconds,
)

KERNEL_JSON = ROOT / ".factory" / "kernel.json"

# The four per-turn observations of build run 33987381035, seconds per turn, that the ceiling
# is stated from (investigate, contract, context, architecture).
OBSERVED_SECONDS_PER_TURN = (888.607 / 22, 280.317 / 12, 696.978 / 30, 262.363 / 12)


# --- stream-json event shapes, as the installed CLI printed them to a probe -----------------


def init_event(session_id: str = "s-1") -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "cwd": "/w",
        "session_id": session_id,
        "tools": ["Read", "Glob", "Grep", "Write", "Edit"],
        "mcp_servers": [],
        "model": "m",
        "permissionMode": "dontAsk",
        "claude_code_version": "2.1.245",
    }


def assistant_event(
    message_id: str,
    text: str,
    *,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read: int = 0,
    session_id: str = "s-1",
) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": "m",
            "type": "message",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cache_read,
            },
            "content": [{"type": "text", "text": text}],
        },
        "parent_tool_use_id": None,
        "session_id": session_id,
    }


def tool_result_event(session_id: str = "s-1") -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"},
            ],
        },
        "parent_tool_use_id": None,
        "session_id": session_id,
    }


def result_event(**overrides) -> dict:
    raw = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "num_turns": 3,
        "duration_ms": 1000,
        "duration_api_ms": 900,
        "total_cost_usd": 0.10,
        "session_id": "s-1",
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 50,
            "output_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 5,
        },
    }
    raw.update(overrides)
    return raw


def lines(*events: dict) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


# --- a fake CLI: a Python script that emits stream-json lines with controllable pacing ------

FAKE_CLI = """\
import json, os, sys, time
scenario = json.load(open(os.environ["CLAUDE_FAKE_SCENARIO"], encoding="utf-8"))
if scenario.get("argv_log"):
    with open(scenario["argv_log"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\\n")
steps = scenario.get("steps")
if steps is None:
    # One entry per launch: the counter file says which launch this is.
    n = 0
    if os.path.exists(scenario["counter"]):
        n = int(open(scenario["counter"], encoding="utf-8").read() or 0)
    open(scenario["counter"], "w", encoding="utf-8").write(str(n + 1))
    steps = scenario["attempts"][min(n, len(scenario["attempts"]) - 1)]
for step in steps:
    if "sleep" in step:
        time.sleep(step["sleep"])
    if "emit" in step:
        sys.stdout.write(json.dumps(step["emit"]) + "\\n")
        sys.stdout.flush()
    if "raw" in step:
        sys.stdout.write(step["raw"])
        sys.stdout.flush()
    if "stderr" in step:
        sys.stderr.write(step["stderr"] + "\\n")
        sys.stderr.flush()
    if "stdin" in step:
        seen = {"type": "system", "subtype": "stdin", "text": sys.stdin.read()}
        sys.stdout.write(json.dumps(seen) + "\\n")
        sys.stdout.flush()
    if "exit" in step:
        sys.exit(step["exit"])
sys.exit(0)
"""


def fake_cli(tmp: Path) -> str:
    """Write the fake CLI and the wrapper the provider launches as `binary`."""
    script = tmp / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf-8", newline="\n")
    if os.name == "nt":
        binary = tmp / "fake-claude.cmd"
        binary.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8", newline="")
    else:
        binary = tmp / "fake-claude"
        binary.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8", newline="\n"
        )
        binary.chmod(0o755)
    return str(binary)


def provider_for(
    binary: str, *, retries: int = 2, timeout: int = 60, idle: float = 1
) -> ClaudeCliProvider:
    # The loader requires an integer idle timeout; the dataclass does not check, and a
    # fraction of a second keeps the end-to-end cases short.
    return ClaudeCliProvider(
        ProviderConfig(
            provider_id="claude-cli",
            binary=binary,
            model="m",
            timeout_seconds=timeout,
            transient_retries=retries,
            idle_timeout_seconds=idle,
        )
    )


def request(role: str = "test_author", *, timeout_seconds: int | None = None) -> AgentRequest:
    return AgentRequest(
        role=role,
        prompt="p",
        cwd=tempfile.gettempdir(),
        max_turns=30,
        max_budget_usd=12.0,
        timeout_seconds=timeout_seconds,
    )


class _FakeCliCase(unittest.TestCase):
    """A temp dir holding the fake CLI, a scenario file the fake reads, and an argv log."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="dark-factory-stream-")
        self.tmp = Path(self.tmp_dir.name)
        self.binary = fake_cli(self.tmp)
        self.scenario_path = self.tmp / "scenario.json"
        self.argv_log = self.tmp / "argv.log"
        # The scenario path reaches the fake through the one env prefix the provider forwards.
        env = {"CLAUDE_FAKE_SCENARIO": str(self.scenario_path)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The provider's env filter keeps only what the CLI needs; a Python child on Windows
        # also needs SYSTEMROOT, which the real CLI never did on the Linux runner. Widen the
        # filter for the fake only, on top of the real filter's result.
        original = ClaudeCliProvider._worker_env

        def widened(extra):
            env = original(extra)
            for key in ("SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT"):
                if key in os.environ:
                    env[key] = os.environ[key]
            return env

        env_patch = mock.patch.object(ClaudeCliProvider, "_worker_env", staticmethod(widened))
        env_patch.start()
        self.addCleanup(env_patch.stop)
        sleep_patch = mock.patch.object(providers_module, "_sleep", lambda s: None)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def tearDown(self) -> None:
        # A killed wrapper on Windows leaves its Python child to finish its scenario; give it
        # a moment before the directory it read from goes away.
        for _ in range(50):
            try:
                self.tmp_dir.cleanup()
                return
            except (PermissionError, OSError):
                time.sleep(0.2)
        self.tmp_dir.cleanup()

    def scenario(
        self, steps: list[dict] | None = None, *, attempts: list[list[dict]] | None = None
    ) -> None:
        body: dict = {"argv_log": str(self.argv_log)}
        if steps is not None:
            body["steps"] = steps
        else:
            body["attempts"] = attempts
            body["counter"] = str(self.tmp / "counter")
        self.scenario_path.write_text(json.dumps(body), encoding="utf-8")

    def launches(self) -> list[list[str]]:
        if not self.argv_log.exists():
            return []
        return [json.loads(line) for line in self.argv_log.read_text(encoding="utf-8").splitlines()]


def healthy_steps(pace: float = 0.05) -> list[dict]:
    return [
        {"emit": init_event()},
        {"sleep": pace, "emit": assistant_event("msg_1", "looking")},
        {"sleep": pace, "emit": tool_result_event()},
        {"sleep": pace, "emit": assistant_event("msg_2", "done")},
        {"emit": result_event()},
    ]


def hanging_steps(silence: float = 3.0) -> list[dict]:
    return [
        {"emit": init_event()},
        {"sleep": 0.05, "emit": assistant_event("msg_1", "thinking", input_tokens=300)},
        {"sleep": silence},
        {"emit": result_event()},
    ]


def slow_steps(count: int = 25, pace: float = 0.2) -> list[dict]:
    steps: list[dict] = [{"emit": init_event()}]
    for n in range(count):
        steps.append({"sleep": pace, "emit": assistant_event(f"msg_{n}", f"turn {n}")})
    steps.append({"emit": result_event(num_turns=count)})
    return steps


class NormalCompletionTests(_FakeCliCase):
    def test_a_streamed_session_produces_the_same_result_the_json_envelope_did(self):
        self.scenario(healthy_steps())
        result = provider_for(self.binary).run(request())
        self.assertEqual(result.content, "done")
        self.assertEqual(result.num_turns, 3)
        self.assertEqual(result.duration_ms, 1000)
        self.assertAlmostEqual(result.cost_usd, 0.10)
        self.assertEqual(result.input_tokens, 50)
        self.assertEqual(result.output_tokens, 7)
        self.assertEqual(result.cache_read_input_tokens, 5)
        self.assertEqual(result.session_id, "s-1")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.transient_errors, ())
        self.assertEqual(result.events_seen, 5)
        (argv,) = self.launches()
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "30")

    def test_a_terminal_error_result_is_refused_with_its_events_counted(self):
        self.scenario(
            [
                {"emit": init_event()},
                {"emit": assistant_event("msg_1", "x")},
                {
                    "emit": result_event(
                        is_error=True,
                        subtype="error_max_turns",
                        result="Reached max turns",
                        num_turns=30,
                    )
                },
                {"exit": 1},
            ]
        )
        with self.assertRaises(ProviderStageError) as ctx:
            provider_for(self.binary).run(request())
        self.assertIn("error_max_turns", str(ctx.exception))
        self.assertEqual(ctx.exception.telemetry["num_turns"], 30)
        self.assertEqual(ctx.exception.telemetry["events_seen"], 3)
        self.assertEqual(len(self.launches()), 1, "a cap is terminal, never retried")

    def test_a_transient_error_result_is_retried_as_before(self):
        self.scenario(
            attempts=[
                [
                    {"emit": init_event()},
                    {
                        "emit": result_event(
                            is_error=True,
                            result="API Error: stream closed before completion",
                            num_turns=2,
                        )
                    },
                    {"exit": 1},
                ],
                healthy_steps(),
            ]
        )
        result = provider_for(self.binary).run(request())
        self.assertEqual(result.content, "done")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.num_turns, 2 + 3)
        self.assertEqual(result.events_seen, 2 + 5)
        self.assertEqual(len(self.launches()), 2)


class ReaderTests(_FakeCliCase):
    """`_stream_cli` against the fake CLI: the two clocks, on a real process."""

    def read(self, steps: list[dict], *, wall: float, idle: float) -> CliRun:
        self.scenario(steps)
        return providers_module._stream_cli(
            [self.binary, "--bare", "-p", "p"],
            cwd=tempfile.gettempdir(),
            env=ClaudeCliProvider._worker_env({}),
            wall_seconds=wall,
            idle_seconds=idle,
        )

    def test_silence_longer_than_the_idle_timeout_is_a_hang(self):
        run = self.read(hanging_steps(silence=3.0), wall=10, idle=0.3)
        self.assertTrue(run.hung)
        self.assertFalse(run.timed_out)
        self.assertIsNone(run.returncode, "killed by the kernel, not exited")
        self.assertEqual(run.events_seen, 2)
        self.assertGreaterEqual(run.last_event_age, 0.3)
        self.assertLess(run.elapsed, 2.0, "killed at the idle timeout, not after the 3 s silence")
        self.assertIsNone(run.result_event)

    def test_events_within_the_idle_timeout_are_progress_not_a_hang(self):
        """The idle clock measures silence between events, not total time."""
        run = self.read(slow_steps(count=5, pace=0.12), wall=10, idle=0.4)
        self.assertEqual(run.returncode, 0)
        self.assertFalse(run.hung)
        self.assertFalse(run.timed_out)
        self.assertEqual(run.events_seen, 7)
        self.assertEqual(run.result_event["num_turns"], 5)

    def test_the_wall_kills_a_process_that_is_still_printing(self):
        run = self.read(slow_steps(count=40, pace=0.05), wall=0.6, idle=5)
        self.assertTrue(run.timed_out)
        self.assertFalse(run.hung)
        self.assertIsNone(run.returncode)
        self.assertGreaterEqual(run.events_seen, 5)
        self.assertGreaterEqual(run.elapsed, 0.6)
        self.assertLess(run.elapsed, 2.0)
        self.assertIsNone(run.result_event)

    def test_stderr_is_collected_and_is_not_progress(self):
        steps = [{"emit": init_event()}, {"stderr": "warning: something"}, {"sleep": 2.0}]
        run = self.read(steps, wall=10, idle=0.25)
        self.assertTrue(run.hung)
        self.assertIn("warning: something", run.stderr)
        self.assertEqual(run.events_seen, 1)

    def test_stdin_is_closed_so_a_worker_that_reads_it_gets_nothing_at_once(self):
        run = self.read([{"stdin": True}, {"emit": result_event()}], wall=10, idle=2)
        self.assertEqual(run.returncode, 0)
        (stdin_event, _result) = run.events
        self.assertEqual((stdin_event["subtype"], stdin_event["text"]), ("stdin", ""))


class HangTests(_FakeCliCase):
    def test_a_hung_process_is_killed_recorded_and_terminal_without_a_retry_budget(self):
        self.scenario(hanging_steps())
        started = time.monotonic()
        with self.assertRaises(ProviderStageError) as ctx:
            provider_for(self.binary, retries=0, idle=0.5).run(request())
        elapsed = time.monotonic() - started
        exc = ctx.exception
        self.assertLess(elapsed, 2.5, "one idle timeout of 0.5 s, not the 3 s silence")
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(exc.attempts, 1)
        self.assertFalse(exc.timed_out)
        self.assertIs(exc.telemetry["hang"], True)
        self.assertGreaterEqual(exc.telemetry["last_event_age_s"], 0.5)
        self.assertEqual(exc.telemetry["events_seen"], 2)
        self.assertEqual(exc.telemetry["num_turns"], 1, "one assistant message seen")
        self.assertEqual(exc.telemetry["input_tokens"], 300)
        self.assertIsNone(exc.telemetry["total_cost_usd"], "no result event: cost unknown")
        self.assertIn("msg_1", exc.telemetry["partial_output"])
        self.assertEqual(len(exc.transient_errors), 1)
        self.assertIn("hung", exc.transient_errors[0])
        self.assertIn("hung 1 time(s)", str(exc))
        self.assertIn("idle_timeout_seconds=0.5", str(exc))

    def test_a_hang_followed_by_a_healthy_process_is_one_retry_with_the_restore_hook(self):
        self.scenario(attempts=[hanging_steps(), healthy_steps()])
        restores: list[int] = []
        result = provider_for(self.binary, retries=2, idle=0.5).run(
            request(), before_retry=restores.append
        )
        self.assertEqual(result.content, "done")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(restores, [2], "the worktree restore hook ran before the relaunch")
        self.assertEqual(len(result.transient_errors), 1)
        self.assertIn("hung", result.transient_errors[0])
        self.assertEqual(result.num_turns, 1 + 3, "the hung attempt's turn is counted")
        self.assertEqual(result.events_seen, 2 + 5)
        self.assertEqual(len(self.launches()), 1 + HANG_RETRIES)


class WallTimeoutTests(_FakeCliCase):
    def test_a_stage_killed_at_its_own_wall_records_partial_turns_and_events(self):
        """The request said 1 s; the configured 60 s maximum and the 5 s idle clock are not
        what stops it, and what it showed by then is in the refusal."""
        self.scenario(slow_steps(count=30, pace=0.1))
        started = time.monotonic()
        with self.assertRaises(ProviderStageError) as ctx:
            provider_for(self.binary, retries=2, timeout=60, idle=5).run(request(timeout_seconds=1))
        elapsed = time.monotonic() - started
        exc = ctx.exception
        self.assertLess(elapsed, 3.0, "killed at the 1 s wall, not the 5 s idle or the run's end")
        self.assertEqual(len(self.launches()), 1, "a wall timeout is terminal, never retried")
        self.assertTrue(exc.timed_out)
        self.assertNotIn("hang", exc.telemetry)
        self.assertGreaterEqual(exc.telemetry["events_seen"], 4)
        self.assertGreaterEqual(exc.telemetry["num_turns"], 3, "one per assistant message seen")
        self.assertGreaterEqual(exc.telemetry["input_tokens"], 300)
        self.assertGreaterEqual(exc.telemetry["wall_seconds_last_attempt"], 1.0)
        self.assertLessEqual(exc.telemetry["last_event_age_s"], 1.0)
        self.assertIn("msg_", exc.telemetry["partial_output"])
        self.assertLessEqual(len(exc.telemetry["partial_output"]), PARTIAL_OUTPUT_CHARS)
        self.assertIn("timed out role='test_author'", str(exc))
        self.assertIn("timeout_seconds=1", str(exc))
        self.assertIn("events_seen=", str(exc))

    def test_a_request_wall_above_the_maximum_is_clamped_to_it(self):
        prov = provider_for(self.binary, timeout=60)
        self.assertEqual(prov.wall_seconds(request(timeout_seconds=5000)), 60)
        self.assertEqual(prov.wall_seconds(request(timeout_seconds=7)), 7)
        self.assertEqual(prov.wall_seconds(request()), 60, "no request wall: the maximum")


class StreamReaderUnitTests(unittest.TestCase):
    """The reader and the envelope built from events, without a process."""

    def test_events_are_typed_json_objects_one_per_line_and_nothing_else(self):
        text = (
            lines(init_event(), assistant_event("m1", "x"))
            + 'not json\n{"no": "type"}\n[1]\n\n'
            + lines(result_event())
        )
        events = parse_events(text)
        self.assertEqual([e["type"] for e in events], ["system", "assistant", "result"])

    def test_the_result_event_is_the_envelope_and_unwraps_as_before(self):
        run = CliRun(
            returncode=0,
            stdout=lines(
                init_event(), assistant_event("m1", "x"), tool_result_event(), result_event()
            ),
        )
        self.assertEqual(run.events_seen, 4)
        self.assertEqual(run.result_event["type"], "result")
        envelope = unwrap_result_envelope(
            run.envelope_text, role="implement", events_seen=run.events_seen
        )
        self.assertEqual(envelope.content, "done")
        self.assertEqual(envelope.num_turns, 3)
        self.assertEqual(envelope.duration_ms, 1000)
        self.assertAlmostEqual(envelope.cost_usd, 0.10)
        self.assertEqual(envelope.events_seen, 4)
        self.assertEqual(envelope.telemetry()["events_seen"], 4)

    def test_a_single_json_envelope_is_one_result_event(self):
        """The `json` format's one-line envelope is a stream of exactly one `result` event, so
        the parser is not coupled to the flag."""
        run = CliRun(returncode=0, stdout=json.dumps(result_event()) + "\n")
        self.assertEqual(run.events_seen, 1)
        self.assertEqual(unwrap_result_envelope(run.envelope_text, role="x").content, "done")

    def test_a_stream_without_a_result_is_refused_by_name(self):
        run = CliRun(returncode=0, stdout=lines(init_event(), assistant_event("m1", "x")))
        self.assertIsNone(run.result_event)
        with self.assertRaisesRegex(RuntimeError, "did not return a JSON result envelope"):
            unwrap_result_envelope(run.envelope_text, role="implement")

    def test_the_partial_envelope_counts_one_turn_per_assistant_message(self):
        """The CLI may print one `assistant` event per content block of the same message; the
        turns and usage are per distinct `message.id`, and the cost is unknown, not zero."""
        run = CliRun(
            returncode=None,
            stdout=lines(
                init_event("s-9"),
                assistant_event("m1", "text block", input_tokens=100, output_tokens=10),
                assistant_event("m1", "tool block", input_tokens=100, output_tokens=10),
                tool_result_event(),
                assistant_event("m2", "again", input_tokens=250, output_tokens=5, cache_read=90),
            ),
            elapsed=12.3,
            hung=True,
            last_event_age=7.0,
        )
        partial = ResultEnvelope.from_events(run)
        self.assertEqual(partial.num_turns, 2)
        self.assertEqual(partial.input_tokens, 350)
        self.assertEqual(partial.output_tokens, 15)
        self.assertEqual(partial.cache_read_input_tokens, 90)
        self.assertIsNone(partial.cost_usd)
        self.assertEqual(partial.session_id, "s-9")
        self.assertEqual(partial.duration_ms, 12300)
        self.assertEqual(partial.events_seen, 5)
        self.assertEqual(partial.content, "")

    def test_the_tail_is_the_last_event_lines_capped(self):
        run = CliRun(
            returncode=None, stdout=lines(*[assistant_event(f"m{n}", "x" * 400) for n in range(12)])
        )
        tail = run.tail()
        self.assertLessEqual(len(tail), PARTIAL_OUTPUT_CHARS)
        self.assertIn("m11", tail)
        self.assertNotIn('"m0"', tail)


def _hung(**over) -> CliRun:
    fields = dict(
        returncode=None,
        stdout=lines(init_event(), assistant_event("m1", "x")),
        stderr="",
        elapsed=421.0,
        hung=True,
        last_event_age=420.5,
    )
    fields.update(over)
    return CliRun(**fields)


def _timed_out(turns: int = 4) -> CliRun:
    return CliRun(
        returncode=None,
        stderr="",
        stdout=lines(
            init_event(), *[assistant_event(f"m{n}", "x", input_tokens=10) for n in range(turns)]
        ),
        elapsed=2025.2,
        timed_out=True,
        last_event_age=3.1,
    )


def _ok() -> CliRun:
    return CliRun(returncode=0, stdout=lines(init_event(), result_event()), stderr="")


class _Runs:
    def __init__(self, *runs: CliRun) -> None:
        self.runs = list(runs)
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, **kwargs})
        if not self.runs:
            raise AssertionError("more launches than the test allowed")
        return self.runs.pop(0)


def _provider(retries: int = 2, timeout: int = 2700, idle: int = 420) -> ClaudeCliProvider:
    return ClaudeCliProvider(
        ProviderConfig(
            provider_id="claude-cli",
            binary="claude",
            model="m",
            timeout_seconds=timeout,
            transient_retries=retries,
            idle_timeout_seconds=idle,
        )
    )


class RetryLoopTests(unittest.TestCase):
    """The hang budget beside the transient budget, with the reader faked."""

    def setUp(self) -> None:
        patcher = mock.patch.object(providers_module, "_sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_second_hang_is_terminal_even_with_transient_retries_left(self):
        runs = _Runs(_hung(), _hung(), _ok())
        with (
            mock.patch.object(providers_module, "_stream_cli", runs),
            self.assertRaises(ProviderStageError) as ctx,
        ):
            _provider(retries=3).run(request())
        self.assertEqual(len(runs.calls), 2)
        self.assertEqual(ctx.exception.attempts, 2)
        self.assertIs(ctx.exception.telemetry["hang"], True)
        self.assertEqual(ctx.exception.telemetry["events_seen"], 4)
        self.assertEqual(ctx.exception.telemetry["num_turns"], 2)

    def test_a_hang_then_a_dropped_stream_then_success_uses_both_budgets(self):
        dropped = CliRun(
            returncode=1,
            stdout=lines(
                init_event(),
                result_event(
                    is_error=True, result="API Error: stream closed before completion", num_turns=1
                ),
            ),
            stderr="",
        )
        runs = _Runs(_hung(), dropped, _ok())
        with mock.patch.object(providers_module, "_stream_cli", runs):
            result = _provider(retries=2).run(request())
        self.assertEqual(len(runs.calls), 3)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(
            [("hung" in e, "stream closed" in e) for e in result.transient_errors],
            [(True, False), (False, True)],
        )
        self.assertEqual(result.events_seen, 2 + 2 + 2)

    def test_the_hang_error_is_a_transient_error_with_the_partial_envelope(self):
        with (
            mock.patch.object(providers_module, "_stream_cli", _Runs(_hung())),
            self.assertRaises(WorkerHungError) as ctx,
        ):
            _provider()._launch(["claude"], request())
        self.assertIsInstance(ctx.exception, providers_module.TransientProviderError)
        self.assertEqual(ctx.exception.envelope.num_turns, 1)
        self.assertEqual(ctx.exception.envelope.events_seen, 2)
        self.assertEqual(ctx.exception.telemetry["last_event_age_s"], 420.5)

    def test_the_reader_is_given_the_requests_wall_and_the_configured_idle_timeout(self):
        runs = _Runs(_ok())
        with mock.patch.object(providers_module, "_stream_cli", runs):
            _provider(timeout=2700, idle=420).run(request(timeout_seconds=2025))
        (call,) = runs.calls
        self.assertEqual(call["wall_seconds"], 2025)
        self.assertEqual(call["idle_seconds"], 420)
        self.assertEqual(call["cwd"], tempfile.gettempdir())

    def test_a_timed_out_run_carries_its_partial_telemetry_and_is_not_retried(self):
        runs = _Runs(_timed_out(turns=4), _ok())
        with (
            mock.patch.object(providers_module, "_stream_cli", runs),
            self.assertRaises(ProviderStageError) as ctx,
        ):
            _provider().run(request("implement", timeout_seconds=2025))
        self.assertEqual(len(runs.calls), 1)
        exc = ctx.exception
        self.assertTrue(exc.timed_out)
        self.assertEqual(exc.telemetry["num_turns"], 4)
        self.assertEqual(exc.telemetry["events_seen"], 5)
        self.assertEqual(exc.telemetry["input_tokens"], 40)
        self.assertEqual(exc.telemetry["wall_seconds_last_attempt"], 2025.2)
        self.assertIn("after 2025.2s (timeout_seconds=2025, max_turns=30", str(exc))


def _runtime(tmp: Path, provider) -> KernelRuntime:
    rt = object.__new__(KernelRuntime)
    rt.repo_root = ROOT
    rt.provider = provider
    rt.config = mock.Mock()
    rt.config.provider.model = "m"
    rt.check_stop = lambda: None
    return rt


def _bounded(role: str) -> AgentRequest:
    return AgentRequest(
        role=role,
        prompt="p",
        cwd="/tmp",
        allowed_tools=("Read",),
        max_turns=max_turns(role),
        max_budget_usd=12.0,
        timeout_seconds=stage_timeout_seconds(role),
        effort=effort(role),
    )


class TimedOutStageLeavesTelemetryTests(unittest.TestCase):
    """The record, the row and the FACTORY_STAGE line of a stage that died at its wall or hung
    say what its stream had shown: the empty `agent-test_author.json` of run 33987381035."""

    def _run(self, run: CliRun, role: str = "test_author", retries: int = 0):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _provider(retries=retries))
            out = io.StringIO()
            with (
                mock.patch.object(providers_module, "_stream_cli", _Runs(run, run)),
                mock.patch.object(providers_module, "_sleep", lambda s: None),
                contextlib.redirect_stdout(out),
                self.assertRaises(ProviderStageError),
            ):
                rt._agent_stage(paths, _bounded(role))
            record = json.loads(
                (paths.transcripts / f"agent-{role}.json").read_text(encoding="utf-8")
            )
            timings = (paths.transcripts / STAGE_TIMINGS).read_text(encoding="utf-8")
            rows = [json.loads(row) for row in timings.splitlines()]
            line = next(
                text for text in out.getvalue().splitlines() if text.startswith("FACTORY_STAGE ")
            )
            return record, rows[0], line

    def test_a_wall_timeout_is_recorded_with_turns_cost_events_and_the_flag(self):
        record, row, line = self._run(_timed_out(turns=4))
        self.assertEqual(record["outcome"], "failed")
        self.assertTrue(record["timed_out"])
        self.assertEqual(record["num_turns"], 4)
        self.assertEqual(record["events_seen"], 5)
        self.assertIsNone(
            record["total_cost_usd"], "no result event: the cost is unknown, not zero"
        )
        self.assertEqual(record["input_tokens"], 40)
        self.assertEqual(record["last_event_age_s"], 3.1)
        self.assertIn("m3", record["partial_output"])
        self.assertEqual(record["timeout_seconds"], stage_timeout_seconds("test_author"))
        self.assertEqual(row["num_turns"], 4)
        self.assertEqual(row["events_seen"], 5)
        self.assertIs(row["timed_out"], True)
        self.assertNotIn("hang", row)
        self.assertIn("name=test_author", line)
        self.assertIn(" turns=4 ", line)
        self.assertNotIn("cost_usd=", line, "an unknown cost is left out, not printed as 0")
        self.assertIn(" outcome=failed events=5 timed_out=true", line)
        self.assertNotIn("hang=true", line)

    def test_a_terminal_hang_is_recorded_with_the_hang_flag(self):
        record, row, line = self._run(_hung(), retries=2)
        self.assertEqual(record["outcome"], "failed")
        self.assertIs(record["hang"], True)
        self.assertFalse(record["timed_out"])
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(record["events_seen"], 4)
        self.assertEqual(record["num_turns"], 2)
        self.assertEqual(record["last_event_age_s"], 420.5)
        self.assertEqual(len(record["transient_errors"]), 2)
        self.assertIs(row["hang"], True)
        self.assertIn(" outcome=failed events=4 hang=true", line)

    def test_a_returned_stage_records_its_events_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _provider())
            out = io.StringIO()
            with (
                mock.patch.object(providers_module, "_stream_cli", _Runs(_ok())),
                contextlib.redirect_stdout(out),
            ):
                rt._agent_stage(paths, _bounded("holdout"))
            record = json.loads(
                (paths.transcripts / "agent-holdout.json").read_text(encoding="utf-8")
            )
        self.assertEqual(record["events_seen"], 2)
        self.assertEqual(record["timeout_seconds"], stage_timeout_seconds("holdout"))
        self.assertIn(" outcome=ok events=2", out.getvalue())

    def test_stage_line_shapes(self):
        base = {
            "kind": "agent",
            "name": "test_author",
            "seconds": 2025.3,
            "num_turns": 4,
            "cost_usd": 0.0,
            "outcome": "failed",
        }
        self.assertEqual(
            stage_line({**base, "events_seen": 5, "timed_out": True, "over_budget": True}),
            "FACTORY_STAGE kind=agent name=test_author seconds=2025.3 turns=4 cost_usd=0.0 "
            "outcome=failed events=5 timed_out=true over_budget=true",
        )
        self.assertEqual(
            stage_line({**base, "events_seen": 4, "hang": True}),
            "FACTORY_STAGE kind=agent name=test_author seconds=2025.3 turns=4 cost_usd=0.0 "
            "outcome=failed events=4 hang=true",
        )
        self.assertNotIn("events=", stage_line({**base, "events_seen": None}))


class PerRoleWallTests(unittest.TestCase):
    def test_the_ceiling_is_stated_from_the_runs_four_observations(self):
        """40.4, 23.4, 23.2 and 21.9 s per turn (build run 33987381035); the ceiling sits above
        the highest with a margin, and 35 is below it."""
        highest = max(OBSERVED_SECONDS_PER_TURN)
        self.assertAlmostEqual(highest, 40.4, places=1)
        self.assertGreater(OBSERVED_SECONDS_PER_TURN_CEILING, highest)
        self.assertGreaterEqual(OBSERVED_SECONDS_PER_TURN_CEILING, 41)
        self.assertEqual(OBSERVED_SECONDS_PER_TURN_CEILING, 45)

    def test_every_roles_wall_is_its_turn_budget_with_headroom(self):
        self.assertEqual(STAGE_WALL_HEADROOM, 1.5)
        for role, cap in ROLE_MAX_TURNS.items():
            with self.subTest(role):
                self.assertEqual(
                    stage_timeout_seconds(role),
                    math.ceil(cap * OBSERVED_SECONDS_PER_TURN_CEILING * STAGE_WALL_HEADROOM),
                )
                self.assertGreater(stage_timeout_seconds(role), stage_budget_seconds(role))
        self.assertEqual(stage_timeout_seconds("test_author"), 2025)
        self.assertEqual(stage_timeout_seconds("holdout"), 675)

    def test_over_budget_keeps_meaning_the_turn_budget_not_the_wall(self):
        self.assertEqual(
            stage_budget_seconds("test_author"), 30 * OBSERVED_SECONDS_PER_TURN_CEILING
        )

    def test_every_wall_fits_under_the_checked_in_maximum(self):
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
        maximum = raw["provider"]["timeout_seconds"]
        self.assertEqual(maximum, 2700)
        assert_caps_fit_timeout(maximum)
        for role in ROLE_MAX_TURNS:
            self.assertLessEqual(stage_timeout_seconds(role), maximum, role)
        self.assertGreater(
            max(stage_timeout_seconds(r) for r in ROLE_MAX_TURNS),
            1200,
            "the old single wall could not hold a 30-turn role at the measured rate",
        )

    def test_a_maximum_below_a_roles_wall_is_refused_by_name(self):
        with self.assertRaisesRegex(
            ValueError, r"turn cap for role '\w+' \(30\) needs a wall of 2025 s"
        ):
            assert_caps_fit_timeout(2024)
        with self.assertRaisesRegex(ValueError, "exceeds the provider maximum"):
            assert_caps_fit_timeout(1200)

    def test_a_request_refuses_a_non_positive_wall(self):
        for bad in (0, -1, True, 2.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                AgentRequest(role="implement", prompt="p", cwd="/tmp", timeout_seconds=bad)


class KernelConfigTests(unittest.TestCase):
    def test_the_checked_in_policy_carries_the_idle_timeout_and_the_raised_maximum(self):
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
        self.assertEqual(raw["provider"]["idle_timeout_seconds"], 420)
        self.assertEqual(raw["provider"]["timeout_seconds"], 2700)
        self.assertEqual(IDLE_TIMEOUT_SECONDS_DEFAULT, 420)

    def _load(self, mutate) -> ProviderConfig:
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
        mutate(raw["provider"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kernel.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with mock.patch.dict(os.environ, {"FACTORY_WORKDIR": tmp}):
                return load_config(path).provider

    def test_the_field_is_parsed_and_defaults_when_absent(self):
        self.assertEqual(self._load(lambda p: None).idle_timeout_seconds, 420)
        self.assertEqual(
            self._load(lambda p: p.pop("idle_timeout_seconds")).idle_timeout_seconds, 420
        )
        self.assertEqual(
            self._load(lambda p: p.update(idle_timeout_seconds=90)).idle_timeout_seconds, 90
        )

    def test_an_invalid_idle_timeout_is_refused(self):
        for bad in (0, -5, "420", True, 420.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self._load(lambda p, bad=bad: p.update(idle_timeout_seconds=bad))
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            self._load(lambda p: p.update(idle_timeout_seconds=2701))

    def test_the_dataclass_default_matches(self):
        config = ProviderConfig(
            provider_id="claude-cli", binary="c", model="m", timeout_seconds=2700
        )
        self.assertEqual(config.idle_timeout_seconds, IDLE_TIMEOUT_SECONDS_DEFAULT)


if __name__ == "__main__":
    unittest.main()
