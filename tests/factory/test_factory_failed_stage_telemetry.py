"""A worker stage that fails is recorded exactly as one that returns (D-041).

Two `test_author` stream drops left only the exception text as evidence because the kernel
wrote `agent-<role>.json` and the timing row only after `provider.run` returned. The record is
observability: the failure still propagates unchanged, and the error text is scrubbed of every
secret shape the guard knows before it reaches an uploaded artifact.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import providers as providers_module  # noqa: E402
from factory_kernel.agents import AgentRequest, AgentResult  # noqa: E402
from factory_kernel.config import ProviderConfig  # noqa: E402
from factory_kernel.providers import ClaudeCliProvider, ProviderStageError  # noqa: E402
from factory_kernel.runtime import RunPaths, STAGE_TIMINGS  # noqa: E402

# Assembled at run time so no added line in this file matches the guard's secret patterns;
# the joined value does, which is what the scrub test needs.
FAKE_SECRET = "".join(("sk-", "or-v1-", "abcdefghijklmnopqrstuvwxyz0123456789"))
TRANSIENT = "API Error: stream closed before completion"


def envelope(**overrides) -> str:
    raw = {
        "type": "result", "subtype": "success", "is_error": False, "result": "done",
        "num_turns": 3, "duration_ms": 1000, "total_cost_usd": 0.10, "session_id": "s-1",
        "usage": {"input_tokens": 50, "output_tokens": 7},
    }
    raw.update(overrides)
    return json.dumps(raw) + "\n"


def transient() -> str:
    return envelope(is_error=True, result=TRANSIENT, num_turns=7, duration_ms=11577,
                    total_cost_usd=0.19, usage={"input_tokens": 29801, "output_tokens": 329})


class _Runs:
    def __init__(self, *stdouts) -> None:
        self.stdouts = list(stdouts)

    def __call__(self, argv, **kwargs):
        item = self.stdouts.pop(0)
        if isinstance(item, BaseException):
            raise item
        rc, out = item if isinstance(item, tuple) else (0, item)
        return mock.Mock(returncode=rc, stdout=out, stderr="")


def _provider(retries: int = 2) -> ClaudeCliProvider:
    return ClaudeCliProvider(ProviderConfig(
        provider_id="claude-cli", binary="claude", model="m", timeout_seconds=60,
        transient_retries=retries,
    ))


class _Raising:
    """A provider whose run() raises what the test hands it."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def run(self, request: AgentRequest, **_kwargs: object) -> AgentResult:
        raise self.exc


def _runtime(tmp: Path, provider):
    from factory_kernel.worker_runtime import WorkerControlledRuntime

    rt = object.__new__(WorkerControlledRuntime)
    rt.repo_root = ROOT
    rt.provider = provider
    rt.config = mock.Mock()
    rt.config.provider.model = "fake"
    prompt = tmp / "prompt.md"
    prompt.write_text("role prompt\n", encoding="utf-8")
    rt.config.prompt_path = lambda role, cwd: prompt
    rt.check_stop = lambda: None
    rt._assert_clean = lambda cwd: None
    # The post-stage checkout inspection runs git against the cwd; the mutation runner's
    # repo-shaped copy is not a git repository, and the record is what this suite tests.
    rt._refuse_literal_artifacts_dir = lambda cwd: None
    return rt


def _run_stage(rt, paths: RunPaths, role: str = "test_author") -> None:
    with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""):
        rt._agent(role, ROOT, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})


def _record(paths: RunPaths, role: str) -> dict:
    return json.loads((paths.transcripts / f"agent-{role}.json").read_text(encoding="utf-8"))


def _rows(paths: RunPaths) -> list[dict]:
    return [json.loads(l) for l in (paths.transcripts / STAGE_TIMINGS).read_text(encoding="utf-8").splitlines()]


class ProviderErrorTelemetryTests(unittest.TestCase):
    """The refusals the provider raises carry what the stage cost."""

    def test_exhausted_transient_retries_carry_summed_telemetry(self):
        prov = _provider(retries=2)
        runs = _Runs((1, transient()), (1, transient()), (1, transient()))
        with mock.patch.object(providers_module.subprocess, "run", runs), \
                mock.patch.object(providers_module, "_sleep", lambda s: None):
            with self.assertRaises(ProviderStageError) as ctx:
                prov.run(AgentRequest(role="test_author", prompt="p", cwd="/tmp", max_turns=30))
        exc = ctx.exception
        self.assertEqual(exc.attempts, 3)
        self.assertEqual(len(exc.transient_errors), 3)
        self.assertEqual(exc.telemetry["num_turns"], 21)
        self.assertEqual(exc.telemetry["input_tokens"], 29801 * 3)
        self.assertAlmostEqual(exc.telemetry["total_cost_usd"], 0.57)
        self.assertFalse(exc.timed_out)
        self.assertIn("failed on a transient provider error 3 time(s)", str(exc))

    def test_terminal_nonzero_exit_carries_its_envelope_counts(self):
        prov = _provider(retries=2)
        terminal = envelope(is_error=True, subtype="error_max_turns", result="max turns", num_turns=30)
        with mock.patch.object(providers_module.subprocess, "run", _Runs((1, terminal))):
            with self.assertRaises(ProviderStageError) as ctx:
                prov.run(AgentRequest(role="implement", prompt="p", cwd="/tmp", max_turns=30))
        self.assertEqual(ctx.exception.telemetry["num_turns"], 30)
        self.assertEqual(ctx.exception.telemetry["subtype"], "error_max_turns")
        self.assertEqual(ctx.exception.attempts, 1)

    def test_timeout_is_marked(self):
        prov = _provider(retries=0)
        boom = subprocess.TimeoutExpired(cmd=["claude"], timeout=60, output="partial")
        with mock.patch.object(providers_module.subprocess, "run", _Runs(boom)):
            with self.assertRaises(ProviderStageError) as ctx:
                prov.run(AgentRequest(role="context", prompt="p", cwd="/tmp", max_turns=24))
        self.assertTrue(ctx.exception.timed_out)
        self.assertIn("timed out", str(ctx.exception))

    def test_generic_failure_message_is_unchanged(self):
        prov = _provider(retries=0)
        with mock.patch.object(providers_module.subprocess, "run", _Runs((1, "not json"))):
            with self.assertRaises(RuntimeError) as ctx:
                prov.run(AgentRequest(role="plan", prompt="p", cwd="/tmp", max_turns=30))
        self.assertIsInstance(ctx.exception, ProviderStageError)
        self.assertIn("agent worker failed role='plan' rc=1", str(ctx.exception))


class FailedStageRecordTests(unittest.TestCase):
    def test_exhausted_retries_are_recorded_then_reraised(self):
        exc = ProviderStageError(
            f"agent worker role='test_author' failed on a transient provider error 3 time(s): {TRANSIENT} token {FAKE_SECRET}",
            telemetry={"num_turns": 21, "total_cost_usd": 0.57, "input_tokens": 89403},
            attempts=3, transient_errors=(TRANSIENT + " " + FAKE_SECRET,) * 3,
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Raising(exc))
            with self.assertRaises(ProviderStageError) as ctx:
                _run_stage(rt, paths)
            self.assertIs(ctx.exception, exc)
            record = _record(paths, "test_author")
            self.assertEqual(record["outcome"], "failed")
            self.assertEqual(record["error_class"], "ProviderStageError")
            self.assertEqual(record["attempts"], 3)
            self.assertEqual(len(record["transient_errors"]), 3)
            self.assertEqual(record["num_turns"], 21)
            self.assertAlmostEqual(record["total_cost_usd"], 0.57)
            self.assertGreaterEqual(record["wall_seconds"], 0)
            serialized = json.dumps(record)
            self.assertNotIn(FAKE_SECRET, serialized)
            self.assertIn(TRANSIENT, record["error"])
            rows = _rows(paths)
            self.assertEqual([(r["kind"], r["name"], r["outcome"]) for r in rows],
                             [("agent", "test_author", "failed")])
            self.assertEqual(rows[0]["num_turns"], 21)
            self.assertNotIn("timed_out", rows[0])
            self.assertFalse((paths.transcripts / "agent-test_author.log").exists())

    def test_timeout_is_recorded_with_the_flag(self):
        exc = ProviderStageError("agent worker timed out role='context' after 1200.0s", timed_out=True)
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Raising(exc))
            with self.assertRaises(ProviderStageError):
                _run_stage(rt, paths, role="context")
            record = _record(paths, "context")
            self.assertTrue(record["timed_out"])
            self.assertEqual(record["attempts"], 1)
            self.assertTrue(_rows(paths)[0]["timed_out"])

    def test_any_exception_class_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Raising(ValueError("worker did not return parseable JSON")))
            with self.assertRaises(ValueError):
                _run_stage(rt, paths, role="review-spec")
            record = _record(paths, "review-spec")
            self.assertEqual(record["error_class"], "ValueError")
            self.assertEqual(record["outcome"], "failed")

    def test_success_path_unchanged(self):
        class _Ok:
            def run(self, request, **_):
                return AgentResult(provider_id="fake", model="fake", content="worker text",
                                   num_turns=4, duration_ms=99, cost_usd=0.5)

        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Ok())
            _run_stage(rt, paths, role="conformance")
            record = _record(paths, "conformance")
            # A returned stage says so, the same way a failed one does (D-050).
            self.assertEqual(record["outcome"], "ok")
            self.assertEqual(record["num_turns"], 4)
            self.assertEqual(_rows(paths)[0]["outcome"], "ok")


if __name__ == "__main__":
    unittest.main()
