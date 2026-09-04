"""The boundary around a model-authored repro command.

"python is allowlisted" is not a sandbox. Three things bound what a repro can do: the argv must
match a test-runner shape (no interpreter eval, no shell metacharacters, no absolute paths);
the child environment is built from an allowlist, so no secret the worker holds can reach it;
and the worktree must be byte-identical before and after, so the repro cannot rewrite what the
contract worker reads next.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import credential_env, repro as r  # noqa: E402

SECRET_NAMES = (
    *credential_env.GITHUB_CREDENTIALS,
    *credential_env.VALIDATION_CREDENTIALS,
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_TOKEN",
    "AWS_SECRET_ACCESS_KEY", "GOOGLE_API_KEY", "AZURE_OPENAI_KEY",
)
for name in SECRET_NAMES:
    assert name in credential_env.GITHUB_CREDENTIALS or name in credential_env.VALIDATION_CREDENTIALS \
        or name.startswith(credential_env.PROVIDER_CREDENTIAL_PREFIXES), name


def repro(argv, **over):
    base = {"version": "1.0", "argv": argv, "cwd": ".", "expect_failure_containing": "boom happened"}
    base.update(over)
    return base


class EnvironmentAllowlistTests(unittest.TestCase):
    def test_every_known_secret_is_withheld_even_when_the_parent_holds_it(self):
        source = {name: f"leak-{name}" for name in SECRET_NAMES}
        source.update({"PATH": "/usr/bin", "HOME": "/home/x", "SOME_UNKNOWN_VAR": "leak-unknown"})
        env = r.repro_env(source)
        for name in SECRET_NAMES:
            self.assertNotIn(name, env, name)
        self.assertNotIn("SOME_UNKNOWN_VAR", env, "allowlist semantics: unknown names are not forwarded")
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/home/x")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

    def test_only_allowlisted_names_are_forwarded(self):
        source = {k: "v" for k in (*r.REPRO_ENV_KEYS, "GH_TOKEN", "OPENROUTER_API_KEY", "FOO", "PYTHONSTARTUP")}
        env = r.repro_env(source)
        self.assertEqual(set(env), set(r.REPRO_ENV_KEYS) | set(r.REPRO_ENV_SYNTHETIC))

    def test_empty_values_are_not_forwarded(self):
        self.assertNotIn("PYTHONPATH", r.repro_env({"PATH": "/bin", "PYTHONPATH": ""}))

    def test_execute_builds_the_child_env_from_the_allowlist(self):
        seen = {}

        def capture(argv, cwd, env, timeout):
            seen.update(env)
            return subprocess.CompletedProcess(list(argv), 1, "boom happened", "")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "leak", "SUPADATA_API_KEY": "leak",
                         "ANTHROPIC_AUTH_TOKEN": "leak", "DATABASE_URL": "leak", "JWT_SECRET": "leak"},
        ):
            r.execute(r.validate_repro(repro(["pytest", "t"])), worktree=Path(tmp), runner=capture)
        for name in ("OPENROUTER_API_KEY", "SUPADATA_API_KEY", "ANTHROPIC_AUTH_TOKEN", "DATABASE_URL", "JWT_SECRET"):
            self.assertNotIn(name, seen, name)

    def test_real_child_process_sees_only_the_allowlist(self):
        """One real subprocess: the interpreter running these tests prints its environment."""
        probe = "import os, json; print(json.dumps(sorted(os.environ)))"
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "leak", "GH_TOKEN": "leak"}):
            env = r.repro_env()
        proc = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True)
        names = set(json.loads(proc.stdout))
        for secret in SECRET_NAMES:
            self.assertNotIn(secret, names, secret)
        self.assertTrue(names <= set(r.REPRO_ENV_KEYS) | set(r.REPRO_ENV_SYNTHETIC) | _platform_injected(),
                        names - set(r.REPRO_ENV_KEYS) - set(r.REPRO_ENV_SYNTHETIC))


def _platform_injected() -> set[str]:
    # Windows' CreateProcess adds a few names of its own; none is a credential.
    return {"SYSTEMROOT", "COMSPEC", "PATHEXT", "SYSTEMDRIVE", "WINDIR", "USERPROFILE", "PROGRAMDATA", "LOCALAPPDATA"}


class CommandShapeTests(unittest.TestCase):
    def test_each_allowed_shape_is_accepted(self):
        for shape in r.ALLOWED_SHAPES:
            argv = [*shape, "app/backend/tests/test_x.py", "-k", "name", "-q"]
            with self.subTest(shape=shape):
                self.assertEqual(r.validate_repro(repro(argv)).argv, tuple(argv))

    def test_interpreter_eval_is_refused(self):
        for argv in (["python", "-c", "import os"], ["uv", "run", "python", "-c", "x"],
                     ["python", "-m", "pytest", "-c", "x"], ["pytest", "--eval", "x"],
                     ["bun", "run", "test", "-e", "x"], ["pytest", "-p", "x"]):
            with self.subTest(argv=argv), self.assertRaisesRegex(r.ReproRefused, "shape|eval/exec"):
                r.validate_repro(repro(argv))

    def test_programs_outside_the_shapes_are_refused(self):
        for argv in (["npx", "vitest", "run"], ["python", "script.py"], ["python"], ["uv", "run", "python", "x.py"],
                     ["bun", "run", "build"], ["bun", "x", "something"], ["uv", "pip", "install", "x"]):
            with self.subTest(argv=argv), self.assertRaisesRegex(r.ReproRefused, "shape"):
                r.validate_repro(repro(argv))

    def test_shell_metacharacters_are_refused(self):
        for bad in ("tests; rm -rf x", "a|b", "a&&b", "a>out", "a<in", "$(id)", "`id`"):
            with self.subTest(arg=bad), self.assertRaisesRegex(r.ReproRefused, "metacharacter"):
                r.validate_repro(repro(["pytest", bad]))

    def test_absolute_and_escaping_paths_are_refused(self):
        for bad, rule in (("/etc/passwd", "absolute"), ("C:\\x", "absolute"), ("~/x", "absolute"),
                          ("../other/test.py", "escapes"), ("a/../../x", "escapes")):
            with self.subTest(arg=bad), self.assertRaisesRegex(r.ReproRefused, rule):
                r.validate_repro(repro(["pytest", bad]))

    def test_no_bare_program_allowlist_remains(self):
        self.assertFalse(hasattr(r, "ALLOWED_PROGRAMS"))
        for shape in r.ALLOWED_SHAPES:
            self.assertNotIn("npx", shape)
            self.assertNotEqual(shape, ("python",))


class CleanTreeGuardTests(unittest.TestCase):
    def _runtime(self, artifacts: Path):
        from factory_kernel.runtime import KernelRuntime

        rt = KernelRuntime.__new__(KernelRuntime)
        rt._write_json = lambda path, value: Path(path).write_text(json.dumps(value), encoding="utf-8")
        rt._agent = mock.Mock(name="_agent")
        (artifacts / "repro.json").write_text(json.dumps(repro(["pytest", "t"])), encoding="utf-8")
        return rt

    def test_a_repro_that_writes_into_the_worktree_is_refused_before_the_contract(self):
        from factory_kernel.runtime import NeedsHuman

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "a"; artifacts.mkdir()
            wt = Path(tmp) / "wt"; wt.mkdir()
            rt = self._runtime(artifacts)
            statuses = iter(["", "?? planted.py"])
            rt._git = lambda *args, cwd=None: next(statuses)

            def dirty(argv, cwd, env, timeout):
                (Path(cwd) / "planted.py").write_text("x", encoding="utf-8")
                return subprocess.CompletedProcess(list(argv), 1, "boom happened", "")

            with self.assertRaisesRegex(NeedsHuman, "modified the worktree"):
                rt._observe_repro(artifacts, wt, runner=dirty)
            rt._agent.assert_not_called()
            self.assertFalse((artifacts / "repro-observed.json").exists())

    def test_status_is_compared_with_untracked_files_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "a"; artifacts.mkdir()
            wt = Path(tmp) / "wt"; wt.mkdir()
            rt = self._runtime(artifacts)
            calls = []
            rt._git = lambda *args, cwd=None: calls.append((args, cwd)) or ""
            rt._observe_repro(artifacts, wt, runner=lambda a, c, e, t: subprocess.CompletedProcess(list(a), 1, "boom happened", ""))
            self.assertEqual(len(calls), 2)
            for args, cwd in calls:
                self.assertEqual(args, ("status", "--porcelain", "--untracked-files=all"))
                self.assertEqual(cwd, wt)

    def test_unchanged_worktree_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "a"; artifacts.mkdir()
            wt = Path(tmp) / "wt"; wt.mkdir()
            rt = self._runtime(artifacts)
            rt._git = lambda *args, cwd=None: " M app/x.py"  # same dirt before and after
            ctx = rt._observe_repro(artifacts, wt, runner=lambda a, c, e, t: subprocess.CompletedProcess(list(a), 1, "boom happened", ""))
            self.assertIn("REPRO OBSERVED", ctx)


if __name__ == "__main__":
    unittest.main()
