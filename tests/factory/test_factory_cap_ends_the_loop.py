"""A turn cap ends a mutation worker's loop, and the gates judge its draft (D-065).

Build run 34033360798 (issue #103) ended `test_author` at its 30-turn cap: 289 s, 31 turns,
$2.63, `error_max_turns`, 44 tool calls of which 35 were Read, one Write and three Edits had
put `ChatArea.test.tsx` on disk, and four Bash and one Glob call each came back `No such tool
available`. The kernel then failed the build on the CLI's exit code without the static gate,
the commit authority or the RED gate ever seeing the file. Two defects, pinned here.

The tool surface: the policy granted Read, Glob, Grep, Write and Edit, and `--bare` put the
CLI in simple mode (`CLAUDE_CODE_SIMPLE=1`), which registers only Read and Edit whatever
`--tools` says. The provider launches with `--safe-mode --setting-sources ""` instead, the
three mutation prompts name the tools and say there is no shell, and the read-scope probe
also measures Grep and Glob against the deny rules and reports what the CLI registered.

The cap: for `test_author`, `implement` and `repair` an `error_max_turns` envelope is returned
marked `cap_reached`, not raised; the record, row and stage line say so; the kernel runs the
same gates a returned worker gets on what is in the checkout, refuses a clean checkout as
`no_draft_at_cap` and a test author's dirty checkout without a usable spec as `no_spec_at_cap`,
and the needs-human comment of a gate that refuses a capped draft says the cap was reached.
Every other role's cap is the failed stage it was. The caps do not change.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for entry in (str(ROOT), str(HERE), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import factory_read_scope_probe as probe  # noqa: E402
from test_factory_read_scope_and_draft_deadline import PROBE_LINE, _FakeRunner  # noqa: E402
from test_factory_red_evidence_and_stop import FakeGitHub, FakeWorktree  # noqa: E402
from test_factory_static_gate import (  # noqa: E402
    PROD_FILE,
    TEST_FILE,
    ScriptedStatic,
    git,
    repo,
)
from test_factory_static_gate import runtime as gate_runtime  # noqa: E402
from test_factory_static_gate import spec as write_spec  # noqa: E402
from test_factory_stream_timeouts import (  # noqa: E402
    _FakeCliCase,
    assistant_event,
    init_event,
    lines,
    provider_for,
    request,
    result_event,
)

from factory_kernel.agents import AgentRequest, AgentResult  # noqa: E402
from factory_kernel.config import ProviderConfig, load_config  # noqa: E402
from factory_kernel.git_authority import GitAuthorityError  # noqa: E402
from factory_kernel.providers import (  # noqa: E402
    CAP_SUBTYPE,
    ISOLATION_FLAGS,
    ClaudeCliProvider,
    ProviderStageError,
    TransientProviderError,
    unwrap_result_envelope,
)
from factory_kernel.runtime import (  # noqa: E402
    STAGE_TIMINGS,
    KernelRuntime,
    NeedsHuman,
    RunPaths,
    stage_line,
)
from factory_kernel.worker_policy import (  # noqa: E402
    AUTHORITY_ROLES,
    READ_TOOLS,
    REPO_MUTATION_ROLES,
    ROLE_MAX_TURNS,
    ROLE_TOOLS,
    WRITE_TOOLS,
    allowed_tools,
    effort,
    max_turns,
    path_scope,
    stage_timeout_seconds,
)
from factory_kernel.worker_runtime import (  # noqa: E402
    NO_DRAFT_AT_CAP,
    NO_SPEC_AT_CAP,
    WorkerControlledRuntime,
)

PROMPT_DIR = ROOT / ".factory" / "prompts"
RULES = ROOT / "FACTORY_RULES.md"
JUDGE_ROLES = AUTHORITY_ROLES | {"triage"}
TOOL_ROLES = sorted(set(ROLE_TOOLS) - JUDGE_ROLES)
TOOL_SENTENCE = (
    "Your tools are Read, Glob, Grep, Write and Edit (Glob to find files, Grep to search "
    "them); there is no shell and no test runner, so do not try to run anything: the kernel "
    "runs every check after you return."
)
CAP_TURNS = 31
CAP_TEXT = "Reached max turns (30)"


def cap_result(**overrides) -> dict:
    raw = result_event(
        subtype=CAP_SUBTYPE,
        is_error=True,
        result=CAP_TEXT,
        num_turns=CAP_TURNS,
        duration_ms=288939,
        total_cost_usd=2.63,
    )
    raw.update(overrides)
    return raw


# --- the tool surface --------------------------------------------------------------------------


class ToolSurfaceTests(unittest.TestCase):
    def test_every_tool_bearing_role_can_navigate_and_the_mutation_roles_can_write(self):
        for role in TOOL_ROLES:
            with self.subTest(role=role):
                self.assertLessEqual({"Read", "Glob", "Grep"}, set(allowed_tools(role)))
        for role in sorted(REPO_MUTATION_ROLES):
            with self.subTest(role=role):
                self.assertEqual(
                    set(allowed_tools(role)), {"Read", "Glob", "Grep", "Write", "Edit"}
                )
        for role in sorted(JUDGE_ROLES):
            self.assertEqual(allowed_tools(role), ())
        self.assertEqual(set(READ_TOOLS), {"Read", "Glob", "Grep"})
        self.assertEqual(set(WRITE_TOOLS), set(READ_TOOLS) | {"Write", "Edit"})

    def _argv(self, role: str) -> list[str]:
        provider = ClaudeCliProvider(
            ProviderConfig(provider_id="claude-cli", binary="claude", model="m", timeout_seconds=60)
        )
        return provider.argv_for(
            AgentRequest(
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
        )

    def test_the_provider_no_longer_puts_the_cli_in_simple_mode(self):
        """`--bare` sets CLAUDE_CODE_SIMPLE=1, under which the pinned CLI registers only Read
        and Edit whatever `--tools` names (measured on 2.1.245: init tools=['Edit','Read'])."""
        self.assertEqual(ISOLATION_FLAGS, ("--safe-mode", "--setting-sources", ""))
        for role in ("test_author", "implement", "repair", "plan", "holdout"):
            with self.subTest(role=role):
                argv = self._argv(role)
                self.assertEqual(argv[0], "claude")
                self.assertEqual(argv[1 : 1 + len(ISOLATION_FLAGS)], list(ISOLATION_FLAGS))
                self.assertNotIn("--bare", argv)
                self.assertIn("--strict-mcp-config", argv)
                self.assertIn("--disable-slash-commands", argv)

    def test_a_mutation_role_asks_the_cli_for_all_five_tools(self):
        argv = self._argv("test_author")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep,Write,Edit")
        self.assertIn("Read(./factory_kernel/**)", argv[argv.index("--disallowedTools") + 1])


# --- the prompts ---------------------------------------------------------------------------------


class PromptTests(unittest.TestCase):
    def test_every_mutation_prompt_names_its_tools_and_says_there_is_no_shell(self):
        for name in ("test-author.md", "implement.md", "repair.md"):
            with self.subTest(prompt=name):
                text = (PROMPT_DIR / name).read_text(encoding="utf-8")
                self.assertEqual(text.count(TOOL_SENTENCE), 1)
                role = "test_author" if name == "test-author.md" else name[:-3]
                for tool in allowed_tools(role):
                    self.assertIn(tool, TOOL_SENTENCE)
                # The constraints stated before D-065 are still there, verbatim.
                self.assertIn("Do not run commands, stage files or create commits.", text)
                self.assertIn("by turn $DRAFT_DEADLINE_TURN", text)

    def test_no_other_prompt_carries_the_sentence(self):
        for path in PROMPT_DIR.glob("*.md"):
            if path.name in ("test-author.md", "implement.md", "repair.md"):
                continue
            self.assertNotIn("no shell and no test runner", path.read_text(encoding="utf-8"))

    @unittest.skipUnless(RULES.is_file(), "repo-shaped copy without the rules")
    def test_the_rules_state_the_bound(self):
        text = RULES.read_text(encoding="utf-8")
        self.assertIn("a cap ends the loop; the gates judge the draft", text)
        self.assertIn("`no_draft_at_cap`", text)
        self.assertIn("`no_spec_at_cap`", text)


# --- the provider ----------------------------------------------------------------------------


class UnwrapTests(unittest.TestCase):
    def test_a_mutation_roles_cap_is_returned_marked_and_every_other_roles_is_refused(self):
        text = json.dumps(cap_result())
        for role in sorted(REPO_MUTATION_ROLES):
            with self.subTest(role=role):
                envelope = unwrap_result_envelope(text, role=role, events_seen=184)
                self.assertTrue(envelope.cap_reached)
                self.assertEqual(envelope.num_turns, CAP_TURNS)
                self.assertEqual(envelope.subtype, CAP_SUBTYPE)
                self.assertEqual(envelope.content, CAP_TEXT)
                self.assertEqual(envelope.events_seen, 184)
                self.assertTrue(envelope.telemetry()["cap_reached"])
        for role in (
            "plan",
            "investigate",
            "contract",
            "context",
            "architecture",
            "review-spec",
            "review-standards",
            "conformance",
            "holdout",
            "triage",
        ):
            with self.subTest(role=role):
                with self.assertRaises(RuntimeError) as ctx:
                    unwrap_result_envelope(text, role=role)
                self.assertNotIsInstance(ctx.exception, TransientProviderError)
                self.assertIn(CAP_SUBTYPE, str(ctx.exception))

    def test_only_that_subtype_is_a_cap(self):
        for subtype in ("error_max_budget_usd", "error_during_execution", "error"):
            with self.subTest(subtype=subtype), self.assertRaises(RuntimeError):
                unwrap_result_envelope(json.dumps(cap_result(subtype=subtype)), role="test_author")
        ok = unwrap_result_envelope(json.dumps(result_event()), role="test_author")
        self.assertFalse(ok.cap_reached)
        self.assertEqual(CAP_SUBTYPE, "error_max_turns")


def capped_steps(*, rc: int, subtype: str = CAP_SUBTYPE) -> list[dict]:
    """A worker that writes at turn one and is stopped by the CLI at its cap: the `result`
    event the CLI prints for `--max-turns`, then exit 1 as 2.1.245 does (run 34033360798)."""
    write = assistant_event("msg_1", "")
    write["message"]["content"] = [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "Write",
            "input": {"file_path": "app/frontend/src/components/ChatArea.test.tsx"},
        },
    ]
    steps = [
        {"emit": init_event()},
        {"sleep": 0.01, "emit": write},
        {"sleep": 0.01, "emit": assistant_event("msg_2", "editing")},
        {"emit": cap_result(subtype=subtype)},
    ]
    if rc:
        steps.append({"exit": rc})
    return steps


class CapFakeCliTests(_FakeCliCase):
    def test_a_mutation_role_stopped_at_its_cap_returns_with_the_flag_and_no_retry(self):
        for rc in (1, 0):
            with self.subTest(rc=rc):
                self.argv_log.unlink(missing_ok=True)
                self.scenario(capped_steps(rc=rc))
                result = provider_for(self.binary, retries=2).run(request("test_author"))
                self.assertTrue(result.cap_reached)
                self.assertEqual(result.num_turns, CAP_TURNS)
                self.assertEqual(result.content, CAP_TEXT)
                self.assertEqual(result.cost_usd, 2.63)
                self.assertEqual(result.attempts, 1)
                self.assertEqual(result.transient_errors, ())
                self.assertEqual(len(self.launches()), 1, "a cap is not a transient error")

    def test_a_drafting_roles_cap_is_still_a_failed_stage(self):
        self.scenario(capped_steps(rc=1))
        with self.assertRaises(ProviderStageError) as ctx:
            provider_for(self.binary, retries=2).run(request("plan"))
        self.assertIn(CAP_SUBTYPE, str(ctx.exception))
        self.assertEqual(ctx.exception.telemetry.get("subtype"), CAP_SUBTYPE)
        self.assertEqual(len(self.launches()), 1)
        self.scenario(capped_steps(rc=0))
        with self.assertRaises(RuntimeError) as ctx2:
            provider_for(self.binary, retries=2).run(request("contract"))
        self.assertIn(CAP_SUBTYPE, str(ctx2.exception))

    def test_a_budget_stop_is_still_terminal_for_a_mutation_role(self):
        self.scenario(capped_steps(rc=1, subtype="error_max_budget_usd"))
        with self.assertRaises(ProviderStageError) as ctx:
            provider_for(self.binary, retries=2).run(request("implement"))
        self.assertIn("error_max_budget_usd", str(ctx.exception))
        self.assertEqual(len(self.launches()), 1)

    def test_a_healthy_session_is_not_marked(self):
        self.scenario([{"emit": init_event()}, {"emit": result_event()}])
        result = provider_for(self.binary).run(request("repair"))
        self.assertFalse(result.cap_reached)


# --- the kernel: a capped draft goes through the gates -------------------------------------------


class CappedProvider:
    """Each call writes the next scripted batch of files into the worktree and returns at
    the cap (or normally, for the contrast case)."""

    def __init__(self, batches: list[list[tuple[str, str]]], *, capped: bool = True) -> None:
        self.batches = [list(batch) for batch in batches]
        self.capped = capped
        self.requests: list[AgentRequest] = []

    def run(self, request, before_retry=None, **_kwargs):
        self.requests.append(request)
        for rel, text in self.batches.pop(0):
            target = Path(request.cwd) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return AgentResult(
            provider_id="fake",
            model="fake",
            content=CAP_TEXT if self.capped else "done",
            num_turns=CAP_TURNS if self.capped else 9,
            duration_ms=1,
            cap_reached=self.capped,
        )


class _CappedCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-cap-")
        self.addCleanup(self.tmp.cleanup)

    def _runtime(self, batches, verdicts, *, capped: bool = True):
        """A fresh repository, runtime and run directory each call, so a subTest loop can
        call it more than once."""
        home = Path(tempfile.mkdtemp(dir=self.tmp.name))
        self.root = repo(home)
        self.base = git(self.root, "rev-parse", "HEAD")
        provider = CappedProvider(batches, capped=capped)
        static = ScriptedStatic(verdicts)
        rt, paths = gate_runtime(home, self.root, provider, static)
        return rt, paths, provider, static

    def _agent(self, rt, paths, role: str) -> str:
        out = io.StringIO()
        with (
            mock.patch("factory_kernel.worker_runtime.method_block", return_value=""),
            mock.patch("factory_kernel.git_authority.refresh_lockfiles"),
            contextlib.redirect_stdout(out),
        ):
            rt._agent(role, self.root, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
        return out.getvalue()

    def head_subject(self) -> str:
        return git(self.root, "log", "--format=%s", "-1")


class CappedTestAuthorTests(_CappedCase):
    def test_a_capped_draft_goes_through_the_static_gate_and_the_commit_authority(self):
        rt, paths, _provider, static = self._runtime(
            [[(TEST_FILE, "it('x', () => {});\n")]], [True]
        )
        write_spec(paths, [TEST_FILE])
        printed = self._agent(rt, paths, "test_author")
        self.assertEqual(static.calls, [[TEST_FILE]], "the static gate judged the capped draft")
        self.assertEqual(self.head_subject(), "test(factory): prove acceptance contract red")
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        record = json.loads(
            (paths.transcripts / "agent-test_author.json").read_text(encoding="utf-8")
        )
        self.assertTrue(record["cap_reached"])
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual(record["num_turns"], CAP_TURNS)
        rows = [
            json.loads(line)
            for line in (paths.transcripts / STAGE_TIMINGS).read_text(encoding="utf-8").splitlines()
        ]
        (row,) = [r for r in rows if r["kind"] == "agent"]
        self.assertTrue(row["cap_reached"])
        self.assertIn("FACTORY_STAGE kind=agent name=test_author ", printed)
        self.assertIn(" cap_reached=true", printed)
        self.assertIn(f"turns={CAP_TURNS}", printed)

    def test_a_clean_checkout_at_the_cap_is_refused_by_name_before_any_gate(self):
        rt, paths, _provider, static = self._runtime([[]], [True])
        write_spec(paths, [TEST_FILE])
        with self.assertRaises(NeedsHuman) as ctx:
            self._agent(rt, paths, "test_author")
        message = str(ctx.exception)
        self.assertIn(f"`{NO_DRAFT_AT_CAP}`", message)
        self.assertIn(f"{CAP_TURNS} turns", message)
        self.assertIn(CAP_SUBTYPE, message)
        self.assertEqual(static.calls, [])
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)
        self.assertEqual(NO_DRAFT_AT_CAP, "no_draft_at_cap")

    def test_a_dirty_checkout_without_a_spec_is_refused_naming_the_files(self):
        """Run 34033360798: the test file was written and edited, test-spec.json was not."""
        rt, paths, _provider, static = self._runtime(
            [[(TEST_FILE, "it('x', () => {});\n")]], [True]
        )
        with self.assertRaises(NeedsHuman) as ctx:
            self._agent(rt, paths, "test_author")
        message = str(ctx.exception)
        self.assertIn(f"`{NO_SPEC_AT_CAP}`", message)
        self.assertIn("test-spec.json was not written", message)
        self.assertIn(TEST_FILE, message)
        self.assertEqual(static.calls, [])
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)
        self.assertTrue((self.root / TEST_FILE).is_file(), "the draft is left for the human")
        self.assertEqual(NO_SPEC_AT_CAP, "no_spec_at_cap")

    def test_a_spec_that_declares_a_file_not_on_disk_is_refused_the_same_way(self):
        other = "app/frontend/src/lib/other.test.ts"
        rt, paths, _provider, static = self._runtime([[(TEST_FILE, "x\n")]], [True])
        write_spec(paths, [other])
        with self.assertRaises(NeedsHuman) as ctx:
            self._agent(rt, paths, "test_author")
        message = str(ctx.exception)
        self.assertIn(f"`{NO_SPEC_AT_CAP}`", message)
        self.assertIn("declared files not on disk", message)
        self.assertIn(other, message)
        self.assertIn(TEST_FILE, message)
        self.assertEqual(static.calls, [])

    def test_a_spec_that_is_not_json_or_has_no_checkpoints_is_refused_the_same_way(self):
        for body, fragment in (
            ("not json", "not valid JSON"),
            ('{"version": "2.1"}', "no checkpoints"),
        ):
            with self.subTest(body=body):
                rt, paths, _provider, static = self._runtime([[(TEST_FILE, "x\n")]], [True])
                (paths.artifacts / "test-spec.json").write_text(body, encoding="utf-8")
                with self.assertRaises(NeedsHuman) as ctx:
                    self._agent(rt, paths, "test_author")
                self.assertIn(f"`{NO_SPEC_AT_CAP}`", str(ctx.exception))
                self.assertIn(fragment, str(ctx.exception))
                self.assertEqual(static.calls, [])

    def test_the_static_hand_back_still_applies_to_a_capped_draft(self):
        rt, paths, provider, static = self._runtime(
            [[(TEST_FILE, "bad\n")], [(TEST_FILE, "good\n")]], [False, True]
        )
        write_spec(paths, [TEST_FILE])
        self._agent(rt, paths, "test_author")
        self.assertEqual([r.role for r in provider.requests], ["test_author", "test_author"])
        self.assertIn("STATIC CHECK FAILURE", provider.requests[1].prompt)
        self.assertEqual(static.calls, [[TEST_FILE], [TEST_FILE]])
        self.assertEqual(self.head_subject(), "test(factory): prove acceptance contract red")
        records = sorted(p.name for p in paths.transcripts.glob("agent-test_author*.json"))
        self.assertEqual(records, ["agent-test_author.2.json", "agent-test_author.json"])

    def test_the_commit_authority_still_refuses_a_capped_draft_that_breaks_its_rules(self):
        stray = "app/frontend/src/lib/stray.test.ts"
        rt, paths, _provider, _static = self._runtime(
            [[(TEST_FILE, "x\n"), (stray, "y\n")]], [True]
        )
        write_spec(paths, [TEST_FILE])
        with self.assertRaises(GitAuthorityError) as ctx:
            self._agent(rt, paths, "test_author")
        self.assertIn("undeclared files", str(ctx.exception))
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)

    def test_a_returned_worker_with_a_clean_checkout_is_refused_as_before_not_as_a_cap(self):
        rt, paths, _provider, _static = self._runtime([[]], [True], capped=False)
        write_spec(paths, [TEST_FILE])
        with self.assertRaises(GitAuthorityError) as ctx:
            self._agent(rt, paths, "test_author")
        self.assertNotIn(NO_DRAFT_AT_CAP, str(ctx.exception))
        record = json.loads(
            (paths.transcripts / "agent-test_author.json").read_text(encoding="utf-8")
        )
        self.assertFalse(record["cap_reached"])


class CappedImplementTests(_CappedCase):
    def _artifacts(self, paths: RunPaths) -> None:
        (paths.artifacts / "task-contract.json").write_text(
            json.dumps({"issue": {"number": 7}}), encoding="utf-8"
        )
        (paths.artifacts / "design.json").write_text(
            json.dumps({"planned_files": [PROD_FILE]}), encoding="utf-8"
        )
        (paths.artifacts / "red-proof.json").write_text(json.dumps({"files": {}}), encoding="utf-8")

    def test_a_capped_implementation_is_committed_by_the_design_envelope(self):
        rt, paths, _provider, static = self._runtime(
            [[(PROD_FILE, "export const x = 2;\n")]], [True]
        )
        self._artifacts(paths)
        printed = self._agent(rt, paths, "implement")
        self.assertEqual(static.calls, [[PROD_FILE]])
        self.assertEqual(self.head_subject(), "fix(factory): satisfy issue #7")
        self.assertIn(" cap_reached=true", printed)

    def test_a_capped_repair_is_committed_too(self):
        rt, paths, _provider, _static = self._runtime(
            [[(PROD_FILE, "export const x = 3;\n")]], [True]
        )
        self._artifacts(paths)
        self._agent(rt, paths, "repair")
        self.assertEqual(self.head_subject(), "fix(factory): repair issue #7")

    def test_a_clean_checkout_at_the_cap_is_refused_for_implement_and_repair(self):
        for role in ("implement", "repair"):
            with self.subTest(role=role):
                rt, paths, _provider, static = self._runtime([[]], [True])
                self._artifacts(paths)
                with self.assertRaises(NeedsHuman) as ctx:
                    self._agent(rt, paths, role)
                self.assertIn(f"`{NO_DRAFT_AT_CAP}`", str(ctx.exception))
                self.assertTrue(str(ctx.exception).startswith(f"{role} reached its turn cap"))
                self.assertEqual(static.calls, [])
                self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base)

    def test_a_capped_draft_outside_the_envelope_is_refused_by_the_commit_authority(self):
        rt, paths, _provider, _static = self._runtime(
            [[("app/frontend/src/lib/y.ts", "y\n")]], [True]
        )
        self._artifacts(paths)
        with self.assertRaises(GitAuthorityError) as ctx:
            self._agent(rt, paths, "implement")
        self.assertIn("outside compiled design", str(ctx.exception))


# --- the record, the row, the line and the comment -------------------------------------------


class RecordTests(unittest.TestCase):
    def test_the_stage_line_carries_the_flag_only_when_set(self):
        row = {
            "kind": "agent",
            "name": "test_author",
            "seconds": 288.939,
            "num_turns": 31,
            "outcome": "ok",
            "cap_reached": True,
            "model": "minimax/minimax-m3",
        }
        line = stage_line(row)
        self.assertIn(" outcome=ok ", line)
        self.assertIn(" cap_reached=true ", line)
        self.assertNotIn("cap_reached", stage_line({**row, "cap_reached": None}))
        self.assertNotIn(
            "cap_reached", stage_line({k: v for k, v in row.items() if k != "cap_reached"})
        )

    def test_the_record_and_the_row_say_the_cap_was_reached(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = object.__new__(KernelRuntime)
            rt.config = mock.Mock()
            rt.config.provider.model = "fake"
            result = AgentResult(
                provider_id="fake",
                model="minimax/minimax-m3",
                content=CAP_TEXT,
                num_turns=CAP_TURNS,
                duration_ms=288939,
                cost_usd=2.63,
                events_seen=184,
                cap_reached=True,
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rt._record_agent(paths, "test_author", result, started=time.time() - 1)
            record = json.loads(
                (paths.transcripts / "agent-test_author.json").read_text(encoding="utf-8")
            )
            self.assertIs(record["cap_reached"], True)
            self.assertEqual(record["outcome"], "ok")
            (row,) = [
                json.loads(line)
                for line in (paths.transcripts / STAGE_TIMINGS)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertIs(row["cap_reached"], True)
            self.assertIn(" cap_reached=true", out.getvalue())
            # A returned worker's record does not carry the flag as true, and its row omits it.
            paths2 = RunPaths.create(Path(tmp), "run2")
            with contextlib.redirect_stdout(io.StringIO()):
                rt._record_agent(
                    paths2,
                    "implement",
                    AgentResult(
                        provider_id="fake", model="m", content="done", num_turns=3, duration_ms=1
                    ),
                    started=time.time() - 1,
                )
            record2 = json.loads(
                (paths2.transcripts / "agent-implement.json").read_text(encoding="utf-8")
            )
            self.assertIs(record2["cap_reached"], False)
            (row2,) = [
                json.loads(line)
                for line in (paths2.transcripts / STAGE_TIMINGS)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertNotIn("cap_reached", row2)


def _cap_record(paths: RunPaths, role: str = "test_author", name: str | None = None) -> None:
    paths.transcripts.mkdir(parents=True, exist_ok=True)
    (paths.transcripts / f"{name or 'agent-' + role}.json").write_text(
        json.dumps(
            {
                "role": role,
                "record": name or f"agent-{role}",
                "outcome": "ok",
                "cap_reached": True,
                "num_turns": CAP_TURNS,
            }
        ),
        encoding="utf-8",
    )


class CommentEvidenceTests(unittest.TestCase):
    def test_a_capped_stage_in_the_run_is_named_in_the_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            self.assertEqual(KernelRuntime._cap_reached_evidence(paths), "")
            self.assertEqual(KernelRuntime._failure_evidence(paths, RuntimeError("x")), "")
            _cap_record(paths)
            text = KernelRuntime._cap_reached_evidence(paths)
            self.assertIn("Turn cap reached (`cap_reached=true`)", text)
            self.assertIn("`test_author` (agent-test_author: 31 turns, `error_max_turns`)", text)
            self.assertIn("a gate's verdict on that draft", text)
            self.assertIn(text, KernelRuntime._failure_evidence(paths, RuntimeError("x")))
            # A second run of the stage and an uncapped stage beside it.
            _cap_record(paths, "repair", "agent-repair.2")
            (paths.transcripts / "agent-implement.json").write_text(
                json.dumps({"role": "implement", "cap_reached": False}), encoding="utf-8"
            )
            text = KernelRuntime._cap_reached_evidence(paths)
            self.assertIn("agent-repair.2", text)
            self.assertNotIn("implement", text)


class BuildCommentTests(unittest.TestCase):
    """build_issue driven to its first model stage against fakes."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-cap-build-")
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.work_root = home / "work"
        self.work_root.mkdir()
        self.worktree = home / "worktree"
        self.worktree.mkdir()
        patcher = mock.patch.dict(os.environ, {"FACTORY_WORKDIR": str(self.work_root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        config = load_config(ROOT / ".factory" / "kernel.json")
        self.config = dataclasses.replace(
            config, runtime=dataclasses.replace(config.runtime, work_root=self.work_root)
        )

    def _build(self, agent, expected):
        gh = FakeGitHub()
        rt = KernelRuntime(repo_root=ROOT, config=self.config)
        rt.github = gh  # type: ignore[assignment]
        rt.check_stop = lambda: None  # type: ignore[method-assign]
        rt._fetch_main = lambda: None  # type: ignore[method-assign]
        rt._git = lambda *args, cwd=None: "a" * 40  # type: ignore[method-assign]
        rt._prepare_worktree = lambda cwd, paths: None  # type: ignore[method-assign]
        rt._issue_frontier = lambda issue: {"version": "1.0", "issue": dict(issue), "blockers": []}  # type: ignore[method-assign]
        rt._lease_heartbeat = lambda *args, **kwargs: None  # type: ignore[method-assign]
        rt._agent = agent  # type: ignore[method-assign]
        with (
            mock.patch(
                "factory_kernel.runtime.create_detached", return_value=FakeWorktree(self.worktree)
            ),
            mock.patch("factory_kernel.runtime.remove"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(expected),
        ):
            rt.build_issue(49)
        self.assertIn((49, "factory:needs-human"), gh.added)
        ((number, body),) = gh.comments
        self.assertEqual(number, 49)
        return body

    def test_a_clean_checkout_at_the_cap_reaches_the_comment_by_name(self):
        def agent(role, cwd, paths, *, context="", env):
            raise NeedsHuman(
                f"test_author reached its turn cap ({CAP_TURNS} turns, `{CAP_SUBTYPE}`) and "
                f"left no change in the checkout (`{NO_DRAFT_AT_CAP}`): there is no draft for "
                "the gates to judge"
            )

        body = self._build(agent, NeedsHuman)
        self.assertIn("`no_draft_at_cap`", body)
        self.assertIn("31 turns", body)

    def test_a_gate_refusing_a_capped_draft_says_the_cap_was_reached(self):
        def agent(role, cwd, paths, *, context="", env):
            _cap_record(paths)
            raise GitAuthorityError(
                "test-author changed undeclared files ['app/x.test.ts']; every changed file "
                "must be declared by a checkpoint"
            )

        body = self._build(agent, GitAuthorityError)
        self.assertIn("builder failed closed: test-author changed undeclared files", body)
        self.assertIn("Turn cap reached (`cap_reached=true`)", body)
        self.assertIn("31 turns, `error_max_turns`", body)


# --- the preflight probe ---------------------------------------------------------------------


def _call(
    message_id: str, name: str, params: dict, result: str, *, is_error: bool = False
) -> list[dict]:
    use = assistant_event(message_id, "")
    use["message"]["content"] = [
        {"type": "tool_use", "id": f"toolu_{message_id}", "name": name, "input": params},
    ]
    answer = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"toolu_{message_id}",
                    "content": result,
                    "is_error": is_error,
                },
            ],
        },
        "parent_tool_use_id": None,
        "session_id": "s-1",
    }
    return [use, answer]


INSIDE_LINE = f"app/probe.txt:1:{probe.INSIDE_SENTINEL}"
OUTSIDE_LINE = f"factory_kernel/probe.txt:1:{probe.OUTSIDE_SENTINEL}"


def _stream(
    *,
    grep: str | None = INSIDE_LINE,
    glob: str | None = "app/probe.txt",
    outside: str = "Permission to read ./factory_kernel/probe.txt has been denied.",
    tools: list[str] | None = None,
) -> str:
    init = init_event()
    if tools is not None:
        init["tools"] = tools
    events = [init]
    events += _call(
        "m1", "Read", {"file_path": "./" + probe.INSIDE_FILE}, probe.INSIDE_SENTINEL + "\n"
    )
    events += _call(
        "m2",
        "Read",
        {"file_path": "./" + probe.OUTSIDE_FILE},
        outside,
        is_error=outside != probe.OUTSIDE_SENTINEL,
    )
    events += _call(
        "m3", "Read", {"file_path": "/tmp/artifacts/note.txt"}, probe.ARTIFACT_SENTINEL + "\n"
    )
    if grep is not None:
        events += _call(
            "m4",
            "Grep",
            {"pattern": probe.SENTINEL_PREFIX, "path": ".", "output_mode": "content"},
            grep,
        )
    if glob is not None:
        events += _call("m5", "Glob", {"pattern": probe.GLOB_PATTERN}, glob)
    events += [assistant_event("m6", "done"), result_event(num_turns=6)]
    return lines(*events)


class ProbeGrepGlobTests(unittest.TestCase):
    def test_the_prompt_asks_for_the_grep_and_the_glob_and_the_probe_has_the_turns(self):
        text = probe.probe_prompt(Path("/tmp/artifacts"))
        self.assertIn(f"Grep for the pattern {probe.SENTINEL_PREFIX}", text)
        self.assertIn(f"Glob for the pattern {probe.GLOB_PATTERN}", text)
        self.assertIn("./" + probe.OUTSIDE_FILE, text)
        for sentinel in (probe.INSIDE_SENTINEL, probe.OUTSIDE_SENTINEL, probe.ARTIFACT_SENTINEL):
            self.assertTrue(sentinel.startswith(probe.SENTINEL_PREFIX))
        self.assertEqual(probe.PROBE_TURNS, 8)

    def test_a_grep_that_finds_the_inside_line_and_not_the_outside_one_is_the_proof(self):
        found = probe.measure(_stream(), returncode=0)
        self.assertTrue(found.grep_attempted)
        self.assertTrue(found.grep_saw_inside)
        self.assertFalse(found.grep_saw_outside)
        self.assertTrue(found.grep_denied_outside)
        self.assertTrue(found.glob_attempted)
        self.assertFalse(found.glob_listed_outside)
        self.assertEqual(found.tools, ("Edit", "Glob", "Grep", "Read", "Write"))
        self.assertEqual(found.tools_missing, ())
        self.assertTrue(found.denied_outside)
        self.assertEqual(found.error, "")
        line = probe.probe_line("m", found)
        match = PROBE_LINE.match(line)
        self.assertIsNotNone(match, line)
        self.assertEqual(match.group("grep"), "true")
        self.assertEqual(match.group("glob"), "false")
        self.assertEqual(match.group("tools"), "Edit,Glob,Grep,Read,Write")
        self.assertEqual(match.group("missing"), "none")

    def test_a_grep_that_surfaces_the_trust_root_line_is_a_leak(self):
        line, rc = probe.run_probe(
            "m", runner=_FakeRunner(_stream(grep=INSIDE_LINE + "\n" + OUTSIDE_LINE))
        )
        self.assertEqual(rc, probe.EXIT_LEAK)
        self.assertIn("grep_denied_outside_scope=false", line)
        self.assertIn("denied_outside_scope=false", line)

    def test_a_glob_that_lists_the_trust_root_path_is_reported_and_not_a_refusal(self):
        line, rc = probe.run_probe(
            "m", runner=_FakeRunner(_stream(glob="app/probe.txt\nfactory_kernel/probe.txt"))
        )
        self.assertEqual(rc, 0, "a path name is data; the file's contents did not come back")
        self.assertIn("glob_outside_scope=true", line)
        self.assertIn("grep_denied_outside_scope=true", line)

    def test_a_grep_that_found_nothing_or_was_never_made_proves_nothing(self):
        self.assertFalse(probe.measure(_stream(grep=""), returncode=0).grep_denied_outside)
        found = probe.measure(_stream(grep=None, glob=None), returncode=0)
        self.assertFalse(found.grep_attempted)
        self.assertFalse(found.grep_denied_outside)
        self.assertFalse(found.glob_attempted)
        self.assertTrue(found.denied_outside, "the Read measurement is unchanged")
        self.assertEqual(found.error, "")

    def test_the_tools_the_cli_dropped_are_named(self):
        """What `--bare` did on 2.1.245: init tools=['Edit','Read'] against the policy's five."""
        found = probe.measure(_stream(grep=None, glob=None, tools=["Edit", "Read"]), returncode=0)
        self.assertEqual(found.tools, ("Edit", "Read"))
        self.assertEqual(found.tools_missing, ("Glob", "Grep", "Write"))
        line = probe.probe_line("m", found)
        self.assertIn(" tools=Edit,Read tools_missing=Glob,Grep,Write ", line)
        self.assertIsNotNone(PROBE_LINE.match(line), line)
        no_init = probe.measure(lines(result_event(is_error=True, result="x")), returncode=1)
        self.assertIsNone(no_init.tools)
        self.assertIn(" tools=none tools_missing=none ", probe.probe_line("m", no_init))

    def test_run_probe_prints_the_full_line_and_exits_zero_on_the_proof(self):
        runner = _FakeRunner(_stream())
        line, rc = probe.run_probe("m", runner=runner)
        self.assertEqual(rc, 0)
        self.assertRegex(line, PROBE_LINE)
        (call,) = runner.calls
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--max-turns") + 1], str(probe.PROBE_TURNS))
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep,Write,Edit")
        self.assertNotIn("--bare", argv)
        self.assertIn("--safe-mode", argv)


class CapsUnchangedTests(unittest.TestCase):
    def test_the_caps_did_not_move(self):
        for role in sorted(REPO_MUTATION_ROLES):
            self.assertEqual(ROLE_MAX_TURNS[role], 30)
        self.assertIsInstance(WorkerControlledRuntime, type)


if __name__ == "__main__":
    unittest.main()
