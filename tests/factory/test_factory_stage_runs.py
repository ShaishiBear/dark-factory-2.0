"""Every run of a stage keeps its own record, and a record says how many processes it took.

Build run 34008561672 (issue #103) ran `test_author` twice: the static-gate hand-back (D-043)
re-ran the stage after biome refused the first draft. The first run was 2687 s, 41 turns and
$4.66; its `agent-test_author.json` was overwritten by the second run's (473 s, 23 turns), so
the only trace of the first run was two `--- attempt N ---` headers appended to the shared log.
The 2687 s itself exceeded the role's 2025 s per-process wall because the stage was two CLI
processes: attempt 1 ended after 717.8 s with `API Error: stream closed before completion`
(rc=1, retried as transient), attempt 2 ran 1964.2 s and returned. The record would have said
`attempts=2`; the timing row said nothing, and neither survived. These tests pin D-058: the
Nth run of a stage writes `agent-<role>.N.json` and `.N.log` beside the first run's files, every
run leaves its timing row with `stage_run`, a record and its row carry `attempts` and, for a
stage that hung once and completed on the retry, `hang` with the count in `hangs`.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from factory_kernel import providers as providers_module  # noqa: E402
from factory_kernel.agents import AgentResult  # noqa: E402
from factory_kernel.providers import CliRun, ProviderStageError  # noqa: E402
from factory_kernel.runtime import (  # noqa: E402
    STAGE_TIMINGS,
    KernelRuntime,
    RunPaths,
    stage_line,
    stage_record_name,
)
from test_factory_static_gate import (  # noqa: E402
    PROD_FILE,
    TEST_FILE,
    ScriptedProvider,
    ScriptedStatic,
    repo,
    runtime,
    spec,
)
from test_factory_stream_timeouts import (  # noqa: E402
    _bounded,
    _hung,
    _ok,
    _provider,
    _runtime,
    _Runs,
    init_event,
    lines,
    request,
    result_event,
)


def _rows(paths: RunPaths) -> list[dict]:
    text = (paths.transcripts / STAGE_TIMINGS).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _record(paths: RunPaths, stem: str) -> dict:
    return json.loads((paths.transcripts / f"{stem}.json").read_text(encoding="utf-8"))


def _stage_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("FACTORY_STAGE ")]


class _CountingProvider:
    """Returns a distinct result per call; streams a line into the transcript like the CLI."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.calls = 0
        self.fail_on = fail_on

    def run(self, request, *, transcript=None, **_kwargs):
        self.calls += 1
        if transcript is not None:
            with Path(transcript).open("a", encoding="utf-8") as handle:
                handle.write(f"--- attempt 1 role={request.role} call={self.calls} ---\n")
        if self.fail_on == self.calls:
            raise ProviderStageError(f"call {self.calls} failed", attempts=1)
        return AgentResult(
            provider_id="fake", model="m", content=f"text of call {self.calls}",
            num_turns=10 * self.calls, duration_ms=100 * self.calls, cost_usd=0.5 * self.calls,
        )


class RecordNameTests(unittest.TestCase):
    def test_the_first_run_keeps_the_plain_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(stage_record_name(Path(tmp), "test_author"), ("agent-test_author", 1))

    def test_a_json_or_a_log_on_disk_moves_the_next_run_to_a_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-test_author.json").write_text("{}", encoding="utf-8")
            self.assertEqual(stage_record_name(root, "test_author"), ("agent-test_author.2", 2))
            (root / "agent-test_author.2.log").write_text("", encoding="utf-8")
            self.assertEqual(stage_record_name(root, "test_author"), ("agent-test_author.3", 3))
            self.assertEqual(stage_record_name(root, "repair"), ("agent-repair", 1))

    def test_a_log_alone_counts_as_a_run(self):
        """A killed first run may have left only its streamed log; it is still a run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-implement.log").write_text("--- attempt 1", encoding="utf-8")
            self.assertEqual(stage_record_name(root, "implement"), ("agent-implement.2", 2))

    def test_every_suffix_matches_the_upload_glob(self):
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-worker.yml").read_text(encoding="utf-8")
        self.assertIn("transcripts/agent-*.log", workflow)
        self.assertIn("transcripts/agent-*.json", workflow)
        for stem in ("agent-test_author", "agent-test_author.2", "agent-test_author.13"):
            self.assertTrue(stem.startswith("agent-"), stem)


class TwoRunsThroughTheFunnelTests(unittest.TestCase):
    def _two_runs(self, provider) -> tuple[RunPaths, str]:
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), provider)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rt._agent_stage(paths, _bounded("test_author"))
                with contextlib.suppress(ProviderStageError):
                    rt._agent_stage(paths, _bounded("test_author"))
            first = _record(paths, "agent-test_author")
            second = _record(paths, "agent-test_author.2")
            logs = {
                name: (paths.transcripts / name).read_text(encoding="utf-8")
                for name in ("agent-test_author.log", "agent-test_author.2.log")
            }
            return first, second, logs, _rows(paths), out.getvalue()

    def test_the_second_run_writes_its_own_json_and_log_and_the_first_survives(self):
        first, second, logs, rows, out = self._two_runs(_CountingProvider())
        self.assertEqual(first["num_turns"], 10)
        self.assertEqual(first["record"], "agent-test_author")
        self.assertEqual(first["stage_run"], 1)
        self.assertEqual(second["num_turns"], 20)
        self.assertEqual(second["record"], "agent-test_author.2")
        self.assertEqual(second["stage_run"], 2)
        self.assertIn("call=1", logs["agent-test_author.log"])
        self.assertNotIn("call=2", logs["agent-test_author.log"], "the second run did not append to the first log")
        self.assertIn("call=2", logs["agent-test_author.2.log"])

    def test_each_run_leaves_its_own_timing_row_and_line(self):
        _, _, _, rows, out = self._two_runs(_CountingProvider())
        agent_rows = [r for r in rows if r["kind"] == "agent"]
        self.assertEqual([r["name"] for r in agent_rows], ["test_author", "test_author"])
        self.assertEqual([r["stage_run"] for r in agent_rows], [1, 2])
        self.assertEqual([r["record"] for r in agent_rows], ["agent-test_author", "agent-test_author.2"])
        self.assertEqual([r["num_turns"] for r in agent_rows], [10, 20])
        first_line, second_line = _stage_lines(out)
        self.assertNotIn("stage_run=", first_line)
        self.assertIn("name=test_author stage_run=2 seconds=", second_line)
        self.assertIn(" turns=20 ", second_line)

    def test_a_failed_second_run_is_recorded_beside_a_returned_first(self):
        first, second, logs, rows, out = self._two_runs(_CountingProvider(fail_on=2))
        self.assertEqual(first["outcome"], "ok")
        self.assertEqual(second["outcome"], "failed")
        self.assertEqual(second["record"], "agent-test_author.2")
        self.assertEqual(second["stage_run"], 2)
        self.assertEqual([r["outcome"] for r in rows if r["kind"] == "agent"], ["ok", "failed"])
        self.assertIn("stage_run=2", _stage_lines(out)[1])
        self.assertIn("outcome=failed", _stage_lines(out)[1])


class StaticGateHandBackKeepsBothRunsTests(unittest.TestCase):
    """The real hand-back: `test_author` lint-fails once, is re-run, and both runs are kept."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-stage-runs-")
        self.addCleanup(self.tmp.cleanup)
        self.root = repo(Path(self.tmp.name))

    def test_the_hand_back_re_run_keeps_the_first_runs_record(self):
        provider = ScriptedProvider([(TEST_FILE, "bad\n"), (TEST_FILE, "good\n")])
        static = ScriptedStatic([False, True])
        rt, paths = runtime(Path(self.tmp.name), self.root, provider, static)
        spec(paths, [TEST_FILE])
        env = {"ARTIFACTS_DIR": str(paths.artifacts)}
        out = io.StringIO()
        with (
            mock.patch("factory_kernel.worker_runtime.method_block", return_value=""),
            contextlib.redirect_stdout(out),
        ):
            rt._agent("test_author", self.root, paths, env=env)
        self.assertEqual(len(provider.requests), 2, "one hand-back")
        names = sorted(p.name for p in paths.transcripts.glob("agent-test_author*"))
        self.assertEqual(names, [
            "agent-test_author.2.json", "agent-test_author.2.log",
            "agent-test_author.json", "agent-test_author.log",
        ])
        first, second = _record(paths, "agent-test_author"), _record(paths, "agent-test_author.2")
        self.assertEqual((first["stage_run"], second["stage_run"]), (1, 2))
        self.assertEqual(first["outcome"], "ok")
        rows = [r for r in _rows(paths) if r["name"] == "test_author"]
        self.assertEqual([r["stage_run"] for r in rows], [1, 2])
        self.assertEqual(len(_stage_lines(out.getvalue())), 2)
        self.assertNotIn(PROD_FILE, "".join(names))


class AttemptsAndHangsInTheRecordTests(unittest.TestCase):
    """What a returned stage says about the processes it took."""

    def setUp(self) -> None:
        patcher = mock.patch.object(providers_module, "_sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stage(self, *runs: CliRun, retries: int = 2):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _provider(retries=retries))
            out = io.StringIO()
            with (
                mock.patch.object(providers_module, "_stream_cli", _Runs(*runs)),
                contextlib.redirect_stdout(out),
            ):
                result = rt._agent_stage(paths, _bounded("test_author"))
            record = _record(paths, "agent-test_author")
            return result, record, _rows(paths)[0], _stage_lines(out.getvalue())[0]

    @staticmethod
    def _dropped() -> CliRun:
        return CliRun(
            returncode=1,
            stdout=lines(
                init_event(),
                result_event(
                    is_error=True, result="API Error: stream closed before completion", num_turns=14
                ),
            ),
            stderr="",
            elapsed=717.8,
        )

    def test_a_hang_then_a_healthy_process_says_attempts_2_and_hang(self):
        result, record, row, line = self._stage(_hung(), _ok())
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.hangs, 1)
        self.assertEqual(record["attempts"], 2)
        self.assertIs(record["hang"], True)
        self.assertEqual(record["hangs"], 1)
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual(len(record["transient_errors"]), 1)
        self.assertEqual(row["attempts"], 2)
        self.assertIs(row["hang"], True)
        self.assertIn(" seconds=", line)
        self.assertIn(" attempts=2 ", line)
        self.assertIn(" hang=true", line)
        self.assertIn(" outcome=ok ", line)

    def test_a_dropped_stream_then_a_healthy_process_says_attempts_2_and_no_hang(self):
        """Run 34008561672's first `test_author`: rc=1 `stream closed` at 717.8 s, then 1964 s."""
        result, record, row, line = self._stage(self._dropped(), _ok())
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.hangs, 0)
        self.assertEqual(record["attempts"], 2)
        self.assertIs(record["hang"], False)
        self.assertEqual(record["hangs"], 0)
        self.assertEqual(len(record["transient_errors"]), 1)
        self.assertIn("API Error: stream closed before completion", record["transient_errors"][0])
        self.assertEqual(record["num_turns"], 14 + 3, "the dropped attempt's turns are summed")
        self.assertEqual(row["attempts"], 2)
        self.assertNotIn("hang", row)
        self.assertIn(" attempts=2 ", line)
        self.assertNotIn("hang=true", line)

    def test_a_single_process_stage_reads_as_before(self):
        result, record, row, line = self._stage(_ok())
        self.assertEqual(result.hangs, 0)
        self.assertEqual(record["attempts"], 1)
        self.assertIs(record["hang"], False)
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["stage_run"], 1)
        self.assertNotIn("attempts=", line)
        self.assertNotIn("stage_run=", line)
        self.assertNotIn("hang=true", line)

    def test_the_wall_is_the_stage_not_the_process(self):
        """`wall_seconds` spans every attempt; the per-process wall is `timeout_seconds`."""
        _, record, _, _ = self._stage(self._dropped(), _ok())
        self.assertIsInstance(record["wall_seconds"], float)
        self.assertEqual(record["timeout_seconds"], 2025)
        self.assertIn("plus the backoff between them", KernelRuntime._record_agent.__doc__)

    def test_the_provider_counts_hangs_on_a_returned_result(self):
        runs = _Runs(_hung(), _ok())
        with mock.patch.object(providers_module, "_stream_cli", runs):
            result = _provider(retries=2).run(request())
        self.assertEqual((result.attempts, result.hangs), (2, 1))
        with mock.patch.object(providers_module, "_stream_cli", _Runs(_ok())):
            result = _provider(retries=2).run(request())
        self.assertEqual((result.attempts, result.hangs), (1, 0))


class StageLineShapeTests(unittest.TestCase):
    def test_stage_run_and_attempts_are_printed_only_when_they_say_something(self):
        base = {"kind": "agent", "name": "repair", "seconds": 1.5, "outcome": "ok"}
        self.assertEqual(stage_line(base), "FACTORY_STAGE kind=agent name=repair seconds=1.5 outcome=ok")
        self.assertEqual(
            stage_line({**base, "stage_run": 1, "attempts": 1}),
            "FACTORY_STAGE kind=agent name=repair seconds=1.5 outcome=ok",
        )
        self.assertEqual(
            stage_line({**base, "stage_run": 2, "attempts": 3, "hang": True}),
            "FACTORY_STAGE kind=agent name=repair stage_run=2 seconds=1.5 attempts=3 outcome=ok hang=true",
        )


class RecordHelpersTests(unittest.TestCase):
    def test_record_agent_without_a_stem_writes_the_plain_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            result = AgentResult(provider_id="p", model="m", content="t", attempts=2, hangs=1,
                                 transient_errors=("hung",))
            with contextlib.redirect_stdout(io.StringIO()):
                rt._record_agent(paths, "holdout", result, started=0.0)
            record = _record(paths, "agent-holdout")
        self.assertEqual(record["record"], "agent-holdout")
        self.assertEqual(record["stage_run"], 1)
        self.assertIs(record["hang"], True)
        self.assertEqual(record["hangs"], 1)

    def test_record_failed_agent_honours_the_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rt._record_failed_agent(
                    paths, "repair", RuntimeError("boom"), started=0.0,
                    record="agent-repair.2", stage_run=2,
                )
            record = _record(paths, "agent-repair.2")
            rows = _rows(paths)
        self.assertEqual(record["outcome"], "failed")
        self.assertEqual(record["stage_run"], 2)
        self.assertEqual(rows[0]["record"], "agent-repair.2")
        self.assertIn("name=repair stage_run=2 ", _stage_lines(out.getvalue())[0])


if __name__ == "__main__":
    unittest.main()
