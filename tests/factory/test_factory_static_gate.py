"""Static checks run on a worker's files before the kernel commits them (D-043).

An acceptance test that fails biome is RED-hashed and immutable the moment RED succeeds, so
the only stage that can fix it is the one that wrote it, before the commit. These tests drive
the real `WorkerControlledRuntime._agent` with a fake provider that writes files into a real
temporary Git repository, and a fake static runner, and assert the gate's order, its bound and
its scope.

The formatter pass (D-068) is here too: build run 34047586142 spent a whole second `test_author`
process, thirty turns and its entire budget on one biome formatting difference and produced
nothing. The gate now applies the FORMATTER - and only the formatter - to the worker's own files
and re-checks before it hands anything back, so a finding that is whitespace costs nothing and a
finding that survives the formatter is the only kind a worker is ever asked about.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import static_gate  # noqa: E402
from factory_kernel.agents import AgentResult  # noqa: E402
from factory_kernel.runtime import NeedsHuman, RunPaths  # noqa: E402
from factory_kernel.static_gate import (  # noqa: E402
    StaticResult,
    check_files,
    commands_for,
    format_commands_for,
    partition,
)
from factory_kernel.worker_runtime import STATIC_RETRIES, WorkerControlledRuntime  # noqa: E402

TEST_FILE = "app/frontend/src/lib/x.test.ts"
PROD_FILE = "app/frontend/src/lib/x.ts"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def repo(tmp: Path) -> Path:
    root = tmp / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    (root / PROD_FILE).parent.mkdir(parents=True)
    (root / PROD_FILE).write_text("export const x = 1;\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


class ScriptedProvider:
    """Each call writes the next scripted file content into the worktree."""

    def __init__(self, writes: list[tuple[str, str]]) -> None:
        self.writes = list(writes)
        self.requests: list = []

    def run(self, request, before_retry=None, **_kwargs):
        self.requests.append(request)
        rel, text = self.writes.pop(0)
        target = Path(request.cwd) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return AgentResult(provider_id="fake", model="fake", content="ok", num_turns=1, duration_ms=1)


class ScriptedStatic:
    """Static verdicts in order; records the file sets it was asked about."""

    def __init__(self, verdicts: list[bool]) -> None:
        self.verdicts = list(verdicts)
        self.calls: list[list[str]] = []

    def __call__(self, cwd: Path, files: list[str]) -> StaticResult:
        self.calls.append(list(files))
        ok = self.verdicts.pop(0)
        return StaticResult(ok=ok, checks=("biome",), output="" if ok else "lint/complexity/x: two spaces")


def runtime(tmp: Path, root: Path, provider, static) -> tuple[WorkerControlledRuntime, RunPaths]:
    rt = object.__new__(WorkerControlledRuntime)
    rt.repo_root = root
    rt.provider = provider
    rt.config = mock.Mock()
    rt.config.provider.model = "fake"
    prompt = tmp / "prompt.md"
    prompt.write_text("role prompt\n", encoding="utf-8")
    rt.config.prompt_path = lambda role, cwd: prompt
    rt.check_stop = lambda: None
    rt._scoped_static = static
    paths = RunPaths.create(tmp / "runs", "run")
    return rt, paths


def spec(paths: RunPaths, files: list[str]) -> None:
    (paths.artifacts / "test-spec.json").write_text(json.dumps({
        "version": "2.0",
        "checkpoints": [{"acceptance_id": "AC-1", "cwd": "app/frontend", "argv": ["bun", "test"],
                         "files": files, "expected_failure": "boom"}],
    }), encoding="utf-8")


class AcceptanceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-static-gate-")
        self.root = repo(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, writes, verdicts):
        provider = ScriptedProvider(writes)
        static = ScriptedStatic(verdicts)
        rt, paths = runtime(Path(self.tmp.name), self.root, provider, static)
        spec(paths, [TEST_FILE])
        env = {"ARTIFACTS_DIR": str(paths.artifacts)}
        with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""):
            rt._agent("test_author", self.root, paths, env=env)
        return provider, static, paths

    def test_clean_files_are_committed_after_one_check(self):
        provider, static, paths = self._run([(TEST_FILE, "it('x', () => {});\n")], [True])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(static.calls, [[TEST_FILE]])
        self.assertEqual(git(self.root, "log", "--format=%s", "-1"), "test(factory): prove acceptance contract red")
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_a_lint_failure_is_handed_back_once_then_committed(self):
        provider, static, paths = self._run(
            [(TEST_FILE, "bad\n"), (TEST_FILE, "good\n")], [False, True],
        )
        self.assertEqual(len(provider.requests), 2)
        retry_prompt = provider.requests[1].prompt
        self.assertIn("STATIC CHECK FAILURE", retry_prompt)
        self.assertIn("two spaces", retry_prompt)
        self.assertIn(TEST_FILE, retry_prompt)
        self.assertEqual(provider.requests[1].role, "test_author")
        self.assertEqual(static.calls, [[TEST_FILE], [TEST_FILE]])
        self.assertEqual((self.root / TEST_FILE).read_text(encoding="utf-8"), "good\n")
        self.assertEqual(git(self.root, "log", "--format=%s", "-1"), "test(factory): prove acceptance contract red")
        records = sorted(p.name for p in paths.artifacts.glob("static-gate-test_author-*.json"))
        self.assertEqual(records, ["static-gate-test_author-1.json", "static-gate-test_author-2.json"])

    def test_the_failed_files_stay_in_place_for_the_retry(self):
        """The retry edits the uncommitted files rather than starting from a restored tree."""
        seen: list[str] = []

        class Peek(ScriptedProvider):
            def run(self, request, before_retry=None, **_kwargs):
                seen.append((Path(request.cwd) / TEST_FILE).read_text(encoding="utf-8") if (Path(request.cwd) / TEST_FILE).exists() else "<absent>")
                return super().run(request, before_retry, **_kwargs)

        provider = Peek([(TEST_FILE, "bad\n"), (TEST_FILE, "good\n")])
        static = ScriptedStatic([False, True])
        rt, paths = runtime(Path(self.tmp.name), self.root, provider, static)
        spec(paths, [TEST_FILE])
        with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""):
            rt._agent("test_author", self.root, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
        self.assertEqual(seen, ["<absent>", "bad\n"])

    def test_two_failures_escalate_and_nothing_is_committed(self):
        with self.assertRaises(NeedsHuman) as ctx:
            self._run([(TEST_FILE, "bad\n"), (TEST_FILE, "still bad\n")], [False, False])
        self.assertIn("static checks", str(ctx.exception))
        self.assertIn("two spaces", str(ctx.exception))
        self.assertEqual(git(self.root, "log", "--format=%s", "-1"), "base")
        self.assertEqual(STATIC_RETRIES, 1)

    def test_gate_runs_before_the_red_commit(self):
        """At the moment the gate runs, HEAD must still be the base commit."""
        heads: list[str] = []
        base = git(self.root, "rev-parse", "HEAD")

        def static(cwd: Path, files: list[str]) -> StaticResult:
            heads.append(git(cwd, "rev-parse", "HEAD"))
            return StaticResult(ok=True, checks=("biome",))

        provider = ScriptedProvider([(TEST_FILE, "fine\n")])
        rt, paths = runtime(Path(self.tmp.name), self.root, provider, static)
        spec(paths, [TEST_FILE])
        with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""):
            rt._agent("test_author", self.root, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
        self.assertEqual(heads, [base])
        self.assertNotEqual(git(self.root, "rev-parse", "HEAD"), base)


class ImplementGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-static-gate-")
        self.root = repo(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _artifacts(self, paths: RunPaths) -> None:
        (paths.artifacts / "task-contract.json").write_text(json.dumps({"issue": {"number": 7}}), encoding="utf-8")
        (paths.artifacts / "design.json").write_text(json.dumps({"planned_files": [PROD_FILE]}), encoding="utf-8")
        (paths.artifacts / "red-proof.json").write_text(json.dumps({"files": {}}), encoding="utf-8")

    def test_implement_lint_failure_is_repaired_once_by_a_fresh_repair_worker(self):
        provider = ScriptedProvider([(PROD_FILE, "export const x = 2 ;\n"), (PROD_FILE, "export const x = 2;\n")])
        static = ScriptedStatic([False, True])
        rt, paths = runtime(Path(self.tmp.name), self.root, provider, static)
        self._artifacts(paths)
        with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""), \
             mock.patch("factory_kernel.git_authority.refresh_lockfiles"):
            rt._agent("implement", self.root, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
        self.assertEqual([r.role for r in provider.requests], ["implement", "repair"])
        self.assertIn("STATIC CHECK FAILURE", provider.requests[1].prompt)
        self.assertEqual(static.calls, [[PROD_FILE], [PROD_FILE]])
        self.assertEqual(git(self.root, "log", "--format=%s", "-1"), "fix(factory): repair issue #7")

    def test_implement_two_failures_escalate(self):
        provider = ScriptedProvider([(PROD_FILE, "bad\n"), (PROD_FILE, "bad\n")])
        static = ScriptedStatic([False, False])
        rt, paths = runtime(Path(self.tmp.name), self.root, provider, static)
        self._artifacts(paths)
        with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""), \
             mock.patch("factory_kernel.git_authority.refresh_lockfiles"), \
             self.assertRaises(NeedsHuman):
            rt._agent("implement", self.root, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
        self.assertEqual(git(self.root, "log", "--format=%s", "-1"), "base")


class FakeTools:
    """A fake `biome`: `check` refuses, `format --write` rewrites, and nothing else exists.

    Formatting here is trailing whitespace, which `format --write` strips; a lint finding is
    the token BUG, which no formatter can remove. `check` reports the formatter difference the
    way biome does (`Formatter would have printed the following content:`), which is the exact
    finding that cost build run 34047586142 a whole `test_author` process.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, cwd, env, timeout):
        self.calls.append(list(argv))
        rels = [a for a in argv if a.endswith((".ts", ".tsx", ".py"))]
        paths = [Path(cwd) / rel for rel in rels]
        writing = "--write" in argv or (
            argv[:4] == ["uv", "run", "ruff", "format"] and "--check" not in argv
        )
        if writing:
            for path in paths:
                # Byte-preserving apart from the whitespace it removes: line endings are
                # left exactly as they were, so "reformatted" means here what it means in
                # the kernel - the file's bytes changed.
                raw = path.read_text(encoding="utf-8", newline="")
                path.write_text(
                    re.sub(r"[ \t]+(\r?\n)", r"\1", raw), encoding="utf-8", newline="",
                )
            return subprocess.CompletedProcess(argv, 0, "1 file reformatted", "")
        texts = {path: path.read_text(encoding="utf-8") for path in paths}
        lint = [path for path, text in texts.items() if "BUG" in text]
        if lint:
            return subprocess.CompletedProcess(
                argv, 1, f"{lint[0].name}:1:1 lint/suspicious/noBug  a real finding", "",
            )
        unformatted = [
            path for path, text in texts.items()
            if any(line != line.rstrip() for line in text.splitlines())
        ]
        if unformatted:
            return subprocess.CompletedProcess(
                argv, 1,
                f"{unformatted[0].name} format  "
                "Formatter would have printed the following content:",
                "",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")


class FormatterPassTests(unittest.TestCase):
    """`check_files` applies the formatter, re-checks, and reports what it rewrote (D-068)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-formatter-")
        self.root = Path(self.tmp.name) / "wt"
        (self.root / "app/frontend/src/lib").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, text: str) -> None:
        (self.root / TEST_FILE).write_text(text, encoding="utf-8", newline="\n")

    def test_a_formatting_only_finding_is_fixed_and_never_becomes_a_finding(self):
        self._write("it('x', () => {});   \n")
        tools = FakeTools()
        result = check_files(self.root, [TEST_FILE], runner=tools)
        self.assertTrue(result.ok, result.output)
        self.assertEqual(result.formatted, (TEST_FILE,))
        self.assertEqual(
            (self.root / TEST_FILE).read_text(encoding="utf-8"), "it('x', () => {});\n"
        )
        self.assertEqual([call[:4] for call in tools.calls], [
            ["bun", "x", "biome", "check"],
            ["bun", "x", "biome", "format"],
            ["bun", "x", "biome", "check"],
        ])

    def test_a_finding_that_survives_the_formatter_is_still_a_finding(self):
        self._write("BUG   \n")
        result = check_files(self.root, [TEST_FILE], runner=FakeTools())
        self.assertFalse(result.ok)
        self.assertIn("lint/suspicious/noBug", result.output)
        self.assertEqual(result.formatted, (TEST_FILE,))

    def test_a_formatter_that_changes_nothing_leaves_the_first_verdict_untouched(self):
        self._write("BUG\n")
        tools = FakeTools()
        result = check_files(self.root, [TEST_FILE], runner=tools)
        self.assertFalse(result.ok)
        self.assertEqual(result.formatted, ())
        self.assertIn("lint/suspicious/noBug", result.output)
        # Exactly today's behaviour: one check, a formatter that did nothing, no second check.
        self.assertEqual([call[:4] for call in tools.calls], [
            ["bun", "x", "biome", "check"],
            ["bun", "x", "biome", "format"],
        ])

    def test_a_clean_first_pass_never_runs_the_formatter(self):
        self._write("it('x', () => {});\n")
        tools = FakeTools()
        result = check_files(self.root, [TEST_FILE], runner=tools)
        self.assertTrue(result.ok)
        self.assertEqual(result.formatted, ())
        self.assertEqual(len(tools.calls), 1)

    def test_a_formatter_that_cannot_run_behaves_exactly_as_today(self):
        self._write("it('x', () => {});   \n")
        seen: list[list[str]] = []

        def runner(argv, cwd, env, timeout):
            seen.append(list(argv))
            if "format" in argv:
                raise FileNotFoundError(argv[0])
            return subprocess.CompletedProcess(argv, 1, "Formatter would have printed", "")

        result = check_files(self.root, [TEST_FILE], runner=runner)
        self.assertFalse(result.ok)
        self.assertEqual(result.formatted, ())
        self.assertIn("Formatter would have printed", result.output)
        self.assertEqual(len(seen), 2, "one check, one failed formatter, no second check")

    def test_a_formatter_that_times_out_behaves_exactly_as_today(self):
        self._write("it('x', () => {});   \n")

        def runner(argv, cwd, env, timeout):
            if "format" in argv:
                raise subprocess.TimeoutExpired(argv, timeout)
            return subprocess.CompletedProcess(argv, 1, "Formatter would have printed", "")

        result = check_files(self.root, [TEST_FILE], runner=runner)
        self.assertFalse(result.ok)
        self.assertEqual(result.formatted, ())

    def test_only_the_workers_own_files_are_formatted(self):
        """The formatter is scoped to the declared files exactly as the checks are."""
        self._write("it('x', () => {});   \n")
        other = self.root / "app/frontend/src/lib/untouched.ts"
        other.write_text("const y = 1;   \n", encoding="utf-8", newline="\n")
        tools = FakeTools()
        check_files(self.root, [TEST_FILE], runner=tools)
        self.assertEqual(other.read_text(encoding="utf-8"), "const y = 1;   \n")
        for call in tools.calls:
            self.assertNotIn("src/lib/untouched.ts", call)

    def test_an_unscoped_file_runs_no_formatter_at_all(self):
        def never(argv, cwd, env, timeout):
            raise AssertionError("must not run")

        self.assertEqual(check_files(self.root, ["docs/x.md"], runner=never).formatted, ())


class FormatterScopeTests(unittest.TestCase):
    def test_the_formatter_commands_are_the_repositorys_own_formatters(self):
        plan = format_commands_for(["app/backend/tests/test_x.py", "app/frontend/src/a.test.tsx"])
        self.assertEqual([p[0] for p in plan], ["ruff-format-write", "biome-format-write"])
        self.assertEqual(plan[0][1], "app/backend")
        self.assertEqual(plan[0][2], ["uv", "run", "ruff", "format", "tests/test_x.py"])
        self.assertEqual(plan[1][1], "app/frontend")
        self.assertEqual(plan[1][2], ["bun", "x", "biome", "format", "--write", "src/a.test.tsx"])
        self.assertEqual(format_commands_for(["docs/x.md"]), [])

    def test_no_lint_fix_is_ever_applied(self):
        """Formatting is whitespace and cannot change behaviour; a lint fix can (D-068)."""
        for files in (["app/backend/x.py"], ["app/frontend/src/x.ts"]):
            for _label, _cwd, argv in format_commands_for(files) + commands_for(files):
                with self.subTest(argv=argv):
                    self.assertNotIn("--fix", argv)
                    self.assertNotIn("--unsafe-fixes", argv)
                    if "check" in argv:
                        self.assertNotIn("--write", argv)

    def test_the_source_applies_no_fix_flag_anywhere(self):
        import inspect
        source = inspect.getsource(static_gate)
        for banned in ('"--fix"', '"--unsafe-fixes"', '"check", "--write"'):
            self.assertNotIn(banned, source)


class FormatterHandBackTests(unittest.TestCase):
    """Through the real `_agent`: a whitespace finding costs no worker process (D-068)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-formatter-gate-")
        self.root = repo(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, text: str):
        provider = ScriptedProvider([(TEST_FILE, text), (TEST_FILE, "it('x', () => {});\n")])
        tools = FakeTools()
        rt, paths = runtime(Path(self.tmp.name), self.root, provider, None)
        rt._scoped_static = lambda cwd, files: check_files(cwd, files, runner=tools)
        spec(paths, [TEST_FILE])
        out = io.StringIO()
        with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""), \
             contextlib.redirect_stdout(out):
            rt._agent("test_author", self.root, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
        return provider, paths, out.getvalue()

    def _record(self, paths: RunPaths, attempt: int = 1) -> dict:
        name = f"static-gate-test_author-{attempt}.json"
        return json.loads((paths.artifacts / name).read_text(encoding="utf-8"))

    def test_a_formatting_only_failure_never_reaches_a_hand_back(self):
        provider, paths, printed = self._run("it('x', () => {});   \n")
        self.assertEqual(len(provider.requests), 1, "a second stage was spent on whitespace")
        record = self._record(paths)
        self.assertTrue(record["ok"])
        self.assertEqual(record["formatted"], [TEST_FILE])
        self.assertIn(
            f"FACTORY_STATIC_FORMATTED role=test_author attempt=1 files={TEST_FILE}", printed
        )
        self.assertEqual(
            git(self.root, "log", "--format=%s", "-1"),
            "test(factory): prove acceptance contract red",
        )

    def test_the_reformatted_file_is_what_gets_committed(self):
        self._run("it('x', () => {});   \n")
        self.assertEqual(git(self.root, "show", f"HEAD:{TEST_FILE}"), "it('x', () => {});")

    def test_a_real_finding_still_costs_a_hand_back_and_the_reformat_is_recorded(self):
        provider, paths, printed = self._run("BUG   \n")
        self.assertEqual(len(provider.requests), 2)
        self.assertIn("STATIC CHECK FAILURE", provider.requests[1].prompt)
        self.assertIn("lint/suspicious/noBug", provider.requests[1].prompt)
        record = self._record(paths)
        self.assertFalse(record["ok"])
        self.assertEqual(record["formatted"], [TEST_FILE])
        self.assertIn("FACTORY_STATIC_FORMATTED", printed)

    def test_nothing_is_printed_when_nothing_was_reformatted(self):
        provider, paths, printed = self._run("BUG\n")
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(self._record(paths)["formatted"], [])
        self.assertNotIn("FACTORY_STATIC_FORMATTED", printed)


class ScopeTests(unittest.TestCase):
    def test_partition_by_stack_and_suffix(self):
        backend, frontend, other = partition([
            "app/backend/tests/test_x.py", "app/frontend/src/a.test.tsx", "app/frontend/src/b.ts",
            "app/backend/README.md", "tests/factory/test_y.py", "docs/x.md",
        ])
        self.assertEqual(backend, ["tests/test_x.py"])
        self.assertEqual(frontend, ["src/a.test.tsx", "src/b.ts"])
        self.assertEqual(other, ["app/backend/README.md", "tests/factory/test_y.py", "docs/x.md"])

    def test_commands_mirror_the_quick_gate_tools_scoped_to_files(self):
        plan = commands_for(["app/backend/tests/test_x.py", "app/frontend/src/a.test.tsx"])
        labels = [p[0] for p in plan]
        self.assertEqual(labels, ["ruff-lint", "ruff-format", "biome"])
        self.assertEqual(plan[0][2], ["uv", "run", "ruff", "check", "tests/test_x.py"])
        self.assertEqual(plan[1][2], ["uv", "run", "ruff", "format", "--check", "tests/test_x.py"])
        self.assertEqual(plan[2][2], ["bun", "x", "biome", "check", "src/a.test.tsx"])
        self.assertEqual(commands_for(["docs/x.md"]), [])

    def test_check_files_runs_with_no_credentials_and_fails_closed(self):
        seen = {}

        def runner(argv, cwd, env, timeout):
            # The first call is the check; the formatter pass that follows a failure is
            # covered by FormatterPassTests and must not overwrite what this asserts.
            seen.setdefault("env", dict(env))
            seen.setdefault("argv", list(argv))
            return subprocess.CompletedProcess(argv, 1, "x.ts:1:1 lint/style/bad", "")

        with mock.patch.dict("os.environ", {"GH_TOKEN": "t", "OPENROUTER_API_KEY": "k", "PATH": "/bin"}, clear=True):
            result = check_files(Path("/wt"), ["app/frontend/src/x.ts"], runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("lint/style/bad", result.output)
        self.assertNotIn("GH_TOKEN", seen["env"])
        self.assertNotIn("OPENROUTER_API_KEY", seen["env"])
        self.assertEqual(seen["argv"][:4], ["bun", "x", "biome", "check"])

    def test_missing_tool_or_timeout_is_a_failure_not_a_skip(self):
        def missing(argv, cwd, env, timeout):
            raise FileNotFoundError(argv[0])

        def slow(argv, cwd, env, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        self.assertFalse(check_files(Path("/wt"), ["app/frontend/src/x.ts"], runner=missing).ok)
        self.assertFalse(check_files(Path("/wt"), ["app/backend/x.py"], runner=slow).ok)

    def test_unscoped_files_pass_without_running_anything(self):
        def never(argv, cwd, env, timeout):
            raise AssertionError("must not run")

        result = check_files(Path("/wt"), ["tests/factory/test_y.py"], runner=never)
        self.assertTrue(result.ok)
        self.assertEqual(result.skipped, ("tests/factory/test_y.py",))

    def test_default_runner_never_uses_a_shell(self):
        import inspect
        self.assertNotIn("shell=True", inspect.getsource(static_gate.default_runner))


if __name__ == "__main__":
    unittest.main()
