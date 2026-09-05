"""Static checks run on a worker's files before the kernel commits them (D-043).

An acceptance test that fails biome is RED-hashed and immutable the moment RED succeeds, so
the only stage that can fix it is the one that wrote it, before the commit. These tests drive
the real `WorkerControlledRuntime._agent` with a fake provider that writes files into a real
temporary Git repository, and a fake static runner, and assert the gate's order, its bound and
its scope.
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

from factory_kernel import static_gate  # noqa: E402
from factory_kernel.agents import AgentResult  # noqa: E402
from factory_kernel.runtime import NeedsHuman, RunPaths  # noqa: E402
from factory_kernel.static_gate import StaticResult, check_files, commands_for, partition  # noqa: E402
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
            seen["env"] = dict(env)
            seen["argv"] = list(argv)
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
