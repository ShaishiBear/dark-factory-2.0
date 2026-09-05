"""Every worker runs at a stated effort, and every stage keeps its whole transcript (D-055).

Build run 33992451400 (issue #103) put the `test_author` stage of a three-criterion design at
2025 s, 14 turns and 76,248 stream events (about 5,400 per turn, against 476-1,790 for the four
stages before it), alive to the last second, its stream's tail an unbroken run of
`{"type":"system","subtype":"thinking_tokens",...}` events. Nothing bounded how long a turn
could think: the kernel named no effort level, so every worker ran at the CLI's default. And
when the wall killed the process, the record kept 1500 characters of tail and no
`agent-test_author.log` at all, so what the worker wrote in 34 minutes is unknown.

Two changes, pinned here. Every request names an effort level from `worker_policy.ROLE_EFFORT`
(workers `medium`, judges `high`), rendered as `--effort` on every launch, required by the
`_agent_stage` funnel as the fifth bound, overridable per deployment through
`provider.effort_overrides` (validated at load). And the provider tees every stdout line of
every attempt to `agent-<role>.log` as it arrives, so a killed process leaves its whole stream;
the thinking those events showed is summed into the record, the timing row and the
`FACTORY_STAGE` line as `thinking=N`, beside `effort=<level>`. The worker workflow's preflight
measures whether the route honours the level at all (`scripts/factory_effort_probe.py`).

The end-to-end cases reuse the fake CLI of `test_factory_stream_timeouts.py`: a real
subprocess emitting stream-json lines with controllable pacing, launched through the
provider's own argv and read by the provider's own reader.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for entry in (str(ROOT), str(HERE), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import factory_effort_probe as probe  # noqa: E402
from test_factory_stream_timeouts import (  # noqa: E402
    _FakeCliCase,
    _Runs,
    _runtime,
    assistant_event,
    hanging_steps,
    healthy_steps,
    init_event,
    lines,
    result_event,
    slow_steps,
)

from factory_kernel import providers as providers_module  # noqa: E402
from factory_kernel.agents import AgentRequest, AgentResult  # noqa: E402
from factory_kernel.config import ProviderConfig, load_config  # noqa: E402
from factory_kernel.providers import (  # noqa: E402
    PARTIAL_OUTPUT_CHARS,
    ClaudeCliProvider,
    CliRun,
    ProviderStageError,
    ResultEnvelope,
    parse_events,
    thinking_tokens,
    unwrap_result_envelope,
)
from factory_kernel.runtime import STAGE_TIMINGS, KernelRuntime, RunPaths, stage_line  # noqa: E402
from factory_kernel.worker_policy import (  # noqa: E402
    AUTHORITY_ROLES,
    EFFORT_LEVELS,
    JUDGE_EFFORT,
    ROLE_EFFORT,
    ROLE_MAX_TURNS,
    WORKER_EFFORT,
    effort,
    effort_rank,
    max_turns,
    stage_timeout_seconds,
    validate_effort_overrides,
)

KERNEL_JSON = ROOT / ".factory" / "kernel.json"
WORKER_WORKFLOW = ROOT / ".github" / "workflows" / "dark-factory-worker.yml"
JUDGE_ROLES = AUTHORITY_ROLES | {"triage"}
PROBE_LINE = re.compile(
    r"^FACTORY_PREFLIGHT_EFFORT_PROBE model=(?P<model>\S+) low_thinking=(?P<low>\d+) "
    r"high_thinking=(?P<high>\d+) honoured=(?P<honoured>true|false) "
    r"low_level=(?P<low_level>\S+) high_level=(?P<high_level>\S+) "
    r"low_events=\d+ high_events=\d+(?: error=(?P<error>\S+))?$"
)


def thinking_event(estimated: int, delta: int = 1, session_id: str = "s-1") -> dict:
    """The shape run 33992451400 printed 76,248 times for one stage."""
    return {
        "type": "system",
        "subtype": "thinking_tokens",
        "estimated_tokens": estimated,
        "estimated_tokens_delta": delta,
        "uuid": f"u-{estimated}",
        "session_id": session_id,
    }


def thinking_steps(counts: list[int], *, turns: int = 1) -> list[dict]:
    """A healthy session whose stream shows the given `estimated_tokens` values, in order."""
    steps: list[dict] = [{"emit": init_event()}]
    for n in range(turns):
        steps.append({"emit": assistant_event(f"msg_{n}", f"turn {n}")})
    steps.extend({"emit": thinking_event(value)} for value in counts)
    steps.append({"emit": result_event(num_turns=turns)})
    return steps


def bounded(role: str = "test_author", **overrides) -> AgentRequest:
    fields = dict(
        role=role,
        prompt="p",
        cwd=tempfile.gettempdir(),
        allowed_tools=("Read",),
        max_turns=max_turns(role),
        max_budget_usd=12.0,
        timeout_seconds=stage_timeout_seconds(role),
        effort=effort(role),
    )
    fields.update(overrides)
    return AgentRequest(**fields)


def provider_with(
    binary: str,
    *,
    overrides: dict | None = None,
    retries: int = 2,
    timeout: int = 60,
    idle: float = 1,
) -> ClaudeCliProvider:
    return ClaudeCliProvider(
        ProviderConfig(
            provider_id="claude-cli",
            binary=binary,
            model="m",
            timeout_seconds=timeout,
            transient_retries=retries,
            idle_timeout_seconds=idle,
            effort_overrides=dict(overrides or {}),
        )
    )


# --- the policy --------------------------------------------------------------------------------


class EffortPolicyTests(unittest.TestCase):
    def test_the_levels_are_the_pinned_clis_scale_lowest_first(self):
        """`claude --help` on 2.1.245 (the workflow's pin) and 2.1.259: low, medium, high,
        xhigh, max."""
        self.assertEqual(EFFORT_LEVELS, ("low", "medium", "high", "xhigh", "max"))
        self.assertEqual([effort_rank(level) for level in EFFORT_LEVELS], [0, 1, 2, 3, 4])
        with self.assertRaisesRegex(ValueError, "not one the CLI accepts"):
            effort_rank("ultra")

    def test_every_role_with_a_turn_cap_has_an_accepted_level(self):
        self.assertEqual(set(ROLE_EFFORT), set(ROLE_MAX_TURNS))
        for role in ROLE_MAX_TURNS:
            with self.subTest(role):
                self.assertIn(ROLE_EFFORT[role], EFFORT_LEVELS)
                self.assertEqual(effort(role), ROLE_EFFORT[role])
        with self.assertRaisesRegex(ValueError, "no effort level for role"):
            effort("nope")

    def test_judges_think_harder_than_workers_and_workers_are_bounded_below_the_default(self):
        """Judges (the five authorities and triage) at `high`, the CLI's default, where
        reasoning is the whole job; every worker at `medium`, one notch below, so thinking is
        bounded but not disabled. The test_author of run 33992451400 ran at the default."""
        self.assertEqual(JUDGE_EFFORT, "high")
        self.assertEqual(WORKER_EFFORT, "medium")
        self.assertGreater(effort_rank(JUDGE_EFFORT), effort_rank(WORKER_EFFORT))
        for role in ROLE_EFFORT:
            with self.subTest(role):
                expected = JUDGE_EFFORT if role in JUDGE_ROLES else WORKER_EFFORT
                self.assertEqual(effort(role), expected)
        self.assertEqual(effort("test_author"), "medium")
        self.assertEqual(effort("holdout"), "high")
        self.assertEqual(effort("triage"), "high")

    def test_the_workflow_pins_a_cli_that_accepts_the_flag(self):
        """`--effort` was verified on 2.1.245 and 2.1.259; the worker must not pin below."""
        text = WORKER_WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r"@anthropic-ai/claude-code@(\d+)\.(\d+)\.(\d+)", text)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(tuple(int(x) for x in match.groups()), (2, 1, 245))


# --- the flag on argv -------------------------------------------------------------------------


class EffortOnArgvTests(_FakeCliCase):
    def test_a_worker_asks_for_medium_and_a_judge_for_high(self):
        for role, level in (("test_author", "medium"), ("holdout", "high")):
            with self.subTest(role):
                self.scenario(healthy_steps())
                result = provider_with(self.binary).run(bounded(role))
                argv = self.launches()[-1]
                self.assertEqual(argv[argv.index("--effort") + 1], level)
                self.assertEqual(result.effort, level)
                self.assertEqual(argv.count("--effort"), 1)

    def test_a_configured_override_wins_and_is_what_the_record_says(self):
        self.scenario(healthy_steps())
        result = provider_with(self.binary, overrides={"test_author": "low"}).run(bounded())
        (argv,) = self.launches()
        self.assertEqual(argv[argv.index("--effort") + 1], "low")
        self.assertEqual(result.effort, "low")

    def test_an_override_for_another_role_changes_nothing(self):
        self.scenario(healthy_steps())
        provider_with(self.binary, overrides={"implement": "low"}).run(bounded("test_author"))
        (argv,) = self.launches()
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")

    def test_a_level_the_cli_does_not_accept_is_refused_before_any_launch(self):
        self.scenario(healthy_steps())
        with self.assertRaisesRegex(ValueError, "not one the CLI accepts"):
            provider_with(self.binary).run(bounded(effort="ultra"))
        with self.assertRaisesRegex(ValueError, "not one the CLI accepts"):
            provider_with(self.binary, overrides={"test_author": "ultra"}).run(bounded())
        self.assertEqual(self.launches(), [])

    def test_a_request_without_a_level_renders_no_flag(self):
        """The provider renders what it is given; refusing the absence is the funnel's job."""
        self.scenario(healthy_steps())
        result = provider_with(self.binary).run(bounded(effort=None))
        (argv,) = self.launches()
        self.assertNotIn("--effort", argv)
        self.assertIsNone(result.effort)

    def test_a_failed_stage_carries_the_level_it_ran_at(self):
        self.scenario(slow_steps(count=30, pace=0.1))
        with self.assertRaises(ProviderStageError) as ctx:
            provider_with(self.binary, overrides={"test_author": "low"}).run(
                bounded(timeout_seconds=1)
            )
        self.assertEqual(ctx.exception.telemetry["effort"], "low")


# --- the funnel ---------------------------------------------------------------------------------


class _Recording:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, request: AgentRequest, **kwargs) -> AgentResult:
        self.calls.append({"request": request, **kwargs})
        return AgentResult(provider_id="fake", model="fake", content="worker text", num_turns=1)


class FunnelTests(unittest.TestCase):
    def test_a_request_without_effort_never_reaches_the_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Recording()
            rt = _runtime(Path(tmp), provider)
            with self.assertRaises(RuntimeError) as ctx:
                rt._agent_stage(paths, bounded("implement", effort=None))
            self.assertIn("unbounded", str(ctx.exception))
            self.assertIn("effort", str(ctx.exception))
            self.assertEqual(provider.calls, [])
            self.assertFalse((paths.transcripts / "agent-implement.json").exists())
            self.assertFalse((paths.transcripts / "agent-implement.log").exists())

    def test_effort_is_the_fifth_required_bound(self):
        self.assertEqual(
            KernelRuntime.REQUEST_BOUNDS,
            ("allowed_tools", "max_turns", "max_budget_usd", "timeout_seconds", "effort"),
        )

    def test_the_funnel_hands_the_provider_the_stages_log_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Recording()
            rt = _runtime(Path(tmp), provider)
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent_stage(paths, bounded("implement"), before_retry=None)
            (call,) = provider.calls
            self.assertEqual(Path(call["transcript"]), paths.transcripts / "agent-implement.log")
            self.assertIn("before_retry", call)

    def test_a_provider_that_did_not_stream_still_gets_a_log_of_its_text(self):
        """The reconcile: the tee owns the file when it ran; otherwise the record writes the
        worker's text, as before, and the record says the effort the request asked for."""
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Recording())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rt._agent_stage(paths, bounded("implement"))
            log = paths.transcripts / "agent-implement.log"
            self.assertEqual(log.read_text(encoding="utf-8"), "worker text\n")
            record = json.loads(
                (paths.transcripts / "agent-implement.json").read_text(encoding="utf-8")
            )
            self.assertEqual(record["effort"], "medium")
            self.assertIsNone(record["thinking_tokens"])
            self.assertIn(" effort=medium", out.getvalue())
            self.assertNotIn("thinking=", out.getvalue())


# --- the override table -------------------------------------------------------------------------


class OverrideConfigTests(unittest.TestCase):
    def _load(self, mutate) -> ProviderConfig:
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
        mutate(raw["provider"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kernel.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with mock.patch.dict(os.environ, {"FACTORY_WORKDIR": tmp}):
                return load_config(path).provider

    def test_the_checked_in_policy_carries_an_empty_table(self):
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
        self.assertEqual(raw["provider"]["effort_overrides"], {})
        self.assertEqual(dict(self._load(lambda p: None).effort_overrides), {})

    def test_absent_means_no_override(self):
        self.assertEqual(dict(self._load(lambda p: p.pop("effort_overrides")).effort_overrides), {})
        self.assertEqual(validate_effort_overrides(None), {})

    def test_a_table_is_parsed(self):
        loaded = self._load(
            lambda p: p.update(effort_overrides={"test_author": "low", "holdout": "max"})
        )
        self.assertEqual(dict(loaded.effort_overrides), {"test_author": "low", "holdout": "max"})

    def test_a_level_the_cli_does_not_accept_is_refused(self):
        for bad in ("ultra", "", 3, None, "High"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "must be one of"):
                self._load(lambda p, bad=bad: p.update(effort_overrides={"test_author": bad}))

    def test_a_role_the_policy_does_not_know_is_refused(self):
        with self.assertRaisesRegex(ValueError, "does not know"):
            self._load(lambda p: p.update(effort_overrides={"tester": "low"}))

    def test_a_table_that_is_not_an_object_is_refused(self):
        for bad in ("low", ["test_author", "low"], 1):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "must be an object"):
                self._load(lambda p, bad=bad: p.update(effort_overrides=bad))

    def test_the_dataclass_default_is_no_override(self):
        config = ProviderConfig(provider_id="claude-cli", binary="c", model="m", timeout_seconds=60)
        self.assertEqual(dict(config.effort_overrides), {})


# --- the stream log -----------------------------------------------------------------------------


class StreamLogTests(_FakeCliCase):
    """`agent-<role>.log` holds every stdout line of every attempt, written as it arrives."""

    def setUp(self) -> None:
        super().setUp()
        self.log = self.tmp / "transcripts" / "agent-test_author.log"

    def _events_in_log(self) -> list[dict]:
        return parse_events(self.log.read_text(encoding="utf-8"))

    def test_a_completed_stage_log_is_the_whole_stream_under_an_attempt_header(self):
        self.scenario(healthy_steps())
        result = provider_with(self.binary).run(bounded(), transcript=self.log)
        self.assertEqual(result.content, "done")
        text = self.log.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("--- attempt 1 role=test_author started="), text[:80])
        self.assertEqual(
            [e["type"] for e in self._events_in_log()],
            ["system", "assistant", "user", "assistant", "result"],
        )
        self.assertEqual(len(self._events_in_log()), result.events_seen)
        self.assertIn("--- attempt 1 ended rc=0 timed_out=False hung=False", text)
        self.assertEqual(text.count("--- attempt"), 2, "one header and one end marker")

    def test_a_wall_killed_stage_leaves_everything_it_printed(self):
        self.scenario(slow_steps(count=30, pace=0.1))
        with self.assertRaises(ProviderStageError) as ctx:
            provider_with(self.binary, timeout=60, idle=5).run(
                bounded(timeout_seconds=1), transcript=self.log
            )
        exc = ctx.exception
        self.assertTrue(exc.timed_out)
        seen = self._events_in_log()
        self.assertGreaterEqual(len(seen), 4)
        self.assertEqual(len(seen), exc.telemetry["events_seen"], "the log is the stream")
        self.assertIn("timed_out=True", self.log.read_text(encoding="utf-8"))
        # The record's tail is still the capped last lines, for quick reading.
        self.assertLessEqual(len(exc.telemetry["partial_output"]), PARTIAL_OUTPUT_CHARS)

    def test_a_hung_stage_leaves_its_stream_and_the_retry_appends_under_its_own_header(self):
        self.scenario(attempts=[hanging_steps(), healthy_steps()])
        result = provider_with(self.binary, retries=2, idle=0.5).run(bounded(), transcript=self.log)
        self.assertEqual(result.attempts, 2)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("--- attempt 1 role=test_author", text)
        self.assertIn("--- attempt 2 role=test_author", text)
        self.assertIn("hung=True", text)
        self.assertEqual(len(self._events_in_log()), 2 + 5)
        self.assertEqual(len(self._events_in_log()), result.events_seen)

    def test_a_terminal_hang_still_leaves_both_attempts(self):
        self.scenario(hanging_steps())
        with self.assertRaises(ProviderStageError):
            provider_with(self.binary, retries=2, idle=0.5).run(bounded(), transcript=self.log)
        text = self.log.read_text(encoding="utf-8")
        self.assertEqual(text.count("--- attempt 1 role="), 1)
        self.assertEqual(text.count("--- attempt 2 role="), 1)
        self.assertEqual(len(self._events_in_log()), 2 + 2)

    def test_the_file_is_open_before_the_process_starts(self):
        """A process that prints nothing before it is killed still leaves the header."""
        self.scenario([{"sleep": 3.0}])
        with self.assertRaises(ProviderStageError):
            provider_with(self.binary, retries=0, idle=0.3).run(bounded(), transcript=self.log)
        self.assertTrue(self.log.exists())
        self.assertIn("--- attempt 1 role=test_author", self.log.read_text(encoding="utf-8"))

    def test_the_kernel_record_keeps_the_streamed_log_and_the_capped_tail(self):
        """Through the funnel with the real provider: the log is the stream, not the 1500
        characters the record keeps as `partial_output`."""
        self.scenario(slow_steps(count=40, pace=0.05))
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), provider_with(self.binary, timeout=60, idle=5))
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ProviderStageError):
                rt._agent_stage(paths, bounded(timeout_seconds=1))
            log = (paths.transcripts / "agent-test_author.log").read_text(encoding="utf-8")
            record = json.loads(
                (paths.transcripts / "agent-test_author.json").read_text(encoding="utf-8")
            )
        self.assertEqual(len(parse_events(log)), record["events_seen"])
        self.assertLessEqual(len(record["partial_output"]), PARTIAL_OUTPUT_CHARS)
        self.assertGreater(len(log), len(record["partial_output"]))
        self.assertTrue(record["timed_out"])

    def test_a_returned_stage_through_the_funnel_keeps_the_stream_not_only_the_text(self):
        self.scenario(healthy_steps())
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), provider_with(self.binary))
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent_stage(paths, bounded("implement"))
            log = (paths.transcripts / "agent-implement.log").read_text(encoding="utf-8")
        self.assertNotEqual(log, "done\n", "the post-hoc text write must not replace the stream")
        self.assertEqual(len(parse_events(log)), 5)
        self.assertIn('"result": "done"', log)


# --- thinking telemetry -------------------------------------------------------------------------


class ThinkingCountTests(unittest.TestCase):
    def test_high_water_marks_are_summed_across_resets_and_deltas_are_ignored(self):
        """Three turns whose counter restarts: 12 + 7 + 4. The deltas are written to sum to
        something else, to show they are not what is counted."""
        events = [
            init_event(),
            thinking_event(5, delta=5),
            thinking_event(9, delta=4),
            thinking_event(12, delta=3),
            assistant_event("m1", "x"),
            thinking_event(3, delta=99),
            thinking_event(7, delta=4),
            assistant_event("m2", "y"),
            thinking_event(4, delta=4),
            result_event(),
        ]
        self.assertEqual(thinking_tokens(events), 23)

    def test_a_counter_that_never_resets_is_its_final_value(self):
        events = [thinking_event(n, delta=1) for n in range(1, 101)]
        self.assertEqual(thinking_tokens(events), 100)

    def test_a_missed_line_does_not_lose_the_count(self):
        events = [thinking_event(10), thinking_event(30)]  # 11..29 never arrived
        self.assertEqual(thinking_tokens(events), 30)

    def test_other_events_and_bad_values_are_ignored(self):
        events = [
            init_event(),
            {"type": "system", "subtype": "thinking_tokens"},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": "12"},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": -1},
            {"type": "system", "subtype": "other", "estimated_tokens": 500},
            assistant_event("m1", "x"),
        ]
        self.assertEqual(thinking_tokens(events), 0)
        self.assertEqual(thinking_tokens([]), 0)

    def test_the_run_and_both_envelopes_carry_it(self):
        stream = lines(
            init_event(),
            thinking_event(40),
            thinking_event(90),
            assistant_event("m1", "x"),
            thinking_event(10),
            result_event(
                usage={
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "output_tokens_details": {"thinking_tokens": 97},
                }
            ),
        )
        run = CliRun(returncode=0, stdout=stream)
        self.assertEqual(run.thinking_tokens, 100)
        envelope = unwrap_result_envelope(
            run.envelope_text,
            role="implement",
            events_seen=run.events_seen,
            thinking_tokens=run.thinking_tokens,
        )
        self.assertEqual(envelope.thinking_tokens, 100)
        self.assertEqual(envelope.thinking_tokens_reported, 97)
        self.assertEqual(envelope.telemetry()["thinking_tokens"], 100)
        killed = CliRun(returncode=None, stdout=stream, timed_out=True, elapsed=3.0)
        partial = ResultEnvelope.from_events(killed)
        self.assertEqual(partial.thinking_tokens, 100)
        self.assertIsNone(partial.thinking_tokens_reported)


def _with_thinking(counts: list[int], **over) -> CliRun:
    events = [init_event(), assistant_event("m1", "x", input_tokens=10)]
    events.extend(thinking_event(value) for value in counts)
    fields = dict(returncode=0, stdout=lines(*events, result_event(num_turns=1)), stderr="")
    if over.get("timed_out") or over.get("hung"):
        fields["returncode"] = None
        fields["stdout"] = lines(*events)
    fields.update(over)
    return CliRun(**fields)


class ThinkingInTheRecordTests(unittest.TestCase):
    """The record, the timing row and the FACTORY_STAGE line say `thinking=N effort=<level>`
    for a returned stage, a killed one, and a retried one."""

    def _stage(self, *runs: CliRun, role: str = "test_author", expect_failure: bool = False):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _provider_faked())
            out = io.StringIO()
            with (
                mock.patch.object(providers_module, "_stream_cli", _Runs(*runs)),
                mock.patch.object(providers_module, "_sleep", lambda s: None),
                contextlib.redirect_stdout(out),
            ):
                if expect_failure:
                    with self.assertRaises(ProviderStageError):
                        rt._agent_stage(paths, bounded(role))
                else:
                    rt._agent_stage(paths, bounded(role))
            record = json.loads(
                (paths.transcripts / f"agent-{role}.json").read_text(encoding="utf-8")
            )
            rows = [
                json.loads(row)
                for row in (paths.transcripts / STAGE_TIMINGS)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            line = next(t for t in out.getvalue().splitlines() if t.startswith("FACTORY_STAGE "))
            return record, rows[0], line

    def test_a_returned_stage(self):
        record, row, line = self._stage(_with_thinking([5, 9, 12, 3, 7]))
        self.assertEqual(record["thinking_tokens"], 19)
        self.assertEqual(record["effort"], "medium")
        self.assertEqual(row["thinking_tokens"], 19)
        self.assertEqual(row["effort"], "medium")
        self.assertTrue(line.endswith(" outcome=ok events=8 thinking=19 effort=medium"), line)

    def test_a_killed_stage(self):
        record, row, line = self._stage(
            _with_thinking([100, 250], timed_out=True, elapsed=2025.0), expect_failure=True
        )
        self.assertEqual(record["thinking_tokens"], 250)
        self.assertEqual(record["effort"], "medium")
        self.assertTrue(record["timed_out"])
        self.assertEqual(row["thinking_tokens"], 250)
        self.assertIn(" outcome=failed events=4 timed_out=true", line)
        self.assertTrue(line.endswith(" thinking=250 effort=medium"), line)

    def test_a_judge_says_high(self):
        record, _row, line = self._stage(_with_thinking([50]), role="holdout")
        self.assertEqual(record["effort"], "high")
        self.assertTrue(line.endswith(" thinking=50 effort=high"), line)

    def test_thinking_is_summed_across_a_hung_attempt_and_its_retry(self):
        record, _row, line = self._stage(
            _with_thinking([30], hung=True, elapsed=421.0, last_event_age=420.5),
            _with_thinking([12]),
        )
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(record["thinking_tokens"], 42)
        self.assertIn(" thinking=42 ", line + " ")

    def test_stage_line_shapes(self):
        base = {
            "kind": "agent",
            "name": "holdout",
            "seconds": 12.5,
            "num_turns": 3,
            "cost_usd": 0.2,
            "outcome": "ok",
            "events_seen": 9,
        }
        self.assertEqual(
            stage_line({**base, "thinking_tokens": 0, "effort": "high"}),
            "FACTORY_STAGE kind=agent name=holdout seconds=12.5 turns=3 cost_usd=0.2 "
            "outcome=ok events=9 thinking=0 effort=high",
        )
        self.assertEqual(
            stage_line({**base, "outcome": "failed", "timed_out": True, "thinking_tokens": 7}),
            "FACTORY_STAGE kind=agent name=holdout seconds=12.5 turns=3 cost_usd=0.2 "
            "outcome=failed events=9 timed_out=true thinking=7",
        )
        self.assertNotIn("thinking=", stage_line({**base, "thinking_tokens": None}))
        self.assertNotIn("effort=", stage_line({**base, "effort": None}))


def _provider_faked() -> ClaudeCliProvider:
    return ClaudeCliProvider(
        ProviderConfig(
            provider_id="claude-cli",
            binary="claude",
            model="m",
            timeout_seconds=2700,
            transient_retries=2,
            idle_timeout_seconds=420,
        )
    )


# --- the preflight probe ------------------------------------------------------------------------


def _stream_with_thinking(count: int, *, is_error: bool = False) -> str:
    events = [init_event(), assistant_event("m1", "plan")]
    if count:
        events.extend(thinking_event(n) for n in range(1, count + 1))
    events.append(
        result_event(num_turns=1, is_error=is_error, subtype="error" if is_error else "success")
    )
    return lines(*events)


class _FakeRunner:
    """Answers each `--effort` level with a scripted stream."""

    def __init__(self, streams: dict[str, tuple[str, int]]) -> None:
        self.streams = streams
        self.argvs: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.argvs.append(list(argv))
        level = argv[argv.index("--effort") + 1]
        stdout, rc = self.streams[level]
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")


class ProbeScriptTests(unittest.TestCase):
    def test_the_default_levels_are_the_policys_lowest_and_highest(self):
        self.assertEqual(probe.policy_extremes(), (WORKER_EFFORT, JUDGE_EFFORT))
        self.assertEqual(probe.policy_extremes(), ("medium", "high"))

    def test_the_argv_is_the_providers_tool_less_request_one_turn_at_the_level(self):
        argv = probe.probe_argv("claude", "z-ai/glm-5.3-flash", "medium")
        self.assertEqual(argv[:2], ["claude", "--bare"])
        self.assertEqual(argv[argv.index("-p") + 1], probe.PROMPT)
        self.assertEqual(argv[argv.index("--model") + 1], "z-ai/glm-5.3-flash")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "1")
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")
        self.assertNotIn("--allowedTools", argv)
        with self.assertRaises(ValueError):
            probe.probe_argv("claude", "m", "ultra")

    def test_honoured_means_a_clear_margin(self):
        self.assertTrue(probe.honoured(100, 400))
        self.assertTrue(probe.honoured(0, 100))
        self.assertFalse(probe.honoured(100, 140), "ratio met by neither margin")
        self.assertFalse(probe.honoured(1000, 1099), "gap below MARGIN_TOKENS")
        self.assertFalse(probe.honoured(300, 350), "gap met, ratio not")
        self.assertFalse(probe.honoured(400, 100))

    def test_the_line_when_the_route_honours_the_level(self):
        runner = _FakeRunner(
            {"medium": (_stream_with_thinking(50), 0), "high": (_stream_with_thinking(400), 0)}
        )
        line = probe.run_probe("z-ai/glm-5.3-flash", runner=runner)
        match = PROBE_LINE.match(line)
        self.assertIsNotNone(match, line)
        self.assertEqual(match.group("model"), "z-ai/glm-5.3-flash")
        self.assertEqual((match.group("low"), match.group("high")), ("50", "400"))
        self.assertEqual(match.group("honoured"), "true")
        self.assertEqual((match.group("low_level"), match.group("high_level")), ("medium", "high"))
        self.assertIsNone(match.group("error"))
        self.assertEqual(len(runner.argvs), 2, "two one-turn calls, no more")
        self.assertEqual([a[a.index("--effort") + 1] for a in runner.argvs], ["medium", "high"])

    def test_the_line_when_it_does_not(self):
        runner = _FakeRunner(
            {"medium": (_stream_with_thinking(300), 0), "high": (_stream_with_thinking(310), 0)}
        )
        line = probe.run_probe("m", runner=runner)
        match = PROBE_LINE.match(line)
        self.assertIsNotNone(match, line)
        self.assertEqual(match.group("honoured"), "false")
        self.assertEqual((match.group("low"), match.group("high")), ("300", "310"))

    def test_a_process_that_did_not_return_is_false_with_the_error_named(self):
        runner = _FakeRunner(
            {
                "medium": (_stream_with_thinking(0), 0),
                "high": (_stream_with_thinking(900, is_error=True), 1),
            }
        )
        line = probe.run_probe("m", runner=runner)
        match = PROBE_LINE.match(line)
        self.assertIsNotNone(match, line)
        self.assertEqual(match.group("honoured"), "false", "an errored call proves nothing")
        self.assertTrue(match.group("error").startswith("high:"), line)

    def test_explicit_levels_must_be_in_order(self):
        runner = _FakeRunner(
            {"low": (_stream_with_thinking(1), 0), "max": (_stream_with_thinking(500), 0)}
        )
        line = probe.run_probe("m", low_level="low", high_level="max", runner=runner)
        self.assertIn("low_level=low high_level=max", line)
        with self.assertRaises(ValueError):
            probe.run_probe("m", low_level="high", high_level="medium", runner=runner)

    def test_main_prints_the_line_and_exits_zero_even_when_not_honoured(self):
        runner = _FakeRunner(
            {"medium": (_stream_with_thinking(5), 0), "high": (_stream_with_thinking(5), 0)}
        )
        out = io.StringIO()
        with mock.patch.object(probe, "subprocess") as sp, contextlib.redirect_stdout(out):
            sp.run = runner
            sp.TimeoutExpired = subprocess.TimeoutExpired
            sp.CompletedProcess = subprocess.CompletedProcess
            rc = probe.main(["--model", "m"])
        self.assertEqual(rc, 0)
        self.assertRegex(out.getvalue().strip(), PROBE_LINE)
        self.assertIn("honoured=false", out.getvalue())

    def test_the_workflow_runs_the_probe_after_the_route_probe_and_names_a_judges_level(self):
        text = WORKER_WORKFLOW.read_text(encoding="utf-8")
        step = text.split("Prove the worker's model route with the pinned CLI", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("scripts/factory_effort_probe.py", step)
        self.assertIn("FACTORY_PREFLIGHT_EFFORT_PROBE", step)
        self.assertIn("--effort high", step, "the route probe makes a judge's request")
        self.assertLess(
            step.index("FACTORY_PREFLIGHT_MODEL_ROUTE_OK"), step.index("factory_effort_probe.py")
        )
        self.assertIn(
            "honoured=false", step, "a probe that cannot run prints the line, never fails"
        )
        self.assertNotIn("exit 1", step.split("factory_effort_probe.py", 1)[1])


if __name__ == "__main__":
    unittest.main()
