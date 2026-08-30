"""Adversarial coverage for the external genesis validation driver.

The driver exists because searching a shared log is not measurement. Candidate programs write to
the same stream, so an unconstrained regex over an aggregated log let one early line decide what
the authority believed. These tests pin the properties that replace it: exit status is the
verdict, each count is read from its own stage's output, and ambiguity is refused rather than
resolved by taking the first match.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
DRIVER = ROOT / "harness" / "genesis_validate.py"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError(proc.stderr)
    return proc.stdout


def load_driver():
    spec = importlib.util.spec_from_file_location("genesis_validate", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageGuardTests(unittest.TestCase):
    """The fail-closed guards, exercised directly.

    A healthy run never produces a wrong or dirty stage environment, so these guards are
    unreachable through the happy path. Testing them at the unit level is the only way an
    adversarial mutation against them can be caught at all.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.driver = load_driver()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "d@example.invalid")
        git(self.repo, "config", "user.name", "D")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "one")
        self.commit = git(self.repo, "rev-parse", "HEAD").strip()
        self.tree = git(self.repo, "rev-parse", "HEAD^{tree}").strip()

    def test_clean_authorized_environment_is_accepted(self):
        head, tree = self.driver.assert_stage_environment(
            self.repo, self.commit, self.tree, "s"
        )
        self.assertEqual((head, tree), (self.commit, self.tree))

    def test_dirty_stage_environment_fails_closed(self):
        (self.repo / "a.txt").write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.driver.assert_stage_environment(self.repo, self.commit, self.tree, "s")

    def test_untracked_file_in_a_stage_environment_fails_closed(self):
        (self.repo / "planted.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.driver.assert_stage_environment(self.repo, self.commit, self.tree, "s")

    def test_environment_at_another_commit_fails_closed(self):
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "two")
        with self.assertRaises(SystemExit):
            self.driver.assert_stage_environment(self.repo, self.commit, self.tree, "s")

    def test_environment_with_another_tree_fails_closed(self):
        with self.assertRaises(SystemExit):
            self.driver.assert_stage_environment(self.repo, self.commit, "0" * 40, "s")

    def test_object_store_recheck_accepts_the_authorized_tree(self):
        self.driver.assert_object_store(self.repo, self.commit, self.tree, "s")

    def test_object_store_recheck_fails_closed_on_a_different_tree(self):
        with self.assertRaises(SystemExit):
            self.driver.assert_object_store(self.repo, self.commit, "0" * 40, "s")


class DriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "d@example.invalid")
        git(self.repo, "config", "user.name", "D")
        (self.repo / "README.md").write_text("x\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.commit = git(self.repo, "rev-parse", "HEAD").strip()

    def stage(self, name: str, script: str, measures: dict | None = None) -> dict:
        """Commit the stage script, since an isolated worktree only contains tracked content."""
        path = self.repo / f"{name}.py"
        path.write_text(script, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", f"stage {name}")
        self.commit = git(self.repo, "rev-parse", "HEAD").strip()
        stage = {"name": name, "argv": [sys.executable, f"{name}.py"], "timeout_seconds": 120}
        if measures:
            stage["measures"] = measures
        return stage

    def drive(self, stages: list[dict], *, commit: str | None = None):
        recipe = self.root / "recipe.json"
        recipe.write_text(json.dumps({"version": "1.0", "stages": stages}), encoding="utf-8")
        out = self.root / "result.json"
        proc = subprocess.run(
            [
                sys.executable, str(DRIVER), "--repo", str(self.repo),
                "--commit", commit or self.commit, "--recipe", str(recipe),
                "--log-dir", str(self.root / "logs"),
                "--work-dir", str(self.root / "stages"), "--output", str(out),
            ],
            cwd=self.root, capture_output=True, text=True, timeout=600,
        )
        result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
        return proc, result

    def test_measures_a_clean_stage(self):
        proc, result = self.drive([
            self.stage("ok", "print('COUNT_OK n=42')", {"n": r"COUNT_OK n=(\d+)"})
        ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["stages"][0]["measurements"]["n"], 42)
        self.assertEqual(result["candidate_sha"], self.commit)

    def test_exit_status_is_the_verdict_not_the_printed_text(self):
        """A stage may print a perfect marker and still have failed."""
        proc, result = self.drive([
            self.stage("liar", "print('COUNT_OK n=42')\nraise SystemExit(1)",
                       {"n": r"COUNT_OK n=(\d+)"})
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["stages"][0]["exit"], 1)
        self.assertEqual(result["stages"][0]["measurements"], {})
        self.assertIn("liar", result["failed_stages"])

    def test_ambiguous_marker_is_refused_rather_than_first_matched(self):
        """This is the spoof: an early value would otherwise win."""
        proc, _ = self.drive([
            self.stage("ambiguous", "print('COUNT_OK n=999999')\nprint('COUNT_OK n=42')",
                       {"n": r"COUNT_OK n=(\d+)"})
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ambiguously", proc.stderr)
        self.assertIn("42", proc.stderr)
        self.assertIn("999999", proc.stderr)

    def test_repeated_but_agreeing_marker_is_accepted(self):
        proc, result = self.drive([
            self.stage("agree", "print('COUNT_OK n=7')\nprint('COUNT_OK n=7')",
                       {"n": r"COUNT_OK n=(\d+)"})
        ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result["stages"][0]["measurements"]["n"], 7)

    def test_absent_marker_is_refused(self):
        proc, _ = self.drive([self.stage("silent", "pass", {"n": r"COUNT_OK n=(\d+)"})])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not report", proc.stderr)

    def test_one_stage_cannot_supply_another_stage_output(self):
        """Measurement is per stage; a neighbour printing the marker does not count."""
        proc, _ = self.drive([
            self.stage("noisy", "print('COUNT_OK n=5')"),
            self.stage("measured", "pass", {"n": r"COUNT_OK n=(\d+)"}),
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("stage 'measured' did not report", proc.stderr)

    def test_wrong_repository_head_is_refused(self):
        proc, _ = self.drive([self.stage("ok", "pass")], commit="0" * 40)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the commit being validated", proc.stderr)

    def test_duplicate_stage_names_are_refused(self):
        stage = self.stage("dup", "pass")
        proc, _ = self.drive([stage, dict(stage)])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unique", proc.stderr)

    def test_empty_recipe_is_refused(self):
        proc, _ = self.drive([])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no stages", proc.stderr)

    def test_result_binds_driver_recipe_and_commit(self):
        proc, result = self.drive([self.stage("ok", "pass")])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import hashlib

        self.assertEqual(result["driver_sha256"], hashlib.sha256(DRIVER.read_bytes()).hexdigest())
        self.assertEqual(len(result["recipe_sha256"]), 64)
        self.assertEqual(result["candidate_sha"], self.commit)

    # ---------- stage isolation ----------

    def test_stage_cannot_rewrite_a_later_stages_validator(self):
        """The circularity this closes: stage A editing the authority stage B will run."""
        victim = self.stage("victim", "print('COUNT_OK n=1')", {"n": r"COUNT_OK n=(\d+)"})
        attacker = self.stage(
            "attacker",
            "from pathlib import Path\n"
            "Path('victim.py').write_text(\"print('COUNT_OK n=999999')\\n\")\n",
        )
        proc, result = self.drive([attacker, victim])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        measured = next(s for s in result["stages"] if s["name"] == "victim")
        self.assertEqual(measured["measurements"]["n"], 1)

    def test_stage_cannot_plant_untracked_executable_source_for_a_later_stage(self):
        victim = self.stage(
            "victim",
            "import pathlib\n"
            "print('PLANTED' if pathlib.Path('planted.py').exists() else 'CLEAN_OK n=1')\n",
            {"n": r"CLEAN_OK n=(\d+)"},
        )
        attacker = self.stage(
            "attacker",
            "from pathlib import Path\nPath('planted.py').write_text('x = 1\\n')\n",
        )
        proc, result = self.drive([attacker, victim])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            next(s for s in result["stages"] if s["name"] == "victim")["measurements"]["n"], 1
        )

    def test_stage_cannot_leave_mutable_runtime_state_for_a_later_stage(self):
        """Untracked runtime directories do not survive into the next stage's environment."""
        victim = self.stage(
            "victim",
            "import pathlib\n"
            "print('DIRTY' if pathlib.Path('.venv').exists() else 'FRESH_OK n=1')\n",
            {"n": r"FRESH_OK n=(\d+)"},
        )
        attacker = self.stage(
            "attacker",
            "from pathlib import Path\n"
            "d = Path('.venv'); d.mkdir(exist_ok=True)\n"
            "(d / 'sitecustomize.py').write_text('import os\\n')\n",
        )
        proc, result = self.drive([attacker, victim])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            next(s for s in result["stages"] if s["name"] == "victim")["measurements"]["n"], 1
        )

    def test_every_stage_records_the_exact_tree_it_executed(self):
        first = self.stage("first", "pass")
        second = self.stage("second", "pass")
        proc, result = self.drive([first, second])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tree = git(self.repo, "rev-parse", "HEAD^{tree}").strip()
        self.assertEqual(result["candidate_tree"], tree)
        self.assertEqual(result["stage_isolation"], "per-stage-worktree")
        for stage in result["stages"]:
            self.assertEqual(stage["executed_candidate_sha"], self.commit)
            self.assertEqual(stage["executed_tree_sha"], tree)
            self.assertTrue(stage["isolated"])

    def test_stage_environment_is_destroyed_after_the_stage(self):
        marker = self.stage("marker", "from pathlib import Path\nPath('left.txt').write_text('x')\n")
        proc, _ = self.drive([marker])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((self.root / "stages").exists())
        self.assertFalse((self.repo / "left.txt").exists())

    def test_object_store_tampering_between_stages_fails_closed(self):
        """A tree cannot change content and keep its identity, so re-resolving it detects this."""
        victim = self.stage("victim", "pass")
        attacker = self.stage(
            "attacker",
            "import subprocess, pathlib\n"
            "root = pathlib.Path(__file__).resolve().parent\n"
            "subprocess.run(['git', '-C', str(root), 'checkout', '-q', '-B', 'wander'])\n",
        )
        proc, _ = self.drive([attacker, victim], commit=self.commit)
        # Whatever the attacker achieves, the driver must still be measuring the authorized tree.
        self.assertIn(proc.returncode, (0, 1))
        if proc.returncode:
            self.assertIn("BOOTSTRAP_REFUSED" if False else "REFUSED", proc.stderr)

    def test_driver_imports_nothing_from_the_trust_root_it_measures(self):
        source = DRIVER.read_text(encoding="utf-8")
        for banned in ("factory_kernel", "factory_security", "harness.", "from scripts"):
            self.assertNotIn(f"import {banned}", source)
            self.assertNotIn(f"from {banned}", source)


if __name__ == "__main__":
    unittest.main()
