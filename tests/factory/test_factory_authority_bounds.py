"""Every model call is bounded in tools, turns and dollars, and a judge can touch nothing (D-052).

Validation run 33960088633 spent 934 seconds in the blinded code holdout against a 350-second
budget. Nothing bounded its spend: the validation authorities (the code holdout, the
architecture holdout and the three pre-code certifiers) built their `AgentRequest` with a turn
cap and nothing else, so the provider rendered no `--max-budget-usd` and no `--tools` policy of
the kernel's choosing. The build workers carried all three from `worker_policy`; the judges,
which are the part of the factory that decides whether a PR merges, carried one.

These tests pin the bound at three depths. By AST, every `AgentRequest(...)` the kernel
constructs names `allowed_tools`, `max_turns` and `max_budget_usd`, each taken from the policy
function of the same name for the request's own role, and no kernel module constructs one
anywhere else. Through the rehearsal harness, the real `validate_pr` hands each authority to the
provider with exactly the policy's values, and the CLI provider renders them as flags. And the
one funnel a model is run through, `_agent_stage`, refuses a request that arrives without any
of the three before a process starts, because an unbounded request is a kernel defect and not a
stage that failed.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.agents import AgentRequest, AgentResult  # noqa: E402
from factory_kernel.config import ProviderConfig  # noqa: E402
from factory_kernel.providers import ClaudeCliProvider  # noqa: E402
from factory_kernel.runtime import STAGE_TIMINGS, KernelRuntime, RunPaths  # noqa: E402
from factory_kernel.triage import TriageEngine  # noqa: E402
from factory_kernel.worker_policy import (  # noqa: E402
    AUTHORITY_ROLES,
    JUDGE_TOOLS,
    READ_TOOLS,
    ROLE_MAX_BUDGET_USD,
    ROLE_MAX_TURNS,
    ROLE_TOOLS,
    allowed_tools,
    max_budget_usd,
    max_turns,
)
from harness import rehearsal  # noqa: E402
from harness.rehearsal import Scenario, rehearse  # noqa: E402

KERNEL = ROOT / "factory_kernel"
BOUNDS = ("allowed_tools", "max_turns", "max_budget_usd")
# Every place the kernel constructs a request, with the number of sites each file holds. A
# refactor that adds a site must add it here; a file that is not listed may construct none.
REQUEST_SITES = {"runtime.py": 4, "worker_runtime.py": 1, "triage.py": 1}
# Anything a judge could use to change the tree or run a process. Not a tool list the policy
# reads; the assertion is that a judge's surface contains none of it, whatever the policy says.
MUTATING_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Task", "Agent"})


def _request_calls(source: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentRequest"
    ]


def _envelope(result: dict) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(result),
            "num_turns": 2,
            "duration_ms": 40,
            "total_cost_usd": 0.01,
            "session_id": "s",
            "usage": {"input_tokens": 3, "output_tokens": 3},
        }
    )


class _Provider:
    """Records what reaches it and returns the fixed verdict."""

    def __init__(self, structured: dict | None = None) -> None:
        self.structured = structured or {"version": "1.0", "findings": [], "verdict": "pass"}
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest, **_kwargs: object) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            provider_id="fake",
            model="fake-model",
            content=json.dumps(self.structured),
            structured_output=self.structured,
            num_turns=1,
        )


def _runtime(tmp: Path, provider: object, *, cls: type = KernelRuntime) -> KernelRuntime:
    rt = object.__new__(cls)
    rt.repo_root = ROOT
    rt.provider = provider
    rt.config = mock.Mock()
    rt.config.provider.model = "fake-model"
    prompt = tmp / "prompt.md"
    prompt.write_text("judge prompt\n", encoding="utf-8")
    rt.config.prompt_path = lambda role, cwd: prompt
    rt.check_stop = lambda: None
    return rt


class EveryRequestIsBoundedAtConstructionTests(unittest.TestCase):
    """Source-shape pin: no `AgentRequest(...)` in the kernel is built without all three bounds,
    and each bound is the policy's own value for the request's own role."""

    def test_every_request_site_takes_every_bound_from_the_policy(self):
        for filename, expected_sites in REQUEST_SITES.items():
            source = (KERNEL / filename).read_text(encoding="utf-8")
            calls = _request_calls(source)
            with self.subTest(filename):
                self.assertEqual(
                    len(calls), expected_sites,
                    f"{filename} constructs {len(calls)} AgentRequest(s); REQUEST_SITES says "
                    f"{expected_sites}. A new site must be listed and bounded.",
                )
            for call in calls:
                where = f"{filename}:{call.lineno}"
                with self.subTest(where):
                    self.assertFalse(
                        [kw for kw in call.keywords if kw.arg is None],
                        f"{where} splats its keywords, which hides whether the bounds are present",
                    )
                    keywords = {kw.arg: kw.value for kw in call.keywords}
                    self.assertIn("role", keywords, f"{where} names no role")
                    for bound in BOUNDS:
                        self.assertIn(bound, keywords, f"{where} constructs a request without {bound}")
                        value = keywords[bound]
                        self.assertIsInstance(
                            value, ast.Call, f"{where} sets {bound} to a literal, not the policy"
                        )
                        self.assertIsInstance(value.func, ast.Name, f"{where} {bound}")
                        self.assertEqual(
                            value.func.id, bound,
                            f"{where} sets {bound} from {ast.unparse(value)} rather than "
                            f"worker_policy.{bound}(role)",
                        )
                        self.assertEqual(
                            (len(value.args), value.keywords), (1, []),
                            f"{where} calls {bound} with something other than the role",
                        )
                        self.assertEqual(
                            ast.dump(value.args[0]), ast.dump(keywords["role"]),
                            f"{where} bounds {bound} for a different role than it requests",
                        )

    def test_the_bounds_are_the_policy_functions_not_local_shadows(self):
        for filename in REQUEST_SITES:
            source = (KERNEL / filename).read_text(encoding="utf-8")
            imported: set[str] = set()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.ImportFrom) and node.module == "worker_policy":
                    imported.update(alias.asname or alias.name for alias in node.names)
            with self.subTest(filename):
                self.assertTrue(
                    set(BOUNDS) <= imported,
                    f"{filename} imports {sorted(imported)} from worker_policy; needs {BOUNDS}",
                )

    def test_no_other_kernel_module_constructs_a_request(self):
        for path in sorted(KERNEL.glob("*.py")):
            if path.name in REQUEST_SITES:
                continue
            with self.subTest(path.name):
                self.assertEqual(
                    _request_calls(path.read_text(encoding="utf-8")), [],
                    f"{path.name} constructs an AgentRequest outside the listed, bounded sites",
                )


class JudgePolicyTests(unittest.TestCase):
    """The policy says what a judge may touch: nothing."""

    def test_every_authority_has_a_row_in_every_table(self):
        for role in sorted(AUTHORITY_ROLES):
            with self.subTest(role):
                self.assertIn(role, ROLE_TOOLS)
                self.assertIn(role, ROLE_MAX_TURNS)
                self.assertIn(role, ROLE_MAX_BUDGET_USD)

    def test_a_judge_has_no_tools(self):
        self.assertEqual(JUDGE_TOOLS, ())
        for role in sorted(AUTHORITY_ROLES | {"triage"}):
            with self.subTest(role):
                tools = allowed_tools(role)
                self.assertEqual(tools, JUDGE_TOOLS)
                self.assertFalse(set(tools) & MUTATING_TOOLS, f"{role} could change the tree")
                self.assertTrue(set(tools) <= set(READ_TOOLS), f"{role} exceeds read-only")

    def test_an_authority_is_bounded_like_triage_not_like_a_builder(self):
        """Ten turns, no tools, one prompt: the same shape as triage, so the same cap."""
        for role in sorted(AUTHORITY_ROLES):
            with self.subTest(role):
                self.assertEqual(max_budget_usd(role), max_budget_usd("triage"))
                self.assertLessEqual(max_budget_usd(role), min(ROLE_MAX_BUDGET_USD.values()))
                self.assertLessEqual(max_turns(role), max_turns("triage"))


class AuthoritiesReachTheProviderBoundedTests(unittest.TestCase):
    """The real `validate_pr`, through the rehearsal fakes, hands the provider each authority
    with exactly the policy's tools, turns and dollars."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.requests: dict[str, list[AgentRequest]] = {}
        original = rehearsal.FakeProvider.run

        def spy(self, request, **kwargs):
            cls.requests.setdefault(request.role, []).append(request)
            return original(self, request, **kwargs)

        with (
            mock.patch.object(rehearsal.FakeProvider, "run", spy),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cls.trace = rehearse(Scenario("happy"))

    def test_the_rehearsal_merged_so_every_authority_below_actually_ran(self):
        self.assertEqual(self.trace.outcome, "returned", self.trace.error)
        self.assertTrue(self.trace.happened("merge_squash"))
        self.assertEqual(set(self.requests), AUTHORITY_ROLES)

    def test_each_authority_request_carries_the_policy_bounds(self):
        for role in sorted(AUTHORITY_ROLES):
            with self.subTest(role):
                (request,) = self.requests[role]
                self.assertEqual(request.allowed_tools, allowed_tools(role))
                self.assertEqual(request.max_turns, max_turns(role))
                self.assertEqual(request.max_budget_usd, max_budget_usd(role))
                self.assertIsNotNone(request.max_budget_usd)

    def test_no_authority_can_write_or_run_anything(self):
        for role in sorted(AUTHORITY_ROLES):
            with self.subTest(role):
                (request,) = self.requests[role]
                self.assertEqual(request.allowed_tools, ())
                self.assertFalse(set(request.allowed_tools or ()) & MUTATING_TOOLS)
                self.assertEqual(dict(request.environment), {})
                self.assertNotEqual(Path(request.cwd).resolve(), ROOT)


class TheProviderRendersTheBoundsTests(unittest.TestCase):
    """What the CLI is actually told, for the requests the runtime actually builds."""

    def _argv_for(self, run_stage) -> list[str]:
        verdict = {"version": "1.0", "findings": [], "verdict": "pass", "certifies": "design"}
        provider = ClaudeCliProvider(ProviderConfig(
            provider_id="claude-cli", binary="claude", model="m", timeout_seconds=60,
        ))
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), provider)
            with (
                mock.patch("factory_kernel.providers.subprocess.run") as run,
                mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                run.return_value = mock.Mock(returncode=0, stdout=_envelope(verdict), stderr="")
                run_stage(rt, paths)
            return list(run.call_args.args[0])

    def _assert_bounded(self, argv: list[str], role: str) -> None:
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], str(max_turns(role)))
        self.assertEqual(
            argv[argv.index("--max-budget-usd") + 1], f"{max_budget_usd(role):g}"
        )
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")

    def test_the_code_holdout_runs_with_no_tools_ten_turns_and_two_dollars(self):
        argv = self._argv_for(
            lambda rt, paths: rt._run_blinded_holdout(paths, {"contract": {}, "diff": ""})
        )
        self._assert_bounded(argv, "holdout")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "10")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "2")

    def test_a_certifier_runs_with_no_tools_and_its_own_caps(self):
        argv = self._argv_for(
            lambda rt, paths: rt._run_precode_certifier(
                paths, claim_id="design", role="design-certifier", inputs={"design": {}}
            )
        )
        self._assert_bounded(argv, "design-certifier")


class TheFunnelRefusesAnUnboundedRequestTests(unittest.TestCase):
    """`_agent_stage` is the one place a model is run; a request missing a bound stops there,
    before a process starts and before any record is written."""

    def _refuse(self, request: AgentRequest, missing: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Provider()
            rt = _runtime(Path(tmp), provider)
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError) as ctx:
                    rt._agent_stage(paths, request)
            self.assertIn("unbounded", str(ctx.exception))
            self.assertIn(missing, str(ctx.exception))
            self.assertIn(request.role, str(ctx.exception))
            self.assertEqual(provider.requests, [], "the provider was called anyway")
            self.assertFalse((paths.transcripts / f"agent-{request.role}.json").exists())
            self.assertFalse((paths.transcripts / STAGE_TIMINGS).exists())

    def test_a_request_without_a_budget_never_reaches_the_provider(self):
        self._refuse(
            AgentRequest(role="holdout", prompt="p", cwd="/tmp", allowed_tools=(), max_turns=10),
            "max_budget_usd",
        )

    def test_a_request_without_a_tool_policy_never_reaches_the_provider(self):
        self._refuse(
            AgentRequest(role="design-certifier", prompt="p", cwd="/tmp", max_turns=10,
                         max_budget_usd=2.0),
            "allowed_tools",
        )

    def test_a_request_without_a_turn_cap_never_reaches_the_provider(self):
        self._refuse(
            AgentRequest(role="implement", prompt="p", cwd="/tmp", allowed_tools=("Read",),
                         max_budget_usd=12.0),
            "max_turns",
        )

    def test_the_refusal_names_every_missing_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), _Provider())
            with self.assertRaises(RuntimeError) as ctx:
                rt._agent_stage(paths, AgentRequest(role="holdout", prompt="p", cwd="/tmp"))
        for bound in BOUNDS:
            self.assertIn(bound, str(ctx.exception))

    def test_a_bounded_request_is_run_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Provider()
            rt = _runtime(Path(tmp), provider)
            request = AgentRequest(
                role="holdout", prompt="p", cwd="/tmp",
                allowed_tools=allowed_tools("holdout"), max_turns=max_turns("holdout"),
                max_budget_usd=max_budget_usd("holdout"),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent_stage(paths, request)
            self.assertEqual(provider.requests, [request])
            self.assertTrue((paths.transcripts / "agent-holdout.json").exists())


class BuildSideRequestsAreBoundedTests(unittest.TestCase):
    """Both `_agent` paths (the base runtime's and the worker runtime's override) carry all
    three bounds for the role they run."""

    def test_the_base_agent_path_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Provider()
            rt = _runtime(Path(tmp), provider)
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent("conformance", ROOT, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
            (request,) = provider.requests
            self.assertEqual(request.allowed_tools, allowed_tools("conformance"))
            self.assertEqual(request.max_turns, max_turns("conformance"))
            self.assertEqual(request.max_budget_usd, max_budget_usd("conformance"))

    def test_the_worker_agent_path_is_bounded(self):
        from factory_kernel.worker_runtime import WorkerControlledRuntime

        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Provider()
            rt = _runtime(Path(tmp), provider, cls=WorkerControlledRuntime)
            rt._assert_clean = lambda cwd: None
            # Git state after the worker is another authority's concern; the mutation copy is
            # not a repository, and the request is built before either check runs.
            rt._refuse_literal_artifacts_dir = lambda cwd: None
            with (
                mock.patch("factory_kernel.worker_runtime.method_block", return_value=""),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rt._agent("conformance", ROOT, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
            (request,) = provider.requests
            self.assertEqual(request.allowed_tools, allowed_tools("conformance"))
            self.assertEqual(request.max_turns, max_turns("conformance"))
            self.assertEqual(request.max_budget_usd, max_budget_usd("conformance"))


class _TriageGitHub:
    def __init__(self, issues: list[dict]) -> None:
        self.issues = issues
        self.labels_added: list[tuple[int, str]] = []
        self.comments: list[tuple[int, str]] = []

    def json(self, args):
        if args[:2] == ["repo", "view"]:
            return {"owner": {"login": "owner"}}
        if args[:2] == ["issue", "list"]:
            return self.issues
        if args[:2] == ["pr", "list"]:
            return []
        raise AssertionError(args)

    def add_issue_label(self, number, label):
        self.labels_added.append((number, label))

    def remove_issue_label(self, number, label):
        pass

    def comment_issue(self, number, body):
        self.comments.append((number, body))

    def run(self, args):
        self.closed = args


class TriageIsBoundedTests(unittest.TestCase):
    """Triage is a model call too. It has no run directory and does not pass through
    `_agent_stage`, so its bounds are pinned at the provider it calls."""

    def test_the_triage_request_carries_the_policy_bounds(self):
        issue = {
            "number": 7, "title": "A bug", "body": "Steps to reproduce",
            "author": {"login": "owner"}, "createdAt": "2099-01-01T01:00:00Z",
            "state": "OPEN", "labels": [],
        }
        decisions = {"version": "1.0", "decisions": [{
            "issue_number": 7, "verdict": "reject", "priority": "low",
            "classification": "bug", "reason": "underspecified", "duplicate_of": None,
        }]}
        provider = _Provider(decisions)
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "triage.md"
            prompt.write_text("triage prompt\n", encoding="utf-8")
            # Triage reads the constitution from its repo root; a stand-in keeps the test
            # runnable in the mutation copy, which carries neither file.
            (Path(tmp) / "MISSION.md").write_text("mission\n", encoding="utf-8")
            (Path(tmp) / "FACTORY_RULES.md").write_text("rules\n", encoding="utf-8")
            runtime = SimpleNamespace(
                github=_TriageGitHub([issue]),
                config=SimpleNamespace(
                    repository="owner/repo",
                    labels={"rate_limited": "factory:rate-limited", "rejected": "factory:rejected",
                            "accepted": "factory:accepted", "needs_human": "factory:needs-human"},
                    provider=SimpleNamespace(model="fake-model"),
                    prompt_path=lambda role, cwd: prompt,
                ),
                repo_root=Path(tmp),
                provider=provider,
                check_stop=lambda: None,
            )
            # The frontier filter is a deterministic authority with its own suite; here the
            # question is only what the model call carries.
            with (
                mock.patch.object(TriageEngine, "_frontier", lambda self, c: c),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                TriageEngine(runtime).run_once()
        (request,) = provider.requests
        self.assertEqual(request.role, "triage")
        self.assertEqual(request.allowed_tools, allowed_tools("triage"))
        self.assertEqual(request.allowed_tools, ())
        self.assertEqual(request.max_turns, max_turns("triage"))
        self.assertEqual(request.max_budget_usd, max_budget_usd("triage"))


if __name__ == "__main__":
    unittest.main()
