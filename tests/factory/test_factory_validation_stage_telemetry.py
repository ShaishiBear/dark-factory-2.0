"""Every model stage of validation leaves a timing record, and every stage prints a line (D-050).

Validation run 33960088633 uploaded a `stage-timings.jsonl` with five deterministic rows that
ended at 10:15:44Z and one that started at 10:40:37Z. The blinded holdout, the architecture
holdout and the three pre-code certifiers ran in between (their artifacts exist) and recorded
nothing, because they called `provider.run` directly instead of through the path that writes
`agent-<role>.*`. The Actions log was equally silent: the kernel's stdout is a pipe there and
nothing was flushed until the process died.

These tests pin three things. Through the rehearsal harness, the real `validate_pr` records all
five validation roles. Through a fake provider, a validation stage that raises is recorded like
a build stage that raises. And `record_stage_timing` prints one flushed `FACTORY_STAGE` line per
stage, carrying `over_budget=true` when the wall clock exceeded the role's turn cap at the
per-turn ceiling. The flag is telemetry: nothing here asserts a behaviour change, because there
is none.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.agents import AgentRequest, AgentResult  # noqa: E402
from factory_kernel.providers import ProviderStageError  # noqa: E402
from factory_kernel.runtime import (  # noqa: E402
    STAGE_LINE_PREFIX,
    STAGE_TIMINGS,
    KernelRuntime,
    RunPaths,
    over_budget,
    stage_line,
)
from factory_kernel.worker_policy import (  # noqa: E402
    OBSERVED_SECONDS_PER_TURN_CEILING,
    ROLE_MAX_TURNS,
    stage_budget_seconds,
)
from harness.rehearsal import Scenario, rehearse  # noqa: E402

VALIDATION_ROLES = (
    "holdout",
    "architecture-holdout",
    "contract-certifier",
    "design-certifier",
    "governor-certifier",
)

STAGE_LINE = re.compile(
    r"^FACTORY_STAGE kind=(?P<kind>agent|exec) name=(?P<name>\S+) seconds=(?P<seconds>\d+(\.\d+)?)"
    r"(?: turns=(?P<turns>\d+))?(?: cost_usd=(?P<cost>\S+))? outcome=(?P<outcome>ok|failed|refused)"
    r"(?P<over> over_budget=true)?$"
)


def _rows(paths: RunPaths) -> list[dict]:
    text = (paths.transcripts / STAGE_TIMINGS).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _record(paths: RunPaths, role: str) -> dict:
    return json.loads((paths.transcripts / f"agent-{role}.json").read_text(encoding="utf-8"))


def _stage_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(STAGE_LINE_PREFIX + " ")]


class _Provider:
    """Returns a fixed result, or raises what the test hands it."""

    def __init__(self, result: AgentResult | None = None, exc: BaseException | None = None):
        self.result = result
        self.exc = exc
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest, **_kwargs: object) -> AgentResult:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        assert self.result is not None
        return self.result


def _runtime(tmp: Path, provider: _Provider) -> KernelRuntime:
    rt = object.__new__(KernelRuntime)
    rt.repo_root = ROOT
    rt.provider = provider
    rt.config = mock.Mock()
    rt.config.provider.model = "fake-model"
    prompt = tmp / "prompt.md"
    prompt.write_text("judge prompt\n", encoding="utf-8")
    rt.config.prompt_path = lambda role, cwd: prompt
    rt.check_stop = lambda: None
    return rt


def _ok(structured: dict) -> AgentResult:
    return AgentResult(
        provider_id="fake",
        model="fake-model",
        content=json.dumps(structured),
        structured_output=structured,
        num_turns=3,
        duration_ms=1500,
        cost_usd=0.25,
    )


class ValidationStagesAreRecordedTests(unittest.TestCase):
    """The real validate_pr, against the rehearsal fakes, records every authority it runs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.records: dict[str, dict] = {}
        cls.rows: dict[str, dict] = {}
        cls.logs: dict[str, str] = {}
        cls.contents: dict[str, str] = {}
        original = KernelRuntime._record_agent

        def spy(self, paths, role, result, *, started):
            original(self, paths, role, result, started=started)
            # The rehearsal's work root is deleted when it returns, so read the files now.
            cls.records[role] = _record(paths, role)
            cls.rows[role] = next(r for r in _rows(paths) if r["name"] == role)
            cls.logs[role] = (paths.transcripts / f"agent-{role}.log").read_text(encoding="utf-8")
            cls.contents[role] = result.content

        out = io.StringIO()
        with (
            mock.patch.object(KernelRuntime, "_record_agent", spy),
            contextlib.redirect_stdout(out),
        ):
            cls.trace = rehearse(Scenario("happy"))
        cls.stdout = out.getvalue()

    def test_the_rehearsal_merged_so_every_authority_below_actually_ran(self):
        self.assertEqual(self.trace.outcome, "returned", self.trace.error)
        self.assertTrue(self.trace.happened("merge_squash"))
        self.assertEqual(sorted(self.trace.names("agent")), sorted(VALIDATION_ROLES))

    def test_every_validation_role_has_a_record_and_a_timing_row(self):
        for role in VALIDATION_ROLES:
            with self.subTest(role):
                record = self.records[role]
                row = self.rows[role]
                self.assertEqual(record["role"], role)
                self.assertEqual(record["outcome"], "ok")
                self.assertEqual(record["model"], "rehearsal")
                self.assertIn("wall_seconds", record)
                self.assertEqual(record["budget_seconds"], stage_budget_seconds(role))
                self.assertFalse(record["over_budget"])
                self.assertEqual(row["kind"], "agent")
                self.assertEqual(row["outcome"], "ok")
                self.assertEqual(row["model"], "rehearsal")
                self.assertIn("seconds", row)
                self.assertNotIn("over_budget", row)

    def test_the_agent_log_holds_the_authority_text(self):
        """`agent-<role>.log` is the worker's text, which for an authority is its verdict JSON."""
        self.assertEqual(sorted(self.trace.agent_prompts), sorted(VALIDATION_ROLES))
        for role in VALIDATION_ROLES:
            with self.subTest(role):
                self.assertEqual(self.logs[role], self.contents[role] + "\n")
                self.assertEqual(json.loads(self.logs[role])["verdict"], "pass")

    def test_a_stage_line_is_printed_for_every_validation_role(self):
        names = []
        for line in _stage_lines(self.stdout):
            match = STAGE_LINE.match(line)
            self.assertIsNotNone(match, line)
            if match.group("kind") == "agent":
                names.append(match.group("name"))
                self.assertEqual(match.group("outcome"), "ok")
        self.assertEqual(sorted(names), sorted(VALIDATION_ROLES))


class FailedValidationStageTests(unittest.TestCase):
    """A validation authority that raises is recorded exactly as a build worker that raises."""

    def test_a_holdout_that_raises_is_recorded_then_reraised(self):
        exc = ProviderStageError(
            "agent worker role='holdout' failed on a transient provider error 3 time(s)",
            telemetry={"num_turns": 21, "total_cost_usd": 0.57},
            attempts=3,
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Provider(exc=exc))
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(ProviderStageError) as ctx:
                rt._run_blinded_holdout(paths, {"contract": {}, "diff": ""})
            self.assertIs(ctx.exception, exc)
            record = _record(paths, "holdout")
            self.assertEqual(record["outcome"], "failed")
            self.assertEqual(record["error_class"], "ProviderStageError")
            self.assertEqual(record["model"], "fake-model")
            self.assertEqual(record["num_turns"], 21)
            self.assertEqual(record["attempts"], 3)
            rows = _rows(paths)
            self.assertEqual([(r["name"], r["outcome"]) for r in rows], [("holdout", "failed")])
            self.assertEqual(rows[0]["cost_usd"], 0.57)
            self.assertEqual(rows[0]["model"], "fake-model")
            lines = _stage_lines(out.getvalue())
            self.assertEqual(len(lines), 1)
            match = STAGE_LINE.match(lines[0])
            self.assertIsNotNone(match, lines[0])
            self.assertEqual(match.group("name"), "holdout")
            self.assertEqual(match.group("outcome"), "failed")
            self.assertEqual(match.group("turns"), "21")
            self.assertEqual(match.group("cost"), "0.57")
            self.assertFalse((paths.transcripts / "agent-holdout.log").exists())

    def test_a_certifier_that_raises_is_recorded_under_its_own_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Provider(exc=ValueError("worker did not return JSON")))
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                rt._run_precode_certifier(
                    paths, claim_id="design", role="design-certifier", inputs={"design": {}}
                )
            record = _record(paths, "design-certifier")
            self.assertEqual(record["outcome"], "failed")
            self.assertEqual(record["error_class"], "ValueError")
            self.assertEqual(_rows(paths)[0]["name"], "design-certifier")

    def test_a_returned_certifier_is_recorded_before_its_verdict_is_judged(self):
        """The record is evidence of the stage, not of the verdict: a certifier that returned a
        rejection is still an `ok` stage, and the refusal that follows is the kernel's."""
        verdict = {"version": "1.0", "certifies": "contract", "verdict": "fail", "findings": []}
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Provider(result=_ok(verdict)))
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(Exception) as ctx:
                rt._run_precode_certifier(
                    paths, claim_id="contract", role="contract-certifier", inputs={}
                )
            self.assertIn("rejected the contract claim", str(ctx.exception))
            record = _record(paths, "contract-certifier")
            self.assertEqual(record["outcome"], "ok")
            self.assertEqual(record["num_turns"], 3)
            self.assertEqual(
                (paths.transcripts / "agent-contract-certifier.log").read_text(encoding="utf-8"),
                json.dumps(verdict) + "\n",
            )


class StageLineTests(unittest.TestCase):
    def test_an_agent_line_carries_turns_cost_and_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            result = AgentResult(
                provider_id="p", model="m", content="t", num_turns=4, duration_ms=99, cost_usd=0.5
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rt._record_agent(paths, "holdout", result, started=_now() - 2)
            lines = _stage_lines(out.getvalue())
            self.assertEqual(len(lines), 1)
            match = STAGE_LINE.match(lines[0])
            self.assertIsNotNone(match, lines[0])
            self.assertEqual(match.group("kind"), "agent")
            self.assertEqual(match.group("name"), "holdout")
            self.assertEqual(match.group("turns"), "4")
            self.assertEqual(match.group("cost"), "0.5")
            self.assertEqual(match.group("outcome"), "ok")
            self.assertIsNone(match.group("over"))
            self.assertGreaterEqual(float(match.group("seconds")), 2.0)

    def test_an_exec_line_omits_turns_and_cost_and_says_refused_on_a_nonzero_exit(self):
        from factory_kernel.refusal import ToolRefused

        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            out = io.StringIO()
            with (
                contextlib.redirect_stdout(out),
                mock.patch.dict("os.environ", {"PATH": _path()}, clear=True),
            ):
                rt._exec(
                    [sys.executable, "-c", "print('ok')"],
                    cwd=Path(tmp),
                    transcript=paths.transcripts / "contract-gate.log",
                )
                with self.assertRaises(ToolRefused):
                    rt._exec(
                        [sys.executable, "-c", "raise SystemExit(3)"],
                        cwd=Path(tmp),
                        transcript=paths.transcripts / "red-gate.log",
                    )
            lines = _stage_lines(out.getvalue())
            self.assertEqual(len(lines), 2)
            first, second = (STAGE_LINE.match(line) for line in lines)
            self.assertIsNotNone(first, lines[0])
            self.assertIsNotNone(second, lines[1])
            self.assertEqual(
                (first.group("kind"), first.group("name"), first.group("outcome")),
                ("exec", "contract-gate", "ok"),
            )
            self.assertIsNone(first.group("turns"))
            self.assertIsNone(first.group("cost"))
            self.assertEqual(
                (second.group("name"), second.group("outcome")), ("red-gate", "refused")
            )
            rows = _rows(paths)
            self.assertEqual(
                [(r["name"], r["outcome"]) for r in rows],
                [("contract-gate", "ok"), ("red-gate", "refused")],
            )

    def test_the_line_is_flushed(self):
        """Under Actions the kernel's stdout is a pipe: an unflushed line reaches the job log
        when the process exits, which is exactly when it stops being useful."""
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            result = AgentResult(provider_id="p", model="m", content="t", num_turns=1)
            with mock.patch("builtins.print") as printed:
                rt._record_agent(paths, "holdout", result, started=_now())
            stage_calls = [
                c
                for c in printed.call_args_list
                if c.args and str(c.args[0]).startswith(STAGE_LINE_PREFIX)
            ]
            self.assertEqual(len(stage_calls), 1)
            self.assertIs(stage_calls[0].kwargs.get("flush"), True)

    def test_stage_line_shape_from_a_row(self):
        self.assertEqual(
            stage_line(
                {
                    "kind": "agent",
                    "name": "holdout",
                    "seconds": 934.1,
                    "num_turns": 9,
                    "cost_usd": 0.41,
                    "outcome": "ok",
                    "over_budget": True,
                }
            ),
            "FACTORY_STAGE kind=agent name=holdout seconds=934.1 turns=9 cost_usd=0.41 "
            "outcome=ok over_budget=true",
        )
        self.assertEqual(
            stage_line(
                {"kind": "exec", "name": "security", "seconds": 1.36, "rc": 0, "outcome": "ok"}
            ),
            "FACTORY_STAGE kind=exec name=security seconds=1.36 outcome=ok",
        )


class OverBudgetTests(unittest.TestCase):
    """`over_budget` is `max_turns(role) * OBSERVED_SECONDS_PER_TURN_CEILING` exceeded. Data only."""

    def test_the_budget_is_the_turn_cap_at_the_ceiling(self):
        for role, cap in ROLE_MAX_TURNS.items():
            with self.subTest(role):
                self.assertEqual(
                    stage_budget_seconds(role), cap * OBSERVED_SECONDS_PER_TURN_CEILING
                )
        self.assertIsNone(stage_budget_seconds("no-such-role"))
        self.assertFalse(over_budget("no-such-role", 10**9))

    def test_run_33960088633s_holdout_would_have_been_flagged(self):
        """934 s against a 10-turn cap: the number the caps must be tuned from."""
        self.assertEqual(stage_budget_seconds("holdout"), 350)
        self.assertTrue(over_budget("holdout", 934))
        self.assertFalse(over_budget("holdout", 349))
        self.assertFalse(over_budget("holdout", 350))

    def test_a_slow_returned_stage_is_flagged_in_record_row_and_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            result = AgentResult(provider_id="p", model="m", content="t", num_turns=9, cost_usd=0.4)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rt._record_agent(
                    paths, "holdout", result, started=_now() - stage_budget_seconds("holdout") - 1
                )
            record = _record(paths, "holdout")
            self.assertTrue(record["over_budget"])
            self.assertEqual(record["budget_seconds"], 350)
            row = _rows(paths)[0]
            self.assertIs(row["over_budget"], True)
            self.assertEqual(row["outcome"], "ok")
            match = STAGE_LINE.match(_stage_lines(out.getvalue())[0])
            self.assertIsNotNone(match)
            self.assertEqual(match.group("over"), " over_budget=true")
            self.assertEqual(match.group("outcome"), "ok")

    def test_a_slow_failed_stage_is_flagged_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            exc = ProviderStageError("agent worker timed out role='implement'", timed_out=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rt._record_failed_agent(
                    paths, "implement", exc, started=_now() - stage_budget_seconds("implement") - 5
                )
            record = _record(paths, "implement")
            self.assertTrue(record["over_budget"])
            self.assertTrue(record["timed_out"])
            row = _rows(paths)[0]
            self.assertIs(row["over_budget"], True)
            self.assertEqual(row["outcome"], "failed")
            self.assertIn(" over_budget=true", _stage_lines(out.getvalue())[0])

    def test_a_stage_within_budget_is_not_flagged_anywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            result = AgentResult(provider_id="p", model="m", content="t", num_turns=2)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rt._record_agent(paths, "holdout", result, started=_now() - 1)
            self.assertFalse(_record(paths, "holdout")["over_budget"])
            self.assertNotIn("over_budget", _rows(paths)[0])
            self.assertNotIn("over_budget", _stage_lines(out.getvalue())[0])

    def test_the_flag_changes_nothing_the_kernel_decides(self):
        """A returned stage over budget is still a returned stage: the result is handed back
        unchanged and no refusal is raised. Cap tuning happens in worker_policy, from the data."""
        verdict = {"version": "1.0", "findings": [], "verdict": "pass"}
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Provider(result=_ok(verdict)))
            far_past = _now() - 10_000
            with (
                contextlib.redirect_stdout(io.StringIO()),
                mock.patch("factory_kernel.runtime.time.time", side_effect=[far_past, _now()]),
            ):
                value = rt._run_blinded_holdout(paths, {"contract": {}, "diff": ""})
            self.assertEqual(value, verdict)
            self.assertTrue(_record(paths, "holdout")["over_budget"])
            self.assertEqual(json.loads((paths.artifacts / "holdout.json").read_text()), verdict)


class EveryModelCallIsRecordedTests(unittest.TestCase):
    """Source-shape pin: the runtime has exactly one `provider.run` call, inside `_agent_stage`.

    The defect was three direct calls in validation authorities beside the recorded one in
    `_agent`. A future authority that calls the provider directly would silently reopen it.
    """

    def test_runtime_calls_the_provider_only_from_the_stage_funnel(self):
        import ast

        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        callers = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "run"
                        and isinstance(inner.func.value, ast.Attribute)
                        and inner.func.value.attr == "provider"
                    ):
                        callers.append(node.name)
        self.assertEqual(callers, ["_agent_stage"])

    def test_worker_runtime_does_not_call_the_provider_directly(self):
        source = (ROOT / "factory_kernel" / "worker_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("self.provider.run(", source)
        self.assertIn("self._agent_stage(", source)


def _now() -> float:
    import time

    return time.time()


def _path() -> str:
    import os

    return os.environ.get("PATH", "")


if __name__ == "__main__":
    unittest.main()
