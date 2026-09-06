"""A guard checkpoint's file is an existing test the author leaves untouched (D-064).

Build run 34027157595 (issue #103) declared red checkpoints on the new
`ChatArea.test.tsx` and a guard on the existing `useStreamingResponse.test.ts`, the kept
hook test the contract says must stay green. The author changed the first and, correctly,
not the second, and the kernel refused: `test-author changed [ChatArea.test.tsx]; declared
acceptance files are [ChatArea.test.tsx, useStreamingResponse.test.ts]`. The rule that the
dirty checkout equals the declared union predates D-058, and a guard's file is precisely a
file the author must not change. These tests pin the rule that replaces it, in the commit
authority and in the RED gate: every changed file is declared (a stray write is refused);
every file of a red checkpoint is changed (a red test is new or modified); a guard's file
exists at the head and may be unchanged, and a changed guard file is accepted only when a red
checkpoint declares it too. Each refusal names the rule and the file. The prompt says the same.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.git_authority import (  # noqa: E402
    CHECKPOINT_KINDS,
    GitAuthorityError,
    commit_acceptance_tests,
)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof = _load("factory_proof_guard_files", "scripts/factory_proof.py")


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


KEPT_TEST = "tests/test_kept.py"  # exists at the base: what a guard pins
NEW_TEST = "tests/test_new.py"  # what the author writes: what a red checkpoint declares
PRODUCT = "app/backend/value.py"


def red(files: list[str], ac: str = "AC-1") -> dict:
    return {
        "acceptance_id": ac,
        "kind": "red",
        "cwd": ".",
        "argv": ["python", "-V"],
        "files": files,
        "expected_failure": f"{ac} declared symptom",
    }


def guard(files: list[str], ac: str = "AC-2") -> dict:
    return {
        "acceptance_id": ac,
        "kind": "guard",
        "cwd": ".",
        "argv": ["python", "-V"],
        "files": files,
    }


# --- the commit authority --------------------------------------------------------------------------


class CommitAuthorityTests(unittest.TestCase):
    """`commit_acceptance_tests` on a real repository whose base holds one kept test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-guard-files-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.artifacts = Path(self.tmp.name) / "artifacts"
        self.artifacts.mkdir()
        (self.repo / "app/backend").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "core.autocrlf", "false")
        (self.repo / PRODUCT).write_text("VALUE = 1\n", encoding="utf-8")
        (self.repo / KEPT_TEST).write_text("def test_kept():\n    assert True\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def spec(self, checkpoints: list[dict], version: str = "2.1") -> Path:
        path = self.artifacts / "test-spec.json"
        path.write_text(
            json.dumps({"version": version, "checkpoints": checkpoints}), encoding="utf-8"
        )
        return path

    def write(self, rel: str, text: str) -> None:
        (self.repo / rel).write_text(text, encoding="utf-8")

    def write_new_test(self) -> None:
        self.write(NEW_TEST, "def test_new():\n    assert False, 'AC-1 declared symptom'\n")

    def committed(self) -> list[str]:
        self.assertEqual(git(self.repo, "status", "--porcelain"), "", "the checkout is clean")
        self.assertNotEqual(git(self.repo, "rev-parse", "HEAD"), self.base, "a commit was made")
        return sorted(git(self.repo, "diff", "--name-only", "HEAD^", "HEAD").splitlines())

    def refused(self, checkpoints: list[dict], version: str = "2.1") -> str:
        with self.assertRaises(GitAuthorityError) as ctx:
            commit_acceptance_tests(self.repo, self.spec(checkpoints, version))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base, "nothing was committed")
        return str(ctx.exception)

    # red-only specs: unchanged behaviour

    def test_a_red_only_spec_whose_changes_equal_its_declaration_is_committed(self):
        self.write_new_test()
        commit_acceptance_tests(self.repo, self.spec([red([NEW_TEST])]))
        self.assertEqual(self.committed(), [NEW_TEST])

    def test_a_2_0_spec_reads_every_checkpoint_as_red(self):
        self.write_new_test()
        cp = red([NEW_TEST])
        del cp["kind"]
        commit_acceptance_tests(self.repo, self.spec([cp], version="2.0"))
        self.assertEqual(self.committed(), [NEW_TEST])

    def test_a_stray_write_is_refused_and_named(self):
        self.write_new_test()
        self.write(PRODUCT, "VALUE = 999\n")
        message = self.refused([red([NEW_TEST])])
        self.assertIn(f"test-author changed undeclared files ['{PRODUCT}']", message)
        self.assertIn("every changed file must be declared by a checkpoint", message)

    def test_a_declared_but_unchanged_red_file_is_refused_and_named(self):
        self.write_new_test()
        message = self.refused([red([NEW_TEST]), red([KEPT_TEST], ac="AC-2")])
        self.assertIn(f"red checkpoint files ['{KEPT_TEST}'] are unchanged", message)
        self.assertIn("a red checkpoint's file must be new or modified", message)

    # guards

    def test_a_guard_on_an_untouched_existing_test_passes_and_the_commit_holds_the_red_file(self):
        """Run 34027157595's shape: a new red test beside a guard on the kept hook test."""
        self.write_new_test()
        commit_acceptance_tests(self.repo, self.spec([red([NEW_TEST]), guard([KEPT_TEST])]))
        self.assertEqual(self.committed(), [NEW_TEST], "the guard's file is not in the commit")
        self.assertEqual(
            git(self.repo, "show", f"HEAD:{KEPT_TEST}"),
            "def test_kept():\n    assert True",
            "the kept test is what it was at the base",
        )

    def test_a_guard_on_a_missing_file_is_refused_and_named(self):
        self.write_new_test()
        missing = "tests/test_nowhere.py"
        message = self.refused([red([NEW_TEST]), guard([missing])])
        self.assertIn(f"guard checkpoint file '{missing}' does not exist at the head", message)
        self.assertIn("a guard pins an existing test", message)

    def test_a_guard_file_changed_without_a_red_declaration_is_refused_and_named(self):
        self.write_new_test()
        self.write(KEPT_TEST, "def test_kept():\n    assert False\n")
        message = self.refused([red([NEW_TEST]), guard([KEPT_TEST])])
        self.assertIn(f"guard checkpoint file '{KEPT_TEST}' was changed", message)
        self.assertIn("no red checkpoint declares it", message)
        self.assertIn("a guard alone must not rewrite the test it guards", message)

    def test_a_guard_and_a_red_checkpoint_on_the_same_changed_file_are_allowed(self):
        self.write(
            KEPT_TEST, "def test_kept():\n    assert True\n\n\ndef test_new():\n    assert False\n"
        )
        commit_acceptance_tests(self.repo, self.spec([red([KEPT_TEST]), guard([KEPT_TEST])]))
        self.assertEqual(self.committed(), [KEPT_TEST])

    def test_a_guard_and_a_red_checkpoint_on_the_same_unchanged_file_are_refused_as_red(self):
        self.write_new_test()
        message = self.refused([red([NEW_TEST, KEPT_TEST]), guard([KEPT_TEST])])
        self.assertIn(f"red checkpoint files ['{KEPT_TEST}'] are unchanged", message)

    def test_an_unknown_kind_is_refused(self):
        self.write_new_test()
        cp = red([NEW_TEST])
        cp["kind"] = "soft"
        message = self.refused([cp])
        self.assertIn(
            "acceptance checkpoint kind must be one of ['red', 'guard']; got 'soft'", message
        )

    def test_a_guard_file_must_still_be_test_shaped(self):
        self.write_new_test()
        message = self.refused([red([NEW_TEST]), guard([PRODUCT])])
        self.assertIn(f"invalid acceptance-test path: '{PRODUCT}'", message)

    def test_both_authorities_know_the_same_kinds(self):
        self.assertEqual(CHECKPOINT_KINDS, proof.CHECKPOINT_KINDS)


# --- the RED gate: the rule ------------------------------------------------------------------------


class RedGateRuleTests(unittest.TestCase):
    def refused(self, checkpoints: list[dict], actual: list[str]) -> str:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            proof.declared_files(checkpoints, actual)
        self.assertEqual(ctx.exception.code, 1)
        return err.getvalue()

    def test_a_red_only_spec_whose_commit_equals_its_declaration_passes(self):
        self.assertEqual(proof.declared_files([red([NEW_TEST])], [NEW_TEST]), [NEW_TEST])

    def test_a_kind_less_checkpoint_is_a_red_one(self):
        cp = red([NEW_TEST])
        del cp["kind"]
        self.assertEqual(proof.declared_files([cp], [NEW_TEST]), [NEW_TEST])
        self.assertIn("are unchanged", self.refused([cp], []))

    def test_a_stray_file_in_the_test_commit_is_refused_and_named(self):
        err = self.refused([red([NEW_TEST])], [NEW_TEST, PRODUCT])
        self.assertIn(f"PROOF_FAIL: test commit changed undeclared files ['{PRODUCT}']", err)
        self.assertIn("every changed file must be declared by a checkpoint", err)

    def test_a_declared_but_unchanged_red_file_is_refused_and_named(self):
        err = self.refused([red([NEW_TEST]), red([KEPT_TEST], ac="AC-2")], [NEW_TEST])
        self.assertIn(f"PROOF_FAIL: red checkpoint files ['{KEPT_TEST}'] are unchanged", err)
        self.assertIn("a red checkpoint's file must be new or modified", err)

    def test_a_guard_on_an_untouched_file_passes_and_is_still_a_declared_file(self):
        declared = proof.declared_files([red([NEW_TEST]), guard([KEPT_TEST])], [NEW_TEST])
        self.assertEqual(declared, [KEPT_TEST, NEW_TEST], "the proof hashes the guard's file too")

    def test_a_guard_file_changed_without_a_red_declaration_is_refused_and_named(self):
        err = self.refused([red([NEW_TEST]), guard([KEPT_TEST])], [NEW_TEST, KEPT_TEST])
        self.assertIn(f"PROOF_FAIL: guard checkpoint files ['{KEPT_TEST}'] were changed", err)
        self.assertIn("no red checkpoint declares them", err)
        self.assertIn("a guard alone must not rewrite the test it guards", err)

    def test_a_guard_and_a_red_checkpoint_on_the_same_changed_file_are_allowed(self):
        declared = proof.declared_files([red([KEPT_TEST]), guard([KEPT_TEST])], [KEPT_TEST])
        self.assertEqual(declared, [KEPT_TEST])

    def test_red_judges_the_commit_by_this_rule(self):
        source = Path(proof.__file__).read_text(encoding="utf-8")
        body = source[source.index("def red(a):") : source.index("def green(a):")]
        self.assertIn("declared=declared_files(s['checkpoints'],changed())", body)
        self.assertNotIn("actual!=declared", body, "the equality is gone")


# --- the RED gate: end to end ----------------------------------------------------------------------


class RedGateEndToEndTests(unittest.TestCase):
    """`factory_proof.py red` on a repository whose test commit leaves the guarded test alone."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dark-factory-guard-files-red-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        (self.root / "tests").mkdir(parents=True)
        (self.root / "app").mkdir()
        self.artifacts = Path(self.tmp.name) / "artifacts"
        self.artifacts.mkdir()
        for patch in (
            mock.patch.object(proof, "ROOT", self.root),
            mock.patch.dict(os.environ, {"ARTIFACTS_DIR": str(self.artifacts)}),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        git(self.root, "init", "-q")
        git(self.root, "config", "core.autocrlf", "false")
        (self.root / "app" / "x.py").write_text("x = 1\n", encoding="utf-8")
        # The kept test, at the base: it passes and the author never touches it.
        (self.root / KEPT_TEST).write_text(
            "print('AC-2 conversation switch still aborts: 3 passed')\n", encoding="utf-8"
        )
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        contract = {
            "version": "2.0",
            "behaviors": [
                {"id": "AC-1", "given": "g", "when": "w", "then": "t", "seam": "s"},
                {
                    "id": "AC-2",
                    "given": "g",
                    "when": "w",
                    "then": "kept",
                    "seam": "s",
                    "kind": "guard",
                },
            ],
        }
        design = {
            "ac_mapping": {"AC-1": ["app/x.py#f"], "AC-2": ["app/x.py#g"]},
            "planned_files": ["app/x.py"],
            "allowed_new_files": [],
        }
        for name, value in (
            ("task-contract.json", contract),
            ("design.json", design),
            ("context.json", {"version": "1.0"}),
        ):
            (self.artifacts / name).write_text(json.dumps(value), encoding="utf-8")
        self.spec_path = self.artifacts / "test-spec.json"
        self.spec_path.write_text(
            json.dumps(
                {
                    "version": "2.1",
                    "checkpoints": [
                        {
                            "acceptance_id": "AC-1",
                            "kind": "red",
                            "cwd": ".",
                            "argv": [sys.executable, NEW_TEST],
                            "files": [NEW_TEST],
                            "expected_failure": "the stream was aborted",
                        },
                        {
                            "acceptance_id": "AC-2",
                            "kind": "guard",
                            "cwd": ".",
                            "argv": [sys.executable, KEPT_TEST],
                            "files": [KEPT_TEST],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.output = self.artifacts / "red-proof.json"

    def test_commit(self, *, rewrite_guard: bool = False) -> str:
        (self.root / NEW_TEST).write_text(
            "print('AssertionError: AC-1 the stream was aborted'); raise SystemExit(1)\n",
            encoding="utf-8",
        )
        if rewrite_guard:
            (self.root / KEPT_TEST).write_text("print('rewritten')\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "test(factory): prove acceptance contract red")
        return git(self.root, "rev-parse", "HEAD")

    def run_red(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            proof.red(types.SimpleNamespace(spec=str(self.spec_path), output=str(self.output)))
        return out.getvalue()

    def test_red_proves_a_guard_whose_file_the_test_commit_left_alone(self):
        test_commit = self.test_commit()
        out = self.run_red()
        self.assertRegex(
            out, r"RED_PROVED criteria=2 tests=2 commit=[0-9a-f]{40} seconds=[\d.]+ guards=1$"
        )
        self.assertIn("RED_CHECKPOINT AC-1 rc=1", out)
        self.assertIn("GUARD_CHECKPOINT AC-2 stage=red rc=0", out)
        red_proof = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(red_proof["test_commit"], test_commit)
        self.assertEqual(
            set(red_proof["files"]),
            {NEW_TEST, KEPT_TEST},
            "the untouched guard file is hashed and immutable from here on",
        )
        self.assertEqual(red_proof["guards"], 1)
        self.assertEqual(
            git(self.root, "diff", "--name-only", "HEAD^", "HEAD").splitlines(),
            [NEW_TEST],
            "the test commit holds only the red file",
        )

    def test_red_refuses_a_test_commit_that_rewrote_the_guarded_test(self):
        self.test_commit(rewrite_guard=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.run_red()
        self.assertIn(
            f"PROOF_FAIL: guard checkpoint files ['{KEPT_TEST}'] were changed", err.getvalue()
        )
        self.assertFalse(self.output.exists(), "refused before any checkpoint ran")


# --- the prompt ------------------------------------------------------------------------------------


class PromptTests(unittest.TestCase):
    def test_the_test_author_prompt_states_the_rule_and_not_the_old_equality(self):
        text = (ROOT / ".factory/prompts/test-author.md").read_text(encoding="utf-8")
        self.assertNotIn("dirty checkout exactly equals the declared test-file union", text)
        self.assertIn("a `guard` checkpoint's file is an existing test you leave untouched", text)
        self.assertIn("every `red` checkpoint's file is one you wrote or changed", text)
        self.assertIn("you changed nothing else", text)
        self.assertIn("accepted only if a `red` checkpoint declares it too", text)


if __name__ == "__main__":
    unittest.main()
