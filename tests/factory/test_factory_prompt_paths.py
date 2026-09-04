"""Worker prompts are rendered with absolute run paths; a literal `$ARTIFACTS_DIR` is refused.

Canary attempt 5 (worker run 33910993905): the investigate worker followed
`$ARTIFACTS_DIR/repro-deferred.json` literally and wrote into a directory named `$ARTIFACTS_DIR`
inside the worktree. Workers have no shell, so the kernel must render the placeholder itself.
"""
from __future__ import annotations

import json
import os
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

from factory_kernel.agents import AgentRequest, AgentResult  # noqa: E402
from factory_kernel.prompt_render import (  # noqa: E402
    RENDERABLE_PLACEHOLDERS,
    PromptRenderError,
    literal_artifacts_dir_entries,
    render_prompt,
)
from factory_kernel.providers import ClaudeCliProvider, prompt_text  # noqa: E402
from factory_kernel.runtime import RunPaths  # noqa: E402

PROMPT_DIR = ROOT / ".factory" / "prompts"
METHOD_DIR = ROOT / ".factory" / "methods"
PLACEHOLDER = re.compile(r"\$\{?[A-Z][A-Z0-9_]*\}?")


class RenderTests(unittest.TestCase):
    @unittest.skipUnless(PROMPT_DIR.is_dir(), "repo-shaped copy without the prompts (mutation runner)")
    def test_every_placeholder_in_the_checked_in_prompts_is_renderable(self):
        seen: set[str] = set()
        for path in list(PROMPT_DIR.glob("*.md")) + list(METHOD_DIR.glob("*.md")):
            seen.update(m.strip("${}") for m in PLACEHOLDER.findall(path.read_text(encoding="utf-8")))
        self.assertTrue(seen, "prompts are expected to name their outputs via a placeholder")
        self.assertLessEqual(seen, set(RENDERABLE_PLACEHOLDERS), seen)

    def test_renderable_set_equals_the_request_environment_the_provider_forwards(self):
        self.assertEqual(set(RENDERABLE_PLACEHOLDERS), set(ClaudeCliProvider.REQUEST_ENV))

    def test_artifacts_dir_is_substituted_in_both_spellings(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = render_prompt("write $ARTIFACTS_DIR/a.json and ${ARTIFACTS_DIR}/b.md", {"ARTIFACTS_DIR": tmp})
            self.assertEqual(out, f"write {Path(tmp)}/a.json and {Path(tmp)}/b.md")
            self.assertNotIn("$", out)

    def test_other_request_env_names_render_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"ARTIFACTS_DIR": tmp, "FACTORY_BASE_REF": "origin/main", "FACTORY_REPO": "o/r"}
            out = render_prompt("base $FACTORY_BASE_REF repo $FACTORY_REPO", env)
            self.assertEqual(out, "base origin/main repo o/r")

    def test_missing_relative_or_nonexistent_artifacts_dir_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for env, why in (
                ({}, "lacks ARTIFACTS_DIR"),
                ({"ARTIFACTS_DIR": "relative/dir"}, "must be absolute"),
                ({"ARTIFACTS_DIR": str(Path(tmp) / "missing")}, "does not exist"),
            ):
                with self.subTest(env=env), self.assertRaisesRegex(PromptRenderError, why):
                    render_prompt("$ARTIFACTS_DIR/x", env)

    def test_unknown_placeholder_is_refused_not_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(PromptRenderError, r"\$SOME_NEW_THING"):
                render_prompt("$ARTIFACTS_DIR/x then $SOME_NEW_THING", {"ARTIFACTS_DIR": tmp})

    def test_shell_positional_and_lowercase_dollars_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = 'run `echo "$1"` and `$var`; cost $5'
            self.assertEqual(render_prompt(text, {"ARTIFACTS_DIR": tmp}), text)

    @unittest.skipUnless(PROMPT_DIR.is_dir(), "repo-shaped copy without the prompts (mutation runner)")
    def test_every_configured_role_prompt_renders_to_absolute_paths(self):
        config = json.loads((ROOT / ".factory" / "kernel.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            for role, rel in config["prompts"].items():
                if not (ROOT / rel).is_file():
                    continue  # repo-shaped mutation copies carry only some prompts
                with self.subTest(role=role):
                    assembled = prompt_text(ROOT / rel, preamble="pre", context="ctx")
                    rendered = render_prompt(assembled, {"ARTIFACTS_DIR": tmp})
                    self.assertNotIn("$ARTIFACTS_DIR", rendered)
                    self.assertNotIn("${ARTIFACTS_DIR}", rendered)
                    if "ARTIFACTS_DIR" in assembled:
                        self.assertIn(str(Path(tmp)), rendered)


class LiteralDirectoryDetectionTests(unittest.TestCase):
    def test_porcelain_entries_under_a_literal_artifacts_dir_are_named(self):
        porcelain = "?? $ARTIFACTS_DIR/\n?? ${ARTIFACTS_DIR}/repro.json\n M app/x.py\n?? other/$ARTIFACTS_DIR\n"
        self.assertEqual(
            literal_artifacts_dir_entries(porcelain),
            ["$ARTIFACTS_DIR/", "${ARTIFACTS_DIR}/repro.json"],
        )

    def test_clean_or_unrelated_status_yields_nothing(self):
        self.assertEqual(literal_artifacts_dir_entries(""), [])
        self.assertEqual(literal_artifacts_dir_entries(" M app/x.py\n"), [])


class _Provider:
    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            provider_id="fake", model="fake", content="ok", session_id="s",
            input_tokens=1, output_tokens=1, cost_usd=0.0, num_turns=1, duration_ms=1,
        )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()


class LauncherTests(unittest.TestCase):
    """The production `_agent` renders before launching and names a literal-directory failure."""

    def _runtime(self, tmp: Path, provider: _Provider):
        from factory_kernel.worker_runtime import WorkerControlledRuntime

        rt = object.__new__(WorkerControlledRuntime)
        rt.repo_root = ROOT
        rt.provider = provider
        rt.config = mock.Mock()
        rt.config.provider.model = "fake"
        prompt = tmp / "prompt.md"
        prompt.write_text("Write only $ARTIFACTS_DIR/out.json\n", encoding="utf-8")
        rt.config.prompt_path = lambda role, cwd: prompt
        rt.check_stop = lambda: None
        return rt

    def test_worker_receives_the_absolute_artifacts_path_not_the_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q")
            provider = _Provider()
            rt = self._runtime(Path(tmp), provider)
            with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""):
                rt._agent("conformance", repo, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})
            sent = provider.requests[0].prompt
            self.assertIn(str(paths.artifacts), sent)
            self.assertNotIn("$ARTIFACTS_DIR", sent)

    def test_launch_is_refused_before_any_model_call_without_an_artifacts_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            provider = _Provider()
            rt = self._runtime(Path(tmp), provider)
            with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""):
                with self.assertRaises(PromptRenderError):
                    rt._agent("conformance", ROOT, paths, env={})
            self.assertEqual(provider.requests, [])

    def test_a_worker_that_wrote_to_a_literal_artifacts_dir_is_named_as_that_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q")

            class Dirtying(_Provider):
                def run(self, request: AgentRequest) -> AgentResult:
                    (repo / "$ARTIFACTS_DIR").mkdir()
                    (repo / "$ARTIFACTS_DIR" / "repro-deferred.json").write_text("{}", encoding="utf-8")
                    return super().run(request)

            rt = self._runtime(Path(tmp), Dirtying())
            with mock.patch("factory_kernel.worker_runtime.method_block", return_value=""):
                with self.assertRaisesRegex(RuntimeError, r"literal \$ARTIFACTS_DIR path"):
                    rt._agent("conformance", repo, paths, env={"ARTIFACTS_DIR": str(paths.artifacts)})


if __name__ == "__main__":
    unittest.main()
