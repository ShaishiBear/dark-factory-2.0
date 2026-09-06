"""A builder reads only its product tree, and its draft deadline records (D-057, D-066).

Four builds of issue #103 died in `test_author`. The last (run 34002520477) made 46 Read calls
and no Write or Edit in 31 turns: it read kernel source, the harness, biome and tsconfig, and
set out to "verify the kernel's deferred-repro check", then hit its 30-turn cap
(`error_max_turns`, 1925 s, $4.54). Issue #49's test author (run 33999901008) wrote its first
file at turn ~5 of 15. Issue #103's seventh build (34024234313), the first on MiniMax M3, was
killed by the 0.6 deadline at turn 18 after 17 in-scope Reads at one per turn, and its
eleventh (34042566216) by the 0.8 deadline at turn 25 after 22, both before a first write and
both on a model that wrote a complete test file in other runs. One bound and one signal,
pinned here.

The read scope (the bound): every tool-bearing role carries a `PathScope` from
`worker_policy.ROLE_PATH_SCOPE` (the product tree under `app/`, `docs/`, the root docs, and the
run's artifacts; the trust root denied), the provider renders it as the CLI's
`Read(...)`/`Edit(...)` allow and deny rules instead of bare tool names, and the `_agent_stage`
funnel refuses a repository-mutation request that carries no scope.

The draft deadline (the signal): for `test_author`, `implement` and `repair`, the provider's
stream reader notes a turn past `ceil(cap * 0.8)` that begins with no Write/Edit tool_use seen
and keeps reading. Nothing is killed and nothing is refused: the stage runs on to its turn cap
or its wall, `draft_deadline_missed=true reads=N` reaches its record, its timing row and its
`FACTORY_STAGE` line, and D-065's path decides — the gates on a draft, `no_draft_at_cap` /
`no_spec_at_cap` on nothing (`test_factory_cap_ends_the_loop.py` owns that path; this file
pins the entry to it). The prompts state the scope and the deadline, the deadline rendered
from the policy and worded as advice.

The end-to-end cases reuse the fake CLI of `test_factory_stream_timeouts.py`.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
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

import factory_read_scope_probe as probe  # noqa: E402
from test_factory_red_evidence_and_stop import FakeGitHub, FakeWorktree  # noqa: E402
from test_factory_static_gate import git, repo  # noqa: E402
from test_factory_stream_timeouts import (  # noqa: E402
    _FakeCliCase,
    _runtime,
    assistant_event,
    healthy_steps,
    init_event,
    lines,
    provider_for,
    result_event,
    tool_result_event,
)

from factory_kernel import providers as providers_module  # noqa: E402
from factory_kernel.agents import AgentRequest, AgentResult, PathScope  # noqa: E402
from factory_kernel.config import ProviderConfig, load_config  # noqa: E402
from factory_kernel.prompt_render import KERNEL_PLACEHOLDERS, PromptRenderError  # noqa: E402
from factory_kernel.providers import (  # noqa: E402
    CAP_SUBTYPE,
    FILES_READ_CAP,
    ClaudeCliProvider,
    CliRun,
    DraftWatch,
    ProviderStageError,
    path_rules,
)
from factory_kernel.runtime import (  # noqa: E402
    STAGE_TIMINGS,
    KernelRuntime,
    NeedsHuman,
    RunPaths,
    stage_line,
)
from factory_kernel.worker_policy import (  # noqa: E402
    ARCHITECTURE_POLICY_PATH,
    ARCHITECTURE_SCOPE,
    AUTHORITY_ROLES,
    DRAFT_DEADLINE_FRACTION,
    DRAFTING_SCOPE,
    JUDGE_SCOPE,
    MUTATION_SCOPE,
    REPO_MUTATION_ROLES,
    ROLE_MAX_TURNS,
    ROLE_PATH_SCOPE,
    ROLE_TOOLS,
    TRUST_ROOT_DENY_PATHS,
    WRITE_TOOLS,
    allowed_tools,
    draft_deadline_turn,
    effort,
    max_turns,
    path_scope,
    stage_timeout_seconds,
)
from factory_kernel.worker_runtime import (  # noqa: E402
    NO_DRAFT_AT_CAP,
    WorkerControlledRuntime,
)

PROMPT_DIR = ROOT / ".factory" / "prompts"
WORKER_WORKFLOW = ROOT / ".github" / "workflows" / "dark-factory-worker.yml"
JUDGE_ROLES = AUTHORITY_ROLES | {"triage"}
TOOL_ROLES = sorted(set(ROLE_TOOLS) - JUDGE_ROLES)
# The roots the brief names, every one of which must be denied to every tool-bearing role.
TRUST_ROOTS = ("factory_kernel/**", "harness/**", "scripts/**", "tests/factory/**", ".github/**")
FACTORY_PROTECTED = (
    ".factory/kernel.json",
    ".factory/prompts/**",
    ".factory/methods/**",
    ".factory/holdout/**",
)
SCOPE_SENTENCE = (
    "You can read only the product tree under app/ and your run's artifacts; the kernel's own "
    "code, harness and workflows are not readable and not your concern."
)
PROBE_LINE = re.compile(
    r"^FACTORY_PREFLIGHT_READ_SCOPE_PROBE model=(?P<model>\S+) "
    r"denied_outside_scope=(?P<denied>true|false) "
    r"attempted_outside_scope=(?P<attempted>true|false) "
    r"read_inside_scope=(?P<inside>true|false) read_artifacts=(?P<artifacts>true|false) "
    r"grep_denied_outside_scope=(?P<grep>true|false) glob_outside_scope=(?P<glob>true|false) "
    r"tools=(?P<tools>\S+) tools_missing=(?P<missing>\S+) "
    r"events=\d+(?: error=(?P<error>\S+))?$"
)


# --- stream shapes with tool calls ------------------------------------------------------------


def tool_use_event(message_id: str, name: str, params: dict, *, session_id: str = "s-1") -> dict:
    """An `assistant` event carrying one tool_use block, the shape the CLI prints for a tool
    call (possibly as its own event with the same message id as the turn's text block)."""
    event = assistant_event(message_id, "", session_id=session_id)
    event["message"]["content"] = [
        {"type": "tool_use", "id": f"toolu_{message_id}", "name": name, "input": params},
    ]
    return event


def tool_result(text: str, *, is_error: bool = False) -> dict:
    event = tool_result_event()
    event["message"]["content"] = [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": text, "is_error": is_error},
    ]
    return event


# What the CLI prints when `--max-turns` ended the session, and the number of turns its
# envelope reports: one more than the turns the stream showed, as run 34033360798 did.
CAP_TEXT = "Reached max turns (30)"


def reading_steps(
    count: int,
    *,
    write_turn: int | None = None,
    reads_per_turn: int = 1,
    pace: float = 0.01,
    cap: bool = False,
) -> list[dict]:
    """`count` turns; each turn is a text event, `reads_per_turn` Read tool_use events sharing
    the turn's message id, and a tool result. At `write_turn` the tool call is a Write. With
    `cap`, the session ends the way the CLI ends one it stopped at `--max-turns`: an
    `error_max_turns` envelope and exit 1, which for a mutation role is the loop ending and
    not a failed stage (D-065)."""
    steps: list[dict] = [{"emit": init_event()}]
    for n in range(1, count + 1):
        steps.append({"sleep": pace, "emit": assistant_event(f"msg_{n}", f"turn {n}")})
        if write_turn is not None and n == write_turn:
            steps.append(
                {"emit": tool_use_event(f"msg_{n}", "Write", {"file_path": "app/t.test.ts"})}
            )
        else:
            for r in range(reads_per_turn):
                steps.append(
                    {
                        "emit": tool_use_event(
                            f"msg_{n}", "Read", {"file_path": f"factory_kernel/f{n}_{r}.py"}
                        )
                    }
                )
        steps.append({"emit": tool_result("ok")})
    if cap:
        steps.append(
            {
                "emit": result_event(
                    subtype=CAP_SUBTYPE, is_error=True, result=CAP_TEXT, num_turns=count + 1
                )
            }
        )
        steps.append({"exit": 1})
    else:
        steps.append({"emit": result_event(num_turns=count)})
    return steps


def request(role: str = "test_author", **overrides) -> AgentRequest:
    fields = dict(
        role=role,
        prompt="p",
        cwd=tempfile.gettempdir(),
        allowed_tools=allowed_tools(role),
        max_turns=max_turns(role),
        max_budget_usd=12.0,
        timeout_seconds=stage_timeout_seconds(role),
        effort=effort(role),
        path_scope=path_scope(role),
    )
    fields.update(overrides)
    return AgentRequest(**fields)


# --- the policy table ------------------------------------------------------------------------


class PathScopePolicyTests(unittest.TestCase):
    def test_every_role_has_a_scope_and_no_other_role_does(self):
        self.assertEqual(set(ROLE_PATH_SCOPE), set(ROLE_TOOLS))
        for role in ROLE_TOOLS:
            with self.subTest(role):
                self.assertIsInstance(path_scope(role), PathScope)
        with self.assertRaisesRegex(ValueError, "no path scope"):
            path_scope("nobody")

    def test_mutation_roles_may_write_the_product_tree_and_nothing_else(self):
        for role in sorted(REPO_MUTATION_ROLES):
            with self.subTest(role):
                scope = path_scope(role)
                self.assertIs(scope, MUTATION_SCOPE)
                self.assertIn("app/**", scope.write)
                self.assertIn("app/**", scope.read)
                for pattern in scope.write:
                    self.assertFalse(
                        pattern.startswith((".factory", "factory_kernel", "harness", "scripts")),
                        pattern,
                    )
                for name in ("CLAUDE.md", "MISSION.md", "FACTORY_RULES.md"):
                    self.assertIn(name, scope.read)
                    self.assertNotIn(name, scope.write, "governance is readable, never writable")

    def test_every_tool_bearing_role_reads_the_product_tree_and_is_denied_the_trust_root(self):
        for role in TOOL_ROLES:
            with self.subTest(role):
                scope = path_scope(role)
                self.assertFalse(scope.empty)
                for pattern in ("app/**", "docs/**", "README.md", "CLAUDE.md"):
                    self.assertIn(pattern, scope.read)
                self.assertEqual(scope.deny, TRUST_ROOT_DENY_PATHS)
                for root in TRUST_ROOTS + FACTORY_PROTECTED:
                    self.assertIn(root, scope.deny, f"{role} may read {root}")

    def test_drafting_roles_write_nothing_in_the_tree(self):
        for role in ("plan", "investigate", "contract", "context", "review-spec"):
            with self.subTest(role):
                self.assertIs(path_scope(role), DRAFTING_SCOPE)
                self.assertEqual(path_scope(role).write, ())

    def test_a_judge_has_nothing_to_scope(self):
        self.assertTrue(JUDGE_SCOPE.empty)
        for role in sorted(JUDGE_ROLES):
            with self.subTest(role):
                self.assertIs(path_scope(role), JUDGE_SCOPE)
                self.assertEqual(allowed_tools(role), ())

    def test_the_architecture_policy_is_the_one_documented_exception(self):
        """The governor, the conformance authority and the standards reviewer open with
        `.factory/architecture.json`; only they read it, and no deny rule covers it."""
        readers = {role for role in ROLE_TOOLS if ARCHITECTURE_POLICY_PATH in path_scope(role).read}
        self.assertEqual(readers, {"architecture", "conformance", "review-standards"})
        for role in sorted(readers):
            self.assertIs(path_scope(role), ARCHITECTURE_SCOPE)
        self.assertNotIn(".factory/**", TRUST_ROOT_DENY_PATHS)
        self.assertNotIn(ARCHITECTURE_POLICY_PATH, TRUST_ROOT_DENY_PATHS)
        for pattern in TRUST_ROOT_DENY_PATHS:
            self.assertFalse(
                ARCHITECTURE_POLICY_PATH.startswith(pattern.replace("/**", "/")),
                f"deny pattern {pattern} would override the architecture roles' allow",
            )
        for role in TOOL_ROLES:
            if role not in readers:
                self.assertNotIn(ARCHITECTURE_POLICY_PATH, path_scope(role).read, role)

    def test_a_scope_pattern_is_relative_to_the_worktree(self):
        with self.assertRaises(ValueError):
            PathScope(read=("/etc/**",))
        with self.assertRaises(ValueError):
            PathScope(deny=("C:/x/**",))
        with self.assertRaises(ValueError):
            PathScope(write=("",))
        with self.assertRaises(ValueError):
            AgentRequest(role="implement", prompt="p", cwd="/tmp", path_scope="app/**")


# --- the provider renders the scope -----------------------------------------------------------


def _provider(binary: str = "claude") -> ClaudeCliProvider:
    return ClaudeCliProvider(
        ProviderConfig(provider_id="claude-cli", binary=binary, model="m", timeout_seconds=60)
    )


def _flag(argv: list[str], name: str) -> list[str]:
    return argv[argv.index(name) + 1].split(",")


class ScopeOnArgvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="dark-factory-scope-argv-")
        self.addCleanup(self.tmp_dir.cleanup)
        self.artifacts = Path(self.tmp_dir.name) / "artifacts"
        self.artifacts.mkdir()

    def argv(self, role: str, **overrides) -> list[str]:
        return _provider().argv_for(
            request(role, environment={"ARTIFACTS_DIR": str(self.artifacts)}, **overrides)
        )

    def test_a_mutation_role_is_told_paths_not_bare_tools(self):
        argv = self.argv("test_author")
        allow = _flag(argv, "--allowedTools")
        deny = _flag(argv, "--disallowedTools")
        self.assertEqual(argv[argv.index("--tools") + 1], ",".join(WRITE_TOOLS))
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        for rule in ("Read(./app/**)", "Read(./docs/**)", "Read(./CLAUDE.md)", "Edit(./app/**)"):
            self.assertIn(rule, allow)
        for bare in WRITE_TOOLS:
            self.assertNotIn(bare, allow, f"a bare {bare} would make every path rule moot")
        self.assertNotIn("Edit(./CLAUDE.md)", allow)
        for root in TRUST_ROOTS:
            self.assertIn(f"Read(./{root})", deny)
            self.assertIn(f"Edit(./{root})", deny)
        self.assertNotIn("Read(./app/**)", deny)
        self.assertEqual(argv[argv.index("--add-dir") + 1], str(self.artifacts))

    def test_the_artifacts_directory_is_an_absolute_rule_for_reads_and_writes(self):
        argv = self.argv("implement")
        allow = _flag(argv, "--allowedTools")
        absolute = providers_module._absolute_pattern(self.artifacts)
        self.assertTrue(absolute.startswith("//"), absolute)
        self.assertTrue(absolute.endswith("/**"), absolute)
        self.assertNotIn("\\", absolute)
        self.assertIn(f"Read({absolute})", allow)
        self.assertIn(f"Edit({absolute})", allow)

    def test_a_drafting_role_carries_no_edit_allow_but_the_artifacts_dir(self):
        argv = self.argv("review-spec")
        allow = _flag(argv, "--allowedTools")
        absolute = providers_module._absolute_pattern(self.artifacts)
        self.assertIn("Read(./app/**)", allow)
        self.assertIn(f"Edit({absolute})", allow, "artifacts are written with Write/Edit")
        self.assertNotIn("Edit(./app/**)", allow)
        self.assertIn("Read(./factory_kernel/**)", _flag(argv, "--disallowedTools"))

    def test_only_read_and_edit_rules_are_rendered(self):
        """The CLI consults `Read(...)` and `Edit(...)` only; a `Write(...)` or `Glob(...)`
        path rule is accepted and never applied."""
        argv = self.argv("test_author")
        rules = _flag(argv, "--allowedTools") + _flag(argv, "--disallowedTools")
        self.assertTrue(rules)
        for rule in rules:
            self.assertRegex(rule, r"^(Read|Edit)\(\./|^(Read|Edit)\(//")

    def test_a_scope_with_a_read_only_tool_list_renders_no_edit_rules(self):
        allow, deny = path_rules(MUTATION_SCOPE, ("Read", "Grep"), artifacts=self.artifacts)
        self.assertTrue(all(rule.startswith("Read(") for rule in allow + deny))
        allow, deny = path_rules(JUDGE_SCOPE, (), artifacts=self.artifacts)
        self.assertEqual((allow, deny), ([], []))

    def test_an_unscoped_request_renders_bare_tool_names_as_before(self):
        argv = self.argv("test_author", path_scope=None)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], ",".join(WRITE_TOOLS))
        self.assertNotIn("--disallowedTools", argv)

    def test_a_judge_renders_nothing(self):
        argv = _provider().argv_for(request("holdout"))
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", argv)
        self.assertNotIn("--disallowedTools", argv)

    def test_run_launches_exactly_the_rendered_argv(self):
        provider = _provider()
        req = request("test_author", environment={"ARTIFACTS_DIR": str(self.artifacts)})
        with mock.patch.object(providers_module, "_stream_cli") as run:
            run.return_value = CliRun(returncode=0, stdout=lines(init_event(), result_event()))
            provider.run(req)
        self.assertEqual(list(run.call_args.args[0]), provider.argv_for(req))


# --- the funnel and the build-side request -----------------------------------------------------


class _Recording:
    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest, **kwargs) -> AgentResult:
        self.requests.append(request)
        return AgentResult(provider_id="fake", model="fake", content="text", num_turns=1)


class FunnelTests(unittest.TestCase):
    def test_a_scopeless_mutation_role_request_never_reaches_the_provider(self):
        for role in sorted(REPO_MUTATION_ROLES):
            with self.subTest(role), tempfile.TemporaryDirectory() as tmp:
                paths = RunPaths.create(Path(tmp), "run")
                provider = _Recording()
                rt = _runtime(Path(tmp), provider)
                with self.assertRaises(RuntimeError) as ctx:
                    rt._agent_stage(paths, request(role, path_scope=None))
                self.assertIn("unscoped", str(ctx.exception))
                self.assertIn(role, str(ctx.exception))
                self.assertEqual(provider.requests, [])
                self.assertFalse((paths.transcripts / f"agent-{role}.json").exists())
                self.assertFalse((paths.transcripts / STAGE_TIMINGS).exists())

    def test_a_scoped_mutation_request_and_an_unscoped_judge_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Recording()
            rt = _runtime(Path(tmp), provider)
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent_stage(paths, request("implement"))
                rt._agent_stage(paths, request("holdout", path_scope=None))
            self.assertEqual([r.role for r in provider.requests], ["implement", "holdout"])


def _worker_runtime(tmp: Path, provider, prompt_text: str) -> WorkerControlledRuntime:
    rt = object.__new__(WorkerControlledRuntime)
    rt.repo_root = ROOT
    rt.provider = provider
    rt.config = mock.Mock()
    rt.config.provider.model = "m"
    prompt = tmp / "prompt.md"
    prompt.write_text(prompt_text, encoding="utf-8")
    rt.config.prompt_path = lambda role, cwd: prompt
    rt.check_stop = lambda: None
    rt._assert_clean = lambda cwd: None
    rt._refuse_literal_artifacts_dir = lambda cwd: None
    # The stage's aftermath (static gate, kernel commit) is another authority's concern.
    rt._static_gate_or_retry = lambda *args, **kwargs: True
    return rt


class BuildSideRequestTests(unittest.TestCase):
    def _agent(self, role: str, prompt: str) -> AgentRequest:
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Recording()
            rt = _worker_runtime(Path(tmp), provider, prompt)
            # implement/repair read the compiled contract's issue number after the stage.
            (paths.artifacts / "task-contract.json").write_text(
                json.dumps({"issue": {"number": 49}}), encoding="utf-8"
            )
            with (
                mock.patch("factory_kernel.worker_runtime.method_block", return_value=""),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rt._agent(role, ROOT, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
            (req,) = provider.requests
            return req

    def test_the_worker_path_carries_the_roles_scope(self):
        for role in ("test_author", "implement", "repair", "plan", "conformance"):
            with self.subTest(role):
                req = self._agent(role, "prompt for $ARTIFACTS_DIR\n")
                self.assertIs(req.path_scope, path_scope(role))

    def test_the_deadline_is_rendered_into_a_mutation_roles_prompt(self):
        req = self._agent("test_author", "draft by turn $DRAFT_DEADLINE_TURN of $ARTIFACTS_DIR\n")
        self.assertIn("draft by turn 24 of", req.prompt)
        self.assertNotIn("$DRAFT_DEADLINE_TURN", req.prompt)

    def test_a_non_mutation_prompt_may_not_name_the_deadline(self):
        with self.assertRaises(PromptRenderError):
            self._agent("plan", "draft by turn $DRAFT_DEADLINE_TURN\n")
        self.assertEqual(KERNEL_PLACEHOLDERS, ("DRAFT_DEADLINE_TURN",))

    def test_the_base_agent_path_carries_the_scope_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Recording()
            rt = _runtime(Path(tmp), provider)
            prompt = Path(tmp) / "prompt.md"
            prompt.write_text("judge\n", encoding="utf-8")
            rt.config.prompt_path = lambda role, cwd: prompt
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent("conformance", ROOT, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
            (req,) = provider.requests
            self.assertIs(req.path_scope, path_scope("conformance"))


@unittest.skipUnless(PROMPT_DIR.is_dir(), "repo-shaped copy without the prompts (mutation runner)")
class PromptTextTests(unittest.TestCase):
    def _prompt(self, name: str) -> str:
        return (PROMPT_DIR / name).read_text(encoding="utf-8")

    def test_every_mutation_prompt_states_the_scope_and_the_deadline(self):
        for name in ("test-author.md", "implement.md", "repair.md"):
            with self.subTest(name):
                text = self._prompt(name)
                self.assertIn(SCOPE_SENTENCE, text)
                self.assertIn("by turn $DRAFT_DEADLINE_TURN", text)
                self.assertIn(
                    "the kernel records a stage that has written nothing by then, and your "
                    "turn cap ends the loop",
                    text,
                )
                self.assertNotIn("the kernel ends the stage if nothing is written", text)

    def test_the_test_author_is_no_longer_invited_to_verify_the_kernel(self):
        text = self._prompt("test-author.md")
        self.assertNotIn("relevant source/tests", text)
        self.assertNotIn("the kernel refuses the build after RED otherwise", text)
        self.assertIn("that is a requirement of the RED gate", text)
        # Every other constraint stands.
        for kept in (
            "Do not change production code.",
            "one per AC exactly once",
            "Do not run commands, stage files or create commits.",
            "fail the attempt rather than weakening the test",
        ):
            self.assertIn(kept, text)

    def test_no_other_prompt_names_the_deadline(self):
        for path in PROMPT_DIR.glob("*.md"):
            if path.name in ("test-author.md", "implement.md", "repair.md"):
                continue
            with self.subTest(path.name):
                self.assertNotIn("DRAFT_DEADLINE_TURN", path.read_text(encoding="utf-8"))


# --- the deadline -----------------------------------------------------------------------------


class DeadlinePolicyTests(unittest.TestCase):
    def test_the_fraction_and_the_turn(self):
        self.assertEqual(DRAFT_DEADLINE_FRACTION, 0.8)
        self.assertEqual(draft_deadline_turn("test_author"), 24)
        self.assertEqual(draft_deadline_turn("test_author"), math.ceil(30 * 0.8))
        for role in sorted(REPO_MUTATION_ROLES):
            with self.subTest(role):
                cap = ROLE_MAX_TURNS[role]
                turn = draft_deadline_turn(role)
                self.assertEqual(turn, math.ceil(cap * DRAFT_DEADLINE_FRACTION))
                self.assertLess(turn, cap, "a deadline at the cap would never fire first")
                self.assertGreater(turn, 5, "issue #49's author wrote at turn ~5")
                self.assertGreater(turn, 18, "#103's seventh build read 17 files at one a turn")

    def test_only_mutation_roles_have_one(self):
        for role in ROLE_TOOLS:
            with self.subTest(role):
                if role in REPO_MUTATION_ROLES:
                    self.assertIsNotNone(draft_deadline_turn(role))
                else:
                    self.assertIsNone(draft_deadline_turn(role))
        with self.assertRaises(ValueError):
            draft_deadline_turn("nobody")

    def test_the_requests_own_cap_is_honoured(self):
        self.assertEqual(draft_deadline_turn("implement", 10), 8)
        with self.assertRaises(ValueError):
            draft_deadline_turn("implement", 0)


class DraftWatchTests(unittest.TestCase):
    def test_turns_are_distinct_message_ids_and_the_deadline_is_only_noted(self):
        watch = DraftWatch(2)
        events = [
            assistant_event("m1", "a"),
            tool_use_event("m1", "Read", {"file_path": "app/x.py"}),
            tool_use_event("m1", "Read", {"file_path": "app/y.py"}),
            assistant_event("m2", "b"),
            tool_use_event("m2", "Glob", {"pattern": "**/*.py"}),
        ]
        for index, event in enumerate(events):
            self.assertIsNone(watch.observe(event, index=index), "observe decides nothing")
        self.assertEqual(watch.turns, 2)
        self.assertFalse(watch.deadline_missed)
        self.assertEqual(watch.reads, 2, "Glob is not a Read")
        self.assertEqual(watch.files_read, ["app/x.py", "app/y.py"])
        watch.observe(assistant_event("m3", "c"), index=5)
        self.assertEqual(watch.turns, 3)
        self.assertTrue(watch.deadline_missed, "the turn past the deadline began unwritten")

    def test_a_write_in_time_disarms_it(self):
        watch = DraftWatch(2)
        watch.observe(assistant_event("m1", "a"), index=0)
        watch.observe(tool_use_event("m2", "Edit", {"file_path": "a"}), index=1)
        self.assertEqual(watch.wrote_at_turn, 2)
        for n in range(3, 40):
            watch.observe(assistant_event(f"m{n}", "x"), index=n)
        self.assertFalse(watch.deadline_missed)

    def test_a_write_past_the_deadline_leaves_the_observation_standing(self):
        """The flag is sticky: a run that read past its deadline and then drafted says both,
        because the reads are what the cost of that stage is read from (D-066)."""
        watch = DraftWatch(2)
        for n in range(1, 4):
            watch.observe(assistant_event(f"m{n}", "x"), index=n)
        self.assertTrue(watch.deadline_missed)
        watch.observe(tool_use_event("m4", "Write", {"file_path": "app/a.test.ts"}), index=4)
        self.assertEqual(watch.wrote_at_turn, 4)
        self.assertTrue(watch.deadline_missed)

    def test_no_deadline_only_counts(self):
        watch = DraftWatch(None)
        for n in range(1, 40):
            watch.observe(tool_use_event(f"m{n}", "Read", {"file_path": "f"}), index=n)
        self.assertEqual((watch.turns, watch.reads), (39, 39))
        self.assertFalse(watch.deadline_missed)


class DeadlineFakeCliTests(_FakeCliCase):
    """The deadline through a real process: nothing is killed, everything is recorded."""

    def _stage(self, steps: list[dict], role: str = "test_author", **overrides):
        """One stage through the kernel's funnel; returns the result, record, row and line."""
        self.scenario(steps)
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), provider_for(self.binary, retries=2, timeout=60, idle=5))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = rt._agent_stage(paths, request(role, **overrides))
            record = json.loads(
                (paths.transcripts / f"agent-{role}.json").read_text(encoding="utf-8")
            )
            (row,) = [
                json.loads(line)
                for line in (paths.transcripts / STAGE_TIMINGS)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            log = (paths.transcripts / f"agent-{role}.log").read_text(encoding="utf-8")
        line = next(t for t in out.getvalue().splitlines() if t.startswith("FACTORY_STAGE "))
        return result, record, row, line, log

    def test_a_worker_that_only_reads_runs_to_its_cap_and_the_cap_rule_refuses_it(self):
        """D-057's kill is retired: the reader watches the whole run and the CLI's own turn
        cap ends it, which is D-065's path — here with nothing in the checkout, so
        `no_draft_at_cap`, not `no_draft_by_turn`."""
        self.scenario(reading_steps(30, cap=True))
        restores: list[int] = []
        result = provider_for(self.binary, retries=2, timeout=60, idle=5).run(
            request(), before_retry=restores.append
        )
        self.assertEqual(len(self.launches()), 1, "a cap is not a transient error")
        self.assertEqual(restores, [])
        self.assertEqual(result.attempts, 1)
        # Every turn was read to the end: nothing ended the process at turn 25.
        self.assertEqual(result.reads, 30)
        self.assertEqual(result.files_read[0], "factory_kernel/f1_0.py")
        self.assertEqual(result.files_read[-1], "factory_kernel/f30_0.py")
        self.assertIs(result.draft_deadline_missed, True)
        self.assertEqual(result.draft_deadline_turn, 24)
        # The turn cap is what ended the loop, and D-065's rule is what judges the draft.
        self.assertTrue(result.cap_reached)
        self.assertEqual(result.num_turns, 31)
        home = Path(tempfile.mkdtemp(dir=self.tmp))
        root = repo(home)
        self.assertEqual(git(root, "status", "--porcelain"), "", "nothing was drafted")
        paths = RunPaths.create(home, "run")
        rt = object.__new__(WorkerControlledRuntime)
        with self.assertRaises(NeedsHuman) as ctx:
            rt._require_draft_at_cap("test_author", root, paths, result)
        self.assertIn(NO_DRAFT_AT_CAP, str(ctx.exception))
        self.assertIn("reached its turn cap", str(ctx.exception))
        self.assertNotIn("no_draft_by_turn", str(ctx.exception))

    def test_a_worker_that_drafts_past_the_deadline_completes_with_it_recorded(self):
        """The run this change exists for: a first write at turn 26, seven turns inside the
        worker's own budget and two past the deadline that used to kill it."""
        result, record, row, line, log = self._stage(reading_steps(30, write_turn=26))
        self.assertEqual(result.content, "done")
        self.assertEqual(result.num_turns, 30)
        self.assertFalse(result.cap_reached)
        self.assertIs(result.draft_deadline_missed, True)
        self.assertEqual(result.reads, 29, "every turn but the write")
        self.assertEqual(record["outcome"], "ok")
        self.assertNotIn("error_class", record)
        self.assertIs(record["draft_deadline_missed"], True)
        self.assertEqual(record["draft_deadline_turn"], 24)
        self.assertEqual(record["reads"], 29)
        self.assertEqual(len(record["files_read"]), 29)
        self.assertIs(row["draft_deadline_missed"], True)
        self.assertEqual(row["reads"], 29)
        self.assertEqual(row["outcome"], "ok")
        self.assertIn(" turns=30 ", line)
        self.assertIn(" outcome=ok events=", line)
        self.assertIn(" draft_deadline_missed=true reads=29", line)
        self.assertNotIn("cap_reached=true", line)
        self.assertIn("msg_30", log, "the whole stream, because nothing cut it short")

    def test_a_worker_that_wrote_at_turn_two_is_untouched(self):
        self.scenario(reading_steps(25, write_turn=2))
        result = provider_for(self.binary, retries=0, timeout=60, idle=5).run(request())
        self.assertEqual(result.content, "done")
        self.assertEqual(result.num_turns, 25)
        self.assertEqual(len(self.launches()), 1)
        self.assertFalse(result.draft_deadline_missed)
        self.assertIsNone(result.draft_deadline_turn)

    def test_a_drafting_role_has_no_deadline(self):
        self.scenario(reading_steps(25))
        result = provider_for(self.binary, retries=0, timeout=60, idle=5).run(
            request("review-spec")
        )
        self.assertEqual(result.num_turns, 25)
        self.assertFalse(result.draft_deadline_missed)

    def test_the_deadline_follows_the_requests_own_cap(self):
        self.scenario(reading_steps(25))
        result = provider_for(self.binary, retries=0, timeout=60, idle=5).run(request(max_turns=10))
        self.assertIs(result.draft_deadline_missed, True)
        self.assertEqual(result.draft_deadline_turn, 8)
        self.assertEqual(result.num_turns, 25, "the note at turn 9 stopped nothing")

    def test_the_paths_read_are_capped_in_the_record_and_the_count_is_not(self):
        _result, record, row, _line, _log = self._stage(reading_steps(30, reads_per_turn=3))
        self.assertEqual(record["reads"], 90)
        self.assertEqual(row["reads"], 90)
        self.assertEqual(len(record["files_read"]), FILES_READ_CAP)
        self.assertEqual(FILES_READ_CAP, 40)

    def test_a_stage_that_died_at_its_wall_still_carries_what_it_read(self):
        """A process the wall or the idle clock kills after its deadline keeps the note: the
        observation rides on every way out of `_launch`, not only on the returned result."""
        # Thirty read-only turns, then silence: the deadline passes at turn 25 and the wall
        # kills the process while it is still going.
        steps = reading_steps(30)
        steps.insert(-1, {"sleep": 30})
        self.scenario(steps)
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), provider_for(self.binary, retries=0, timeout=5, idle=30))
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(ProviderStageError) as ctx,
            ):
                rt._agent_stage(paths, request())
            record = json.loads(
                (paths.transcripts / "agent-test_author.json").read_text(encoding="utf-8")
            )
        self.assertTrue(ctx.exception.timed_out)
        self.assertIs(record["draft_deadline_missed"], True)
        self.assertEqual(record["draft_deadline_turn"], 24)
        self.assertGreaterEqual(record["reads"], 24)
        self.assertIs(record["timed_out"], True)

    def test_the_stage_record_row_and_line_say_so(self):
        result, record, row, line, log = self._stage(reading_steps(30, cap=True))
        self.assertEqual(record["outcome"], "ok", "the cap ended the loop, not the stage")
        self.assertTrue(record["cap_reached"])
        self.assertIs(record["draft_deadline_missed"], True)
        self.assertEqual(record["draft_deadline_turn"], 24)
        self.assertEqual(record["reads"], 30)
        self.assertEqual(record["num_turns"], 31)
        self.assertEqual(len(record["files_read"]), 30)
        self.assertEqual(record["attempts"], 1)
        self.assertIs(row["draft_deadline_missed"], True)
        self.assertEqual(row["reads"], 30)
        self.assertIn(" turns=31 ", line)
        self.assertIn(" outcome=ok events=", line)
        self.assertIn(" cap_reached=true draft_deadline_missed=true reads=30", line)
        self.assertNotIn("timed_out=true", line)
        self.assertTrue(result.cap_reached)
        self.assertIn("msg_30", log, "the stream runs to the cap")

    def test_a_healthy_session_records_no_deadline_fields(self):
        self.scenario(healthy_steps())
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), provider_for(self.binary, timeout=60, idle=5))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rt._agent_stage(paths, request())
            record = json.loads(
                (paths.transcripts / "agent-test_author.json").read_text(encoding="utf-8")
            )
        self.assertNotIn("draft_deadline_missed", record)
        self.assertNotIn("draft_deadline_missed", out.getvalue())
        self.assertNotIn("reads=", out.getvalue())


class StageLineTests(unittest.TestCase):
    def test_the_line_shape(self):
        row = {
            "kind": "agent",
            "name": "test_author",
            "seconds": 301.2,
            "num_turns": 19,
            "outcome": "failed",
            "events_seen": 77,
            "draft_deadline_missed": True,
            "reads": 18,
            "thinking_tokens": 5,
            "effort": "medium",
        }
        self.assertEqual(
            stage_line(row),
            "FACTORY_STAGE kind=agent name=test_author seconds=301.2 turns=19 outcome=failed "
            "events=77 draft_deadline_missed=true reads=18 thinking=5 effort=medium",
        )
        self.assertNotIn(
            "draft_deadline_missed",
            stage_line({**row, "draft_deadline_missed": None, "reads": None}),
        )


# --- the needs-human comment --------------------------------------------------------------------


def _missed_record(paths: RunPaths, files: list[str] | None = None) -> None:
    """The stage record a `test_author` leaves after passing its deadline and running to its
    cap: the four fields the provider observed, beside the counts every record carries."""
    (paths.transcripts / "agent-test_author.json").write_text(
        json.dumps(
            {
                "role": "test_author",
                "record": "agent-test_author",
                "outcome": "ok",
                "num_turns": 31,
                "draft_deadline_missed": True,
                "draft_deadline_turn": 24,
                "reads": 46,
                "files_read": files
                if files is not None
                else ["factory_kernel/runtime.py", "harness/ci.py"],
            }
        ),
        encoding="utf-8",
    )


class CommentEvidenceTests(unittest.TestCase):
    def test_the_evidence_names_the_reads_and_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            _missed_record(paths)
            text = KernelRuntime._draft_deadline_evidence(paths)
        self.assertIn("draft_deadline_missed=true", text)
        self.assertIn("`test_author`", text)
        self.assertIn("46 Read call(s)", text)
        self.assertIn("by turn 24", text)
        self.assertIn("turns seen: 31", text)
        self.assertIn("recorded, not enforced", text)
        self.assertIn("ran on to its turn cap", text)
        self.assertIn("factory_kernel/runtime.py", text)
        self.assertIn("harness/ci.py", text)
        self.assertIn("Files read (first 2)", text)
        self.assertNotIn("no_draft_by_turn", text)

    def test_a_run_without_one_adds_nothing_and_the_combined_evidence_is_the_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            self.assertEqual(KernelRuntime._draft_deadline_evidence(paths), "")
            self.assertEqual(KernelRuntime._failure_evidence(paths, RuntimeError("x")), "")
            _missed_record(paths)
            self.assertEqual(
                KernelRuntime._failure_evidence(paths, RuntimeError("x")),
                KernelRuntime._draft_deadline_evidence(paths),
            )

    def test_the_evidence_is_capped_and_scrubbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            _missed_record(paths, [f"app/very/long/path/number/{n}.py" for n in range(400)])
            self.assertLessEqual(len(KernelRuntime._draft_deadline_evidence(paths)), 3000)
            # Assembled at run time: the guard scans added lines for this very shape (D-057).
            token = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789ABCD"
            _missed_record(paths, [f"app/x.py?token={token}"])
            text = KernelRuntime._draft_deadline_evidence(paths)
        self.assertNotIn(token, text)
        self.assertIn("app/x.py", text)


class BuildCommentTests(unittest.TestCase):
    """build_issue driven to its first model stage against fakes. The stage records the missed
    deadline and is refused by D-065's `no_draft_at_cap`, and the comment carries both."""

    def setUp(self) -> None:
        import dataclasses

        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-draft-deadline-build-")
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.work_root = home / "work"
        self.work_root.mkdir()
        self.worktree = home / "worktree"
        self.worktree.mkdir()
        # The checked-in work root is a POSIX path; the loader takes an absolute override.
        patcher = mock.patch.dict(os.environ, {"FACTORY_WORKDIR": str(self.work_root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        config = load_config(ROOT / ".factory" / "kernel.json")
        self.config = dataclasses.replace(
            config, runtime=dataclasses.replace(config.runtime, work_root=self.work_root)
        )

    def test_the_needs_human_comment_carries_the_reads(self):
        gh = FakeGitHub()
        rt = KernelRuntime(repo_root=ROOT, config=self.config)
        rt.github = gh  # type: ignore[assignment]
        rt.check_stop = lambda: None  # type: ignore[method-assign]
        rt._fetch_main = lambda: None  # type: ignore[method-assign]
        rt._git = lambda *args, cwd=None: "a" * 40  # type: ignore[method-assign]
        rt._prepare_worktree = lambda cwd, paths: None  # type: ignore[method-assign]
        rt._issue_frontier = lambda issue: {"version": "1.0", "issue": dict(issue), "blockers": []}  # type: ignore[method-assign]
        rt._lease_heartbeat = lambda *args, **kwargs: None  # type: ignore[method-assign]

        def agent(role, cwd, paths, *, context="", env):
            _missed_record(paths)
            raise NeedsHuman(
                "test_author reached its turn cap (31 turns, `error_max_turns`) and left no "
                f"change in the checkout (`{NO_DRAFT_AT_CAP}`): there is no draft for the "
                "gates to judge"
            )

        rt._agent = agent  # type: ignore[method-assign]
        with (
            mock.patch(
                "factory_kernel.runtime.create_detached", return_value=FakeWorktree(self.worktree)
            ),
            mock.patch("factory_kernel.runtime.remove"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(NeedsHuman),
        ):
            rt.build_issue(49)
        self.assertIn((49, "factory:needs-human"), gh.added)
        ((number, body),) = gh.comments
        self.assertEqual(number, 49)
        self.assertIn("test_author reached its turn cap (31 turns, `error_max_turns`)", body)
        self.assertIn(NO_DRAFT_AT_CAP, body)
        self.assertIn("draft_deadline_missed=true", body)
        self.assertIn("46 Read call(s)", body)
        self.assertIn("factory_kernel/runtime.py", body)


# --- the preflight probe --------------------------------------------------------------------------


class _FakeRunner:
    def __init__(self, stdout: str, rc: int = 0) -> None:
        self.stdout = stdout
        self.rc = rc
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        return subprocess.CompletedProcess(argv, self.rc, stdout=self.stdout, stderr="")


def _probe_stream(
    *, outside: str, inside: str = probe.INSIDE_SENTINEL, attempt_outside: bool = True
) -> str:
    events = [
        init_event(),
        tool_use_event("m1", "Read", {"file_path": "./" + probe.INSIDE_FILE}),
        tool_result(inside + "\n"),
    ]
    if attempt_outside:
        events += [
            tool_use_event("m2", "Read", {"file_path": "./" + probe.OUTSIDE_FILE}),
            tool_result(outside, is_error=outside != probe.OUTSIDE_SENTINEL),
        ]
    events += [
        tool_use_event("m3", "Read", {"file_path": "/tmp/artifacts/note.txt"}),
        tool_result(probe.ARTIFACT_SENTINEL + "\n"),
        assistant_event("m4", "done"),
        result_event(num_turns=4),
    ]
    return lines(*events)


class ProbeScriptTests(unittest.TestCase):
    def test_the_argv_is_the_kernels_own_for_a_scoped_test_author(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree, artifacts = probe.build_tree(Path(tmp))
            self.assertEqual(
                (worktree / probe.INSIDE_FILE).read_text(encoding="utf-8").strip(),
                probe.INSIDE_SENTINEL,
            )
            self.assertTrue((worktree / probe.OUTSIDE_FILE).is_file())
            self.assertTrue((artifacts / probe.ARTIFACT_FILE).is_file())
            argv = probe.probe_argv("claude", "z-ai/glm-5.3-flash", worktree, artifacts)
            req = probe.probe_request("z-ai/glm-5.3-flash", worktree, artifacts)
            expected = ClaudeCliProvider(
                ProviderConfig(
                    provider_id="claude-cli",
                    binary="claude",
                    model="z-ai/glm-5.3-flash",
                    timeout_seconds=probe.PROBE_TIMEOUT_SECONDS,
                )
            ).argv_for(req)
            self.assertEqual(argv, expected)
        self.assertEqual(argv[argv.index("--max-turns") + 1], str(probe.PROBE_TURNS))
        self.assertEqual(probe.PROBE_TURNS, 8)
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "1")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("Read(./factory_kernel/**)", _flag(argv, "--disallowedTools"))
        self.assertIn("Edit(./app/**)", _flag(argv, "--allowedTools"))
        self.assertNotIn("Read", _flag(argv, "--allowedTools"))
        self.assertEqual(req.role, "test_author")
        self.assertIs(req.path_scope, MUTATION_SCOPE)
        self.assertEqual(req.effort, effort("test_author"))
        self.assertRegex(
            (ROOT / "scripts" / "factory_read_scope_probe.py").read_text(encoding="utf-8"),
            r"(?m)^ROOT = Path\.cwd\(\)\.resolve\(\)$",
        )

    def test_a_denied_read_is_the_proof(self):
        found = probe.measure(
            _probe_stream(
                outside="Permission to use Read on factory_kernel/probe.txt has been denied."
            ),
            returncode=0,
        )
        self.assertTrue(found.attempted_outside)
        self.assertTrue(found.denied_outside)
        self.assertFalse(found.leaked_outside)
        self.assertTrue(found.read_inside)
        self.assertTrue(found.read_artifact)
        self.assertTrue(found.returned)
        self.assertEqual(found.error, "")

    def test_the_file_coming_back_is_a_leak_whatever_the_result_says(self):
        found = probe.measure(_probe_stream(outside=probe.OUTSIDE_SENTINEL + "\n"), returncode=0)
        self.assertTrue(found.leaked_outside)
        self.assertFalse(found.denied_outside)

    def test_a_worker_that_never_tried_is_inconclusive_not_a_proof(self):
        found = probe.measure(_probe_stream(outside="", attempt_outside=False), returncode=0)
        self.assertFalse(found.attempted_outside)
        self.assertFalse(found.denied_outside)
        self.assertEqual(found.error, "trust-root-read-not-attempted")

    def test_run_probe_prints_the_line_and_exits_two_only_on_a_leak(self):
        runner = _FakeRunner(_probe_stream(outside="denied"))
        line, rc = probe.run_probe("m", runner=runner)
        match = PROBE_LINE.match(line)
        self.assertIsNotNone(match, line)
        self.assertEqual(match.group("denied"), "true")
        self.assertEqual(match.group("attempted"), "true")
        self.assertEqual(match.group("inside"), "true")
        self.assertEqual(match.group("artifacts"), "true")
        self.assertIsNone(match.group("error"))
        self.assertEqual(rc, 0)
        (call,) = runner.calls
        self.assertIn("Read(./factory_kernel/**)", _flag(call["argv"], "--disallowedTools"))
        self.assertTrue(Path(call["cwd"]).name == "worktree")

        line, rc = probe.run_probe(
            "m", runner=_FakeRunner(_probe_stream(outside=probe.OUTSIDE_SENTINEL))
        )
        self.assertIn("denied_outside_scope=false", line)
        self.assertEqual(rc, probe.EXIT_LEAK)
        self.assertEqual(probe.EXIT_LEAK, 2)

        line, rc = probe.run_probe(
            "m",
            runner=_FakeRunner(
                lines(init_event(), result_event(is_error=True, result="API Error")), rc=1
            ),
        )
        self.assertIn("denied_outside_scope=false", line)
        self.assertIn("error=", line)
        self.assertEqual(rc, 0, "an errored call proves nothing and refuses nothing")

    def test_main_prints_the_line(self):
        out = io.StringIO()
        with mock.patch.object(probe, "subprocess") as sp, contextlib.redirect_stdout(out):
            sp.run = _FakeRunner(_probe_stream(outside="denied"))
            sp.TimeoutExpired = subprocess.TimeoutExpired
            sp.CompletedProcess = subprocess.CompletedProcess
            rc = probe.main(["--model", "m"])
        self.assertEqual(rc, 0)
        self.assertRegex(out.getvalue().strip(), PROBE_LINE)

    @unittest.skipUnless(WORKER_WORKFLOW.is_file(), "repo-shaped copy without the workflow")
    def test_the_workflow_proves_the_scope_after_the_route_and_refuses_a_leak(self):
        text = WORKER_WORKFLOW.read_text(encoding="utf-8")
        route = text.index("Prove the worker's model route with the pinned CLI")
        scope = text.index("Prove the worker's read scope with the pinned CLI")
        dispatch = text.index("Dispatch exactly one factory action")
        self.assertLess(route, scope)
        self.assertLess(scope, dispatch)
        step = text[scope:].split("\n      - name:", 1)[0]
        self.assertIn("scripts/factory_read_scope_probe.py", step)
        self.assertIn("FACTORY_PREFLIGHT_READ_SCOPE_PROBE", step)
        self.assertIn('if [ "$rc" = "2" ]', step)
        self.assertIn("FACTORY_PREFLIGHT_REFUSED", step)
        self.assertIn("exit 1", step)
        self.assertIn("error=probe-did-not-run", step)
        self.assertIn("ANTHROPIC_AUTH_TOKEN: ${{ secrets.OPENROUTER_API_KEY }}", step)


if __name__ == "__main__":
    unittest.main()
