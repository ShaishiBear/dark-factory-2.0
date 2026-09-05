"""Transient provider errors are retried per stage; terminal ones never are (D-031).

The tenth canary defect: a `test_author` worker returned `API Error: stream closed before
completion` after 12 seconds of API time and the whole build was refused. A dropped stream is
not a verdict about the worker. The provider re-launches the stage for an explicit list of
transient patterns, restores a mutation role's worktree before doing so, counts every attempt in
the telemetry, and refuses everything else exactly as before.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import providers as providers_module  # noqa: E402
from factory_kernel.agents import AgentRequest, AgentResult  # noqa: E402
from factory_kernel.config import ProviderConfig, TRANSIENT_RETRIES_MAX, load_config  # noqa: E402
from factory_kernel.providers import (  # noqa: E402
    TRANSIENT_BACKOFF_SECONDS, TRANSIENT_ERROR_PATTERNS, ClaudeCliProvider,
    TransientProviderError, is_transient_error, unwrap_result_envelope,
)
from factory_kernel.worker_policy import REPO_MUTATION_ROLES  # noqa: E402

TRANSIENT_RESULT = "API Error: stream closed before completion"


def envelope(**overrides) -> str:
    raw = {
        "type": "result", "subtype": "success", "is_error": False, "result": "done",
        "num_turns": 3, "duration_ms": 1000, "total_cost_usd": 0.10, "session_id": "s-1",
        "usage": {"input_tokens": 50, "output_tokens": 7, "cache_read_input_tokens": 5},
    }
    raw.update(overrides)
    return json.dumps(raw) + "\n"


def transient(result: str = TRANSIENT_RESULT) -> str:
    return envelope(is_error=True, result=result, num_turns=7, duration_ms=11577,
                    total_cost_usd=0.19, usage={"input_tokens": 29801, "output_tokens": 329})


def provider(retries: int = 2) -> ClaudeCliProvider:
    return ClaudeCliProvider(ProviderConfig(
        provider_id="claude-cli", binary="claude", model="m", timeout_seconds=60,
        transient_retries=retries,
    ))


def request(role: str = "test_author") -> AgentRequest:
    return AgentRequest(role=role, prompt="p", cwd="/tmp", max_turns=30, max_budget_usd=12.0)


class Runs:
    """A fake subprocess.run that hands out canned stdouts in order and counts launches.

    An item may be a `(returncode, stdout)` pair to model the CLI exiting non-zero, which it does
    when the session ended in error while still printing its envelope on stdout.
    """

    def __init__(self, *stdouts) -> None:
        self.stdouts = list(stdouts)
        self.calls = 0

    def __call__(self, argv, **kwargs):
        self.calls += 1
        if not self.stdouts:
            raise AssertionError("provider launched more processes than the test allowed")
        item = self.stdouts.pop(0)
        rc, out = item if isinstance(item, tuple) else (0, item)
        return mock.Mock(returncode=rc, stdout=out, stderr="")


FIXTURE = ROOT / "tests" / "factory" / "fixtures" / "provider" / "run-33933101233-test-author-stream-closed.json"


class ClassificationTests(unittest.TestCase):
    def test_the_canary_error_is_transient(self):
        self.assertTrue(is_transient_error(TRANSIENT_RESULT, "success"))

    def test_every_listed_pattern_is_transient_case_insensitively(self):
        for pattern in TRANSIENT_ERROR_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertTrue(is_transient_error(f"API Error: {pattern.upper()} while streaming", "success"))

    def test_caps_budgets_and_unknown_errors_are_terminal(self):
        for detail, subtype in (
            ("Reached max turns", "error_max_turns"),
            ("Reached max budget", "error_max_budget"),
            ("There's an issue with the selected model", "success"),
            ("did not return parseable JSON", "success"),
            ("", "success"),
        ):
            with self.subTest(detail=detail, subtype=subtype):
                self.assertFalse(is_transient_error(detail, subtype))

    def test_an_error_subtype_is_terminal_even_with_a_transient_word_in_it(self):
        """The CLI's own stop reasons are verdicts; a 503 mentioned inside one is not a retry."""
        self.assertFalse(is_transient_error("stopped after 503 tokens", "error_max_turns"))

    def test_unwrap_raises_the_typed_error_for_transient_and_plain_for_terminal(self):
        with self.assertRaises(TransientProviderError) as ctx:
            unwrap_result_envelope(transient(), role="test_author")
        self.assertEqual(ctx.exception.envelope.num_turns, 7)
        with self.assertRaises(RuntimeError) as ctx2:
            unwrap_result_envelope(envelope(is_error=True, subtype="error_max_turns", result="cap"), role="x")
        self.assertNotIsInstance(ctx2.exception, TransientProviderError)


class RetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sleeps: list[float] = []
        patcher = mock.patch.object(providers_module, "_sleep", side_effect=self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_transient_failure_then_success_is_one_retry_with_summed_telemetry(self):
        runs = Runs(transient(), envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            result = provider().run(request())
        self.assertEqual(runs.calls, 2)
        self.assertEqual(result.content, "done")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(result.transient_errors), 1)
        self.assertIn("stream closed", result.transient_errors[0])
        self.assertEqual(result.num_turns, 7 + 3)
        self.assertEqual(result.input_tokens, 29801 + 50)
        self.assertAlmostEqual(result.cost_usd, 0.19 + 0.10)
        self.assertEqual(self.sleeps, [TRANSIENT_BACKOFF_SECONDS[0]])

    def test_transient_failures_beyond_the_retry_budget_are_refused_with_the_last_error(self):
        runs = Runs(transient(), transient("overloaded"), transient("socket hang up"), envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            with self.assertRaises(RuntimeError) as ctx:
                provider(retries=2).run(request())
        self.assertEqual(runs.calls, 3, "1 + transient_retries processes, never more")
        self.assertIn("socket hang up", str(ctx.exception))
        self.assertIn("3 time(s)", str(ctx.exception))
        self.assertEqual(self.sleeps, list(TRANSIENT_BACKOFF_SECONDS[:2]))

    def test_a_terminal_envelope_is_never_retried(self):
        for stdout in (
            envelope(is_error=True, subtype="error_max_turns", result="Reached max turns"),
            envelope(is_error=True, subtype="error_max_budget", result="Reached max budget"),
            envelope(is_error=True, result="There's an issue with the selected model"),
        ):
            with self.subTest(stdout=stdout[:60]):
                runs = Runs(stdout, envelope())
                with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
                    with self.assertRaises(RuntimeError):
                        provider().run(request())
                self.assertEqual(runs.calls, 1)
        self.assertEqual(self.sleeps, [])

    def test_zero_retries_refuses_the_first_transient_error(self):
        runs = Runs(transient(), envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            with self.assertRaises(RuntimeError):
                provider(retries=0).run(request())
        self.assertEqual(runs.calls, 1)

    def test_before_retry_is_called_with_the_attempt_number_before_the_relaunch(self):
        order: list[str] = []
        runs = Runs(transient(), envelope())

        def launch(argv, **kwargs):
            order.append("launch")
            return runs(argv, **kwargs)

        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=launch):
            provider().run(request(), before_retry=lambda attempt: order.append(f"restore:{attempt}"))
        self.assertEqual(order, ["launch", "restore:2", "launch"])

    def test_a_successful_first_attempt_reports_one_attempt_and_no_errors(self):
        runs = Runs(envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            result = provider().run(request())
        self.assertEqual((result.attempts, result.transient_errors), (1, ()))


class NonZeroExitTests(unittest.TestCase):
    """The eighteenth canary defect (D-040): the CLI exits non-zero on an error session and still
    prints its envelope; the generic failure used to fire before that envelope was classified,
    so the retry built for exactly this never ran. Run 33933101233's envelope is the fixture."""

    def setUp(self) -> None:
        patcher = mock.patch.object(providers_module, "_sleep", side_effect=lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_run_33933101233_envelope_is_transient(self):
        text = FIXTURE.read_text(encoding="utf-8")
        raw = json.loads(text)
        self.assertTrue(raw["is_error"])
        self.assertEqual(raw["subtype"], "success")
        self.assertIn("stream closed before completion", raw["result"])
        err = providers_module._transient_from_stdout(text, role="test_author")
        self.assertIsInstance(err, TransientProviderError)
        self.assertEqual(err.envelope.num_turns, 6)

    def test_a_nonzero_exit_with_a_transient_envelope_is_retried(self):
        runs = Runs((1, FIXTURE.read_text(encoding="utf-8")), envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            result = provider().run(request())
        self.assertEqual(runs.calls, 2)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.content, "done")
        self.assertIn("stream closed", result.transient_errors[0])
        self.assertEqual(result.num_turns, 6 + 3)

    def test_a_nonzero_exit_with_a_terminal_envelope_is_not_retried(self):
        runs = Runs((1, envelope(is_error=True, subtype="error_max_turns", result="Reached max turns")), envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            with self.assertRaises(RuntimeError) as ctx:
                provider().run(request())
        self.assertNotIsInstance(ctx.exception, TransientProviderError)
        self.assertEqual(runs.calls, 1)
        self.assertIn("rc=1", str(ctx.exception))

    def test_a_nonzero_exit_with_a_transient_word_in_a_cap_envelope_is_not_retried(self):
        runs = Runs((1, envelope(is_error=True, subtype="error_max_budget", result="503 budget")), envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            with self.assertRaises(RuntimeError):
                provider().run(request())
        self.assertEqual(runs.calls, 1)

    def test_a_nonzero_exit_with_non_envelope_stdout_keeps_the_generic_error(self):
        runs = Runs((1, "Segmentation fault: stream closed before completion"), envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            with self.assertRaises(RuntimeError) as ctx:
                provider().run(request())
        self.assertNotIsInstance(ctx.exception, TransientProviderError)
        self.assertEqual(runs.calls, 1)
        self.assertIn("agent worker failed role='test_author' rc=1", str(ctx.exception))

    def test_transient_nonzero_exits_beyond_the_budget_are_refused(self):
        fx = FIXTURE.read_text(encoding="utf-8")
        runs = Runs((1, fx), (1, fx), (1, fx), envelope())
        with mock.patch("factory_kernel.providers.subprocess.run", side_effect=runs):
            with self.assertRaises(RuntimeError) as ctx:
                provider().run(request())
        self.assertEqual(runs.calls, 3)
        self.assertIn("3 time(s)", str(ctx.exception))


class ConfigTests(unittest.TestCase):
    def test_transient_retries_is_bounded(self):
        raw = json.loads((ROOT / ".factory" / "kernel.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["provider"]["transient_retries"], 2)
        self.assertEqual(TRANSIENT_RETRIES_MAX, 3)
        with tempfile.TemporaryDirectory() as tmp:
            for bad in (-1, 4, True, "2", 2.0):
                with self.subTest(bad=bad):
                    raw["provider"]["transient_retries"] = bad
                    path = Path(tmp) / "kernel.json"
                    path.write_text(json.dumps(raw), encoding="utf-8")
                    with mock.patch.dict("os.environ", {"FACTORY_WORKDIR": tmp}):
                        with self.assertRaises(ValueError):
                            load_config(path)
            for ok in (0, 3):
                raw["provider"]["transient_retries"] = ok
                path = Path(tmp) / "kernel.json"
                path.write_text(json.dumps(raw), encoding="utf-8")
                with mock.patch.dict("os.environ", {"FACTORY_WORKDIR": tmp}):
                    self.assertEqual(load_config(path).provider.transient_retries, ok)

    def test_default_is_two(self):
        self.assertEqual(ProviderConfig(provider_id="claude-cli", binary="c", model="m", timeout_seconds=1).transient_retries, 2)


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


class WorktreeRestoreTests(unittest.TestCase):
    """The kernel, not the provider, restores a mutation role's checkout before a retry."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-retry-")
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "core.autocrlf", "false")
        (self.repo / "app.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _runtime(self):
        from factory_kernel.worker_runtime import WorkerControlledRuntime

        rt = WorkerControlledRuntime.__new__(WorkerControlledRuntime)
        rt._git = lambda *args, cwd=None: git(cwd or self.repo, *args)  # type: ignore[attr-defined]
        rt._assert_clean = lambda cwd: (  # type: ignore[attr-defined]
            (_ for _ in ()).throw(RuntimeError("dirty")) if git(cwd, "status", "--porcelain") else None
        )
        return rt

    def test_a_dirtied_worktree_is_restored_for_every_mutation_role(self):
        rt = self._runtime()
        for role in sorted(REPO_MUTATION_ROLES):
            with self.subTest(role=role):
                (self.repo / "app.py").write_text("x = 2\n", encoding="utf-8")
                (self.repo / "stray.py").write_text("y\n", encoding="utf-8")
                rt._restore_worktree_before_retry(role, self.repo, 2)
                self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), "x = 1\n")
                self.assertFalse((self.repo / "stray.py").exists())
                self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_a_non_mutation_role_that_dirtied_the_tree_is_refused_not_cleaned(self):
        rt = self._runtime()
        (self.repo / "stray.py").write_text("y\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            rt._restore_worktree_before_retry("review-spec", self.repo, 2)
        self.assertTrue((self.repo / "stray.py").exists(), "no cleanup for a role that may not write")

    def test_agent_wires_the_restore_as_the_provider_retry_hook(self):
        source = (ROOT / "factory_kernel" / "worker_runtime.py").read_text(encoding="utf-8")
        self.assertIn("before_retry=lambda attempt: self._restore_worktree_before_retry(role, cwd, attempt)", source)


class TelemetryTests(unittest.TestCase):
    def test_record_agent_writes_attempts_and_transient_errors(self):
        from factory_kernel.runtime import KernelRuntime, RunPaths

        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run-1")
            rt = KernelRuntime.__new__(KernelRuntime)
            result = AgentResult(provider_id="p", model="m", content="t", num_turns=10,
                                 attempts=2, transient_errors=("API Error: stream closed before completion",))
            rt._record_agent(paths, "test_author", result, started=0.0)
            telemetry = json.loads((paths.transcripts / "agent-test_author.json").read_text(encoding="utf-8"))
        self.assertEqual(telemetry["attempts"], 2)
        self.assertEqual(telemetry["transient_errors"], ["API Error: stream closed before completion"])


if __name__ == "__main__":
    unittest.main()
