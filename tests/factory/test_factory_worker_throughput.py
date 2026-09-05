"""Workers are bounded, briefed, measured and cached (D-020).

The first canary spent ~12 minutes per model stage. Prompts were small; the time went into an
unbounded agentic loop with no turn cap, a context worker that received only a hash and had to
rediscover the task from disk, prompts that ordered 87 KB of constitution read up front, cold
dependency caches, and no per-stage timing to show any of it. None of the fixes below touches
an authority, an isolation flag, the tool policy, the blinding or an evidence step.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import providers, runtime as runtime_module  # noqa: E402
from factory_kernel.agents import AgentRequest, AgentResult  # noqa: E402
from factory_kernel.config import ProviderConfig  # noqa: E402
from factory_kernel.providers import ClaudeCliProvider, CliRun, unwrap_result_envelope  # noqa: E402
from factory_kernel.runtime import KernelRuntime, RunPaths, STAGE_TIMINGS  # noqa: E402
from factory_kernel.worker_policy import (  # noqa: E402
    OBSERVED_SECONDS_PER_TURN_CEILING, ROLE_MAX_BUDGET_USD, ROLE_MAX_TURNS, ROLE_TOOLS,
    assert_caps_fit_timeout, max_budget_usd, max_turns, stage_timeout_seconds,
)

RUNTIME = ROOT / "factory_kernel" / "runtime.py"
PROMPTS = ROOT / ".factory" / "prompts"
WORKFLOWS = ROOT / ".github" / "workflows"
WORKER = WORKFLOWS / "dark-factory-worker.yml"
CI = WORKFLOWS / "dark-factory-ci.yml"
REGRESSION = WORKFLOWS / "dark-factory-main-regression.yml"


def envelope(**overrides) -> str:
    raw = {
        "type": "result", "subtype": "success", "is_error": False, "result": "done",
        "num_turns": 3, "duration_ms": 1234, "total_cost_usd": 0.01, "session_id": "s-1",
        "usage": {"input_tokens": 50, "output_tokens": 7},
    }
    raw.update(overrides)
    return json.dumps(raw) + "\n"


def provider() -> ClaudeCliProvider:
    return ClaudeCliProvider(ProviderConfig(
        provider_id="claude-cli", binary="claude", model="sonnet", timeout_seconds=60,
    ))


class TurnCapPolicyTests(unittest.TestCase):
    def test_every_role_has_a_positive_integer_cap(self):
        self.assertEqual(set(ROLE_MAX_TURNS), set(ROLE_TOOLS))
        for role, cap in ROLE_MAX_TURNS.items():
            self.assertIsInstance(cap, int, role)
            self.assertNotIsInstance(cap, bool, role)
            self.assertGreater(cap, 0, role)
            self.assertEqual(max_turns(role), cap)

    def test_unknown_role_has_no_cap(self):
        with self.assertRaises(ValueError):
            max_turns("not-a-role")

    def test_tool_less_authorities_are_capped_tightest(self):
        for role, tools in ROLE_TOOLS.items():
            if not tools:
                self.assertLessEqual(ROLE_MAX_TURNS[role], 20, role)

    def test_request_refuses_a_non_positive_cap(self):
        with self.assertRaises(ValueError):
            AgentRequest(role="implement", prompt="x", cwd="/tmp", max_turns=0)
        with self.assertRaises(ValueError):
            AgentRequest(role="implement", prompt="x", cwd="/tmp", max_turns=True)


class CapsFitTimeoutTests(unittest.TestCase):
    """Worker run 33908589032 measured 33.85 s per turn; the then 1200 s timeout fired near 35
    turns. A cap above that is unreachable, and a timeout records no envelope and no telemetry
    (D-025). Each role now has its own wall (`stage_timeout_seconds`) and the configured
    timeout is the maximum every wall must fit under (D-054)."""

    def test_every_cap_fits_under_the_checked_in_timeout(self):
        kernel = json.loads((ROOT / ".factory" / "kernel.json").read_text(encoding="utf-8"))
        timeout = int(kernel["provider"]["timeout_seconds"])
        assert_caps_fit_timeout(timeout)
        for role, cap in ROLE_MAX_TURNS.items():
            self.assertLessEqual(cap * OBSERVED_SECONDS_PER_TURN_CEILING, timeout, role)
            self.assertLessEqual(stage_timeout_seconds(role), timeout, role)

    def test_a_cap_the_timeout_would_beat_is_refused(self):
        with mock.patch.dict(ROLE_MAX_TURNS, {"implement": 120}):
            with self.assertRaisesRegex(ValueError, "implement"):
                assert_caps_fit_timeout(2700)

    def test_ceiling_is_at_least_the_measured_rate(self):
        # 40.4 s/turn was measured on `investigate` in build run 33987381035 (D-054).
        self.assertGreaterEqual(OBSERVED_SECONDS_PER_TURN_CEILING, 41)

    def test_every_role_has_a_positive_budget(self):
        self.assertEqual(set(ROLE_MAX_BUDGET_USD), set(ROLE_TOOLS))
        for role in ROLE_TOOLS:
            self.assertGreater(max_budget_usd(role), 0, role)
        with self.assertRaises(ValueError):
            max_budget_usd("not-a-role")
        with self.assertRaises(ValueError):
            AgentRequest(role="implement", prompt="x", cwd="/tmp", max_budget_usd=0)


class ProviderArgvTests(unittest.TestCase):
    @mock.patch("factory_kernel.providers._stream_cli")
    def test_argv_carries_the_budget_cap(self, run):
        run.return_value = CliRun(returncode=0, stdout=envelope(), stderr="")
        provider().run(AgentRequest(role="implement", prompt="p", cwd="/tmp", max_turns=30, max_budget_usd=12.0))
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "12")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "30")

    @mock.patch("factory_kernel.providers._stream_cli")
    def test_cache_fields_are_kept_and_default_to_zero(self, run):
        run.return_value = CliRun(returncode=0, stdout=envelope(
            usage={"input_tokens": 50, "output_tokens": 7,
                   "cache_creation_input_tokens": 11, "cache_read_input_tokens": 900}), stderr="")
        result = provider().run(AgentRequest(role="implement", prompt="p", cwd="/tmp"))
        self.assertEqual(result.cache_creation_input_tokens, 11)
        self.assertEqual(result.cache_read_input_tokens, 900)
        env = unwrap_result_envelope(envelope(), role="implement")
        self.assertEqual(env.cache_creation_input_tokens, 0)
        self.assertEqual(env.cache_read_input_tokens, 0)
        self.assertIn("cache_read_input_tokens", env.telemetry())

    @mock.patch("factory_kernel.providers._stream_cli")
    def test_timeout_raises_a_failed_stage_naming_the_role(self, run):
        run.return_value = CliRun(
            returncode=None, stdout='{"type":"system","subtype":"init","note":"partial text so far"}\n',
            stderr="", timed_out=True, elapsed=60.0,
        )
        with self.assertRaisesRegex(RuntimeError, r"timed out role='context' after 60.0s \(timeout_seconds=60, max_turns=24.*partial text") as ctx:
            provider().run(AgentRequest(role="context", prompt="p", cwd="/tmp", max_turns=24))
        self.assertTrue(ctx.exception.timed_out)
        self.assertEqual(ctx.exception.telemetry["events_seen"], 1)

    def test_budget_stopped_envelope_fails_the_stage(self):
        with self.assertRaisesRegex(RuntimeError, "error_max_budget"):
            unwrap_result_envelope(envelope(subtype="error_max_budget_usd", result=""), role="implement")


    @mock.patch("factory_kernel.providers._stream_cli")
    def test_argv_carries_the_cap_and_structured_output(self, run):
        run.return_value = CliRun(returncode=0, stdout=envelope(), stderr="")
        result = provider().run(AgentRequest(role="implement", prompt="p", cwd="/tmp", max_turns=120))
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--max-turns") + 1], "120")
        # Read as it runs (D-054); print mode refuses stream-json without --verbose.
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", argv)
        # Isolation flags are untouched.
        for flag in ("--bare", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(result.content, "done")
        self.assertEqual(result.num_turns, 3)
        self.assertEqual(result.duration_ms, 1234)
        self.assertEqual(result.cost_usd, 0.01)
        self.assertEqual(result.input_tokens, 50)
        self.assertEqual(result.output_tokens, 7)
        self.assertEqual(result.session_id, "s-1")

    @mock.patch("factory_kernel.providers._stream_cli")
    def test_no_cap_means_no_flag(self, run):
        run.return_value = CliRun(returncode=0, stdout=envelope(), stderr="")
        provider().run(AgentRequest(role="implement", prompt="p", cwd="/tmp"))
        self.assertNotIn("--max-turns", run.call_args.args[0])

    @mock.patch("factory_kernel.providers._stream_cli")
    def test_structured_roles_parse_the_unwrapped_text(self, run):
        run.return_value = CliRun(
            returncode=0, stdout=envelope(result='{"version":"1.0","verdict":"pass"}'), stderr="",
        )
        result = provider().run(AgentRequest(
            role="holdout", prompt="judge", cwd="/tmp", structured_schema={"type": "object"},
        ))
        self.assertEqual(result.structured_output, {"version": "1.0", "verdict": "pass"})


class ResultEnvelopeTests(unittest.TestCase):
    def test_success_envelope_is_unwrapped(self):
        env = unwrap_result_envelope(envelope(), role="implement")
        self.assertEqual(env.content, "done")
        self.assertEqual(env.num_turns, 3)
        self.assertEqual(env.telemetry()["duration_ms"], 1234)

    def test_error_envelope_fails_the_stage(self):
        with self.assertRaisesRegex(RuntimeError, "ended in error"):
            unwrap_result_envelope(envelope(is_error=True, result="boom"), role="implement")

    def test_turn_cap_exceeded_fails_the_stage(self):
        with self.assertRaisesRegex(RuntimeError, "error_max_turns"):
            unwrap_result_envelope(
                envelope(subtype="error_max_turns", result=""), role="implement",
            )

    def test_non_envelope_output_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "result envelope"):
            unwrap_result_envelope("done\n", role="implement")
        with self.assertRaisesRegex(RuntimeError, "result envelope"):
            unwrap_result_envelope('{"version":"1.0"}', role="implement")

    @mock.patch("factory_kernel.providers._stream_cli")
    def test_provider_refuses_an_error_envelope_even_at_rc_zero(self, run):
        run.return_value = CliRun(returncode=0, stdout=envelope(is_error=True), stderr="")
        with self.assertRaisesRegex(RuntimeError, "ended in error"):
            provider().run(AgentRequest(role="review-spec", prompt="p", cwd="/tmp"))


class _FakeProvider:
    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest, **_kwargs: object) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            provider_id="fake", model="fake", content="worker text", session_id="s",
            input_tokens=1, output_tokens=2, cost_usd=0.5, num_turns=4, duration_ms=99,
        )


class TimingAndTelemetryTests(unittest.TestCase):
    def _runtime(self, tmp: Path):
        from factory_kernel.worker_runtime import WorkerControlledRuntime

        rt = object.__new__(WorkerControlledRuntime)
        rt.repo_root = ROOT
        rt.provider = _FakeProvider()
        rt.config = mock.Mock()
        rt.config.provider.model = "fake"
        prompt = tmp / "prompt.md"
        prompt.write_text("role prompt\n", encoding="utf-8")
        rt.config.prompt_path = lambda role, cwd: prompt
        rt.check_stop = lambda: None
        rt._assert_clean = lambda cwd: None
        return rt

    def test_worker_stage_writes_text_telemetry_and_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = self._runtime(Path(tmp))
            with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""):
                rt._agent("conformance", ROOT, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
            request = rt.provider.requests[0]
            self.assertEqual(request.max_turns, ROLE_MAX_TURNS["conformance"])
            self.assertEqual((paths.transcripts / "agent-conformance.log").read_text(encoding="utf-8"), "worker text\n")
            telemetry = json.loads((paths.transcripts / "agent-conformance.json").read_text(encoding="utf-8"))
            self.assertEqual(telemetry["num_turns"], 4)
            self.assertEqual(telemetry["duration_ms"], 99)
            self.assertEqual(telemetry["total_cost_usd"], 0.5)
            self.assertGreaterEqual(telemetry["wall_seconds"], 0)
            rows = [json.loads(l) for l in (paths.transcripts / STAGE_TIMINGS).read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(r["kind"], r["name"]) for r in rows], [("agent", "conformance")])
            self.assertEqual(rows[0]["num_turns"], 4)
            self.assertIn("started_at", rows[0])

    def test_exec_with_a_transcript_appends_a_timing_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            transcript = paths.transcripts / "contract-gate.log"
            with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
                rt._exec([sys.executable, "-c", "print('gate ok')"], cwd=Path(tmp), transcript=transcript)
            self.assertIn("gate ok", transcript.read_text(encoding="utf-8"))
            rows = [json.loads(l) for l in (paths.transcripts / STAGE_TIMINGS).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[-1]["kind"], "exec")
            self.assertEqual(rows[-1]["name"], "contract-gate")
            self.assertEqual(rows[-1]["rc"], 0)

    def test_exec_without_a_transcript_records_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = object.__new__(KernelRuntime)
            with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
                rt._exec([sys.executable, "-c", "pass"], cwd=Path(tmp))
            self.assertEqual(list(Path(tmp).rglob(STAGE_TIMINGS)), [])


class WorkerBriefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
        cls.build = next(
            n for n in ast.walk(cls.tree) if isinstance(n, ast.FunctionDef) and n.name == "build_issue"
        )

    def _agent_calls(self) -> dict[str, ast.Call]:
        found = {}
        for node in ast.walk(self.build):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_agent" and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                found[node.args[0].value] = node
        return found

    def test_post_contract_workers_receive_the_contract_not_only_its_hash(self):
        calls = self._agent_calls()
        for role in ("context", "architecture", "test_author"):
            call = calls[role]
            context = next((k.value for k in call.keywords if k.arg == "context"), None)
            self.assertIsNotNone(context, f"{role} worker gets no context")
            # D-030: the test author's brief is `_worker_brief(...) + _deferred_symptom_brief(...)`;
            # the left operand must still be the brief.
            if isinstance(context, ast.BinOp):
                self.assertIsInstance(context.op, ast.Add, role)
                context = context.left
            self.assertIsInstance(context, ast.Call, f"{role} context must be built by _worker_brief")
            self.assertIsInstance(context.func, ast.Attribute)
            self.assertEqual(context.func.attr, "_worker_brief", role)
        # The architecture governor additionally sees the compiled context and design.
        arch = calls["architecture"]
        context = next(k.value for k in arch.keywords if k.arg == "context")
        self.assertTrue(any(k.arg == "include_design" for k in context.keywords))

    def test_brief_reads_the_validated_contract_file(self):
        brief = next(
            n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name == "_worker_brief"
        )
        self.assertIn("task-contract.json", ast.unparse(brief))

    def test_brief_content_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            contract = {"version": "2.0", "summary": "export snippet", "behaviors": []}
            (paths.artifacts / "task-contract.json").write_text(json.dumps(contract), encoding="utf-8")
            (paths.artifacts / "context.json").write_text(json.dumps({"files": ["a.py"]}), encoding="utf-8")
            (paths.artifacts / "design.json").write_text(
                json.dumps({"planned_files": ["a.py"], "allowed_new_files": [], "seams": [{"name": "s"}]}),
                encoding="utf-8",
            )
            rt = object.__new__(KernelRuntime)
            brief = rt._worker_brief(paths, contract_hash="ab" * 32, issue_context="ORIGINAL ISSUE: x")
            self.assertTrue(brief.startswith("Validated contract sha256: " + "ab" * 32))
            self.assertIn("ORIGINAL ISSUE: x", brief)
            self.assertIn('"summary": "export snippet"', brief)
            self.assertNotIn("planned_files", brief)
            with_design = rt._worker_brief(
                paths, contract_hash="ab" * 32, issue_context="i", include_design=True,
            )
            self.assertIn('"planned_files": ["a.py"]', with_design)
            self.assertIn('"context_files": ["a.py"]', with_design)
            self.assertNotIn("holdout", with_design.lower())

    def test_every_agent_request_in_the_runtime_is_capped(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "AgentRequest":
                self.assertIn("max_turns", {k.arg for k in node.keywords}, ast.unparse(node)[:80])


class PromptNarrowingTests(unittest.TestCase):
    STALE = "Read the original issue supplied in the invocation context, repository code, CLAUDE.md, FACTORY_RULES.md"

    @unittest.skipUnless((PROMPTS / "investigate.md").exists(), "repo-shaped copy without prompts")
    def test_no_prompt_orders_the_constitution_read_end_to_end(self):
        for name in ("investigate.md", "plan.md", "review-standards.md", "context.md"):
            text = (PROMPTS / name).read_text(encoding="utf-8")
            self.assertNotIn(self.STALE, text, name)
            self.assertIn("search before reading", text.lower(), name)
        self.assertNotIn("Review the merge-base diff, `CLAUDE.md`", (PROMPTS / "review-standards.md").read_text(encoding="utf-8"))
        self.assertNotIn("recent history", (PROMPTS / "context.md").read_text(encoding="utf-8"))

    @unittest.skipUnless((PROMPTS / "investigate.md").exists(), "repo-shaped copy without prompts")
    def test_prohibitions_and_output_contracts_are_unchanged(self):
        investigate = (PROMPTS / "investigate.md").read_text(encoding="utf-8")
        self.assertIn("Do not implement the fix and do not run commands.", investigate)
        self.assertIn("$ARTIFACTS_DIR/repro.json", investigate)
        self.assertIn("Do not implement.", (PROMPTS / "plan.md").read_text(encoding="utf-8"))
        standards = (PROMPTS / "review-standards.md").read_text(encoding="utf-8")
        self.assertIn("Do not edit code.", standards)
        self.assertIn('"axis":"standards"', standards)
        context = (PROMPTS / "context.md").read_text(encoding="utf-8")
        self.assertIn("planned_files", context)
        self.assertIn("Do not edit product code.", context)


class WorkflowCacheAndUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.ci = CI.read_text(encoding="utf-8")
        cls.regression = REGRESSION.read_text(encoding="utf-8")

    def test_dependency_caches_are_enabled_and_pinned_everywhere(self):
        for name, text in (("worker", self.worker), ("ci", self.ci), ("regression", self.regression)):
            uv = text.split("astral-sh/setup-uv@", 1)[1].split("- ", 1)[0]
            self.assertIn("enable-cache: true", uv, name)
            self.assertIn("cache-dependency-glob: app/backend/uv.lock", uv, name)
            self.assertIn("uses: actions/cache@5a3ec84eff668545956fd18022155c47e93e2684", text, name)
            self.assertIn("path: ~/.bun/install/cache", text, name)
            self.assertIn("hashFiles('app/frontend/bun.lock')", text, name)
            self.assertNotIn("actions/cache@v4", text, name)
        # The cache never replaces the lockfile as the resolution.
        self.assertIn("uv sync --frozen", self.ci)
        self.assertIn("bun install --frozen-lockfile", self.ci)

    def test_worker_uploads_transcripts_for_observability(self):
        step = self.worker.split("- name: Upload run transcripts and artifacts (observability)", 1)
        self.assertEqual(len(step), 2, "upload step missing")
        step = step[1]
        self.assertTrue(step.lstrip().startswith("if: always()"), "must upload on failure too")
        self.assertIn("uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", step)
        self.assertIn("retention-days: 7", step)
        self.assertIn("transcripts/agent-*.log", step)
        self.assertIn("transcripts/agent-*.json", step)
        self.assertIn(f"transcripts/{STAGE_TIMINGS}", step)
        self.assertIn("artifacts/*.json", step)
        for excluded in ("evidence.log", "merge-*.log", "post-merge.log", "security.log"):
            self.assertIn(f"!${{{{ runner.temp }}}}/dark-factory/runs/*/transcripts/{excluded}", step, excluded)
        self.assertGreater(self.worker.index("Dispatch exactly one factory action"), 0)
        self.assertLess(self.worker.index("Dispatch exactly one factory action"), self.worker.index("Upload run transcripts"))

    def test_preflight_probe_proves_the_cap_flag_on_the_pinned_cli(self):
        probe = self.worker.split("Prove the worker's model route with the pinned CLI", 1)[1].split("- name:", 1)[0]
        self.assertIn("--max-turns 2", probe)
        self.assertIn("--max-budget-usd 1", probe)

    def test_investigate_prompt_states_rules_flat_and_forbids_verifying_them_in_source(self):
        text = (PROMPTS / "investigate.md").read_text(encoding="utf-8")
        self.assertIn("do not read factory_kernel/ or .factory/decisions.md to verify them", text)
        self.assertIn("use Grep to locate a section", text)
        self.assertNotIn("The kernel executes this command deterministically", text)
        self.assertNotIn("Consult the specific sections of CLAUDE.md and FACTORY_RULES.md", text)
        for shape in ("repro.json", "repro-deferred.json", "expect_failure_containing", "expected_symptom"):
            self.assertIn(shape, text)


if __name__ == "__main__":
    unittest.main()
