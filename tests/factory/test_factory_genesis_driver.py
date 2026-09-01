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

    def test_clean_authorized_checkout_is_accepted(self):
        self.assertEqual(self.driver.assert_environment(self.repo, self.commit), self.tree)

    def test_dirty_checkout_fails_closed(self):
        (self.repo / "a.txt").write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.driver.assert_environment(self.repo, self.commit)

    def test_untracked_file_fails_closed(self):
        (self.repo / "planted.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.driver.assert_environment(self.repo, self.commit)

    def test_checkout_at_another_commit_fails_closed(self):
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "two")
        with self.assertRaises(SystemExit):
            self.driver.assert_environment(self.repo, self.commit)

    def test_non_repository_fails_closed(self):
        with self.assertRaises(SystemExit):
            self.driver.assert_environment(Path(self.tmp.name), self.commit)


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
        """Commit the stage script: the driver refuses to run against a dirty checkout."""
        path = self.repo / f"{name}.py"
        path.write_text(script, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", f"stage {name}")
        self.commit = git(self.repo, "rev-parse", "HEAD").strip()
        stage = {"name": name, "argv": [sys.executable, f"{name}.py"], "timeout_seconds": 120}
        if measures:
            stage["measures"] = measures
        return stage

    def drive(self, stages: list[dict], *, commit: str | None = None, stage: str | None = None):
        """Run exactly one stage, the only thing the driver can do."""
        recipe = self.root / "recipe.json"
        recipe.write_text(json.dumps({"version": "1.0", "stages": stages}), encoding="utf-8")
        name = stage or (stages[-1]["name"] if stages else "")
        out = self.root / f"stage-{name}.json"
        proc = subprocess.run(
            [
                sys.executable, str(DRIVER), "--repo", str(self.repo),
                "--commit", commit or self.commit, "--recipe", str(recipe),
                "--stage", name, "--log-dir", str(self.root / "logs"), "--output", str(out),
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
        self.assertEqual(result["stage"]["measurements"]["n"], 42)
        self.assertEqual(result["candidate_sha"], self.commit)

    def test_exit_status_is_the_verdict_not_the_printed_text(self):
        """A stage may print a perfect marker and still have failed."""
        proc, result = self.drive([
            self.stage("liar", "print('COUNT_OK n=42')\nraise SystemExit(1)",
                       {"n": r"COUNT_OK n=(\d+)"})
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(result["stage"]["exit"], 1)
        self.assertEqual(result["stage"]["measurements"], {})

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
        self.assertEqual(result["stage"]["measurements"]["n"], 7)

    def test_absent_marker_is_refused(self):
        proc, _ = self.drive([self.stage("silent", "pass", {"n": r"COUNT_OK n=(\d+)"})])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not report", proc.stderr)

    def test_wrong_repository_head_is_refused(self):
        proc, _ = self.drive([self.stage("ok", "pass")], commit="0" * 40)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the commit being validated", proc.stderr)

    def test_empty_recipe_is_refused(self):
        proc, _ = self.drive([], stage="anything")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no stages", proc.stderr)

    def test_result_binds_driver_recipe_and_commit(self):
        proc, result = self.drive([self.stage("ok", "pass")])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import hashlib

        self.assertEqual(result["driver_sha256"], hashlib.sha256(DRIVER.read_bytes()).hexdigest())
        self.assertEqual(len(result["recipe_sha256"]), 64)
        self.assertEqual(result["candidate_sha"], self.commit)

    # ---------- isolation is structural: the driver cannot sequence stages ----------

    def test_driver_runs_exactly_one_named_stage(self):
        """Two stages cannot share a process, a checkout or a machine if only one ever runs."""
        first = self.stage("first", "print('A_OK n=1')", {"n": r"A_OK n=(\d+)"})
        second = self.stage("second", "print('B_OK n=2')", {"n": r"B_OK n=(\d+)"})
        proc, result = self.drive([first, second], stage="second")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result["stage"]["name"], "second")
        self.assertEqual(result["stage"]["measurements"]["n"], 2)
        self.assertNotIn("stages", result)

    def test_driver_requires_a_stage_and_refuses_an_unknown_one(self):
        only = self.stage("only", "pass")
        proc, _ = self.drive([only], stage="ghost")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no stage named", proc.stderr)
        bare = subprocess.run(
            [sys.executable, str(DRIVER), "--repo", str(self.repo), "--commit", self.commit,
             "--recipe", str(self.root / "recipe.json"), "--log-dir", str(self.root / "logs"),
             "--output", str(self.root / "x.json")],
            cwd=self.root, capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(bare.returncode, 0)
        self.assertIn("--stage", bare.stderr)

    def test_driver_sets_no_cache_environment_of_its_own(self):
        """No cross-stage cache: the driver never points a package manager at shared state."""
        source = DRIVER.read_text(encoding="utf-8")
        for banned in ("UV_CACHE_DIR", "BUN_INSTALL_CACHE_DIR", "XDG_CACHE_HOME", "PIP_CACHE_DIR"):
            self.assertNotIn(f'"{banned}"', source, banned)
            self.assertNotIn(f"'{banned}'", source, banned)

    def test_driver_claims_no_unverified_cache_property(self):
        """An authority must not assert a verification it does not perform."""
        source = DRIVER.read_text(encoding="utf-8")
        self.assertNotIn("caches are hash-checked", source)
        self.assertNotIn("contents are verified against the committed lockfiles", source)

    def test_dirty_checkout_fails_closed(self):
        only = self.stage("only", "pass")
        (self.repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")
        proc, _ = self.drive([only])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a clean checkout", proc.stderr)

    def test_stage_result_binds_commit_tree_driver_and_recipe(self):
        only = self.stage("only", "pass")
        proc, result = self.drive([only])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tree = git(self.repo, "rev-parse", "HEAD^{tree}").strip()
        self.assertEqual(result["candidate_sha"], self.commit)
        self.assertEqual(result["candidate_tree"], tree)
        self.assertEqual(len(result["driver_sha256"]), 64)
        self.assertEqual(len(result["recipe_sha256"]), 64)

    def test_driver_imports_nothing_from_the_trust_root_it_measures(self):
        source = DRIVER.read_text(encoding="utf-8")
        for banned in ("factory_kernel", "factory_security", "harness.", "from scripts"):
            self.assertNotIn(f"import {banned}", source)
            self.assertNotIn(f"from {banned}", source)

class RecordReachabilityTests(unittest.TestCase):
    """The measurement has to be in the record the collector reads, or the design does nothing.

    Evidence is assembled on a separate runner from GitHub's sealed log of the stage job. The
    driver used to capture the stage's output into a scratch file under --log-dir and print only
    its own summary line, so a live twenty-minute mutation stage produced a job log with no
    measurement in it at all. Every binding was intact and the whole ladder would still have been
    refused at collection. This is the seam between the two programs, tested across it rather than
    on either side of it.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "d@example.invalid")
        git(self.repo, "config", "user.name", "D")
        (self.repo / "prog.py").write_text(
            "print('COUNT_OK n=42')\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "one")
        self.commit = git(self.repo, "rev-parse", "HEAD").strip()
        self.tree = git(self.repo, "rev-parse", "HEAD^{tree}").strip()
        self.recipe = self.root / "recipe.json"
        self.recipe.write_text(json.dumps({"version": "1.0", "stages": [
            {"name": "only", "argv": [sys.executable, "prog.py"],
             "measures": {"n": r"COUNT_OK n=(\d+)"}},
        ]}), encoding="utf-8")

    def run_driver(self):
        return subprocess.run(
            [sys.executable, str(DRIVER), "--repo", str(self.repo), "--commit", self.commit,
             "--recipe", str(self.recipe), "--stage", "only",
             "--log-dir", str(self.root / "logs"), "--output", str(self.root / "out.json")],
            cwd=self.root, capture_output=True, text=True, timeout=600,
        )

    def test_the_stage_output_reaches_the_drivers_stdout(self):
        proc = self.run_driver()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("COUNT_OK n=42", proc.stdout)
        self.assertIn("STAGE_OUTPUT_BEGIN only", proc.stdout)
        self.assertIn("STAGE_OUTPUT_END only", proc.stdout)

    def test_the_framing_encloses_the_stage_text_and_not_the_drivers_own_lines(self):
        proc = self.run_driver()
        begin = proc.stdout.index("STAGE_OUTPUT_BEGIN only")
        end = proc.stdout.index("STAGE_OUTPUT_END only")
        enclosed = proc.stdout[begin:end]
        self.assertIn("COUNT_OK n=42", enclosed)
        self.assertNotIn("STAGE_RESULT", enclosed)
        self.assertNotIn("STAGE_OK", enclosed)
        self.assertLess(end, proc.stdout.index("STAGE_RESULT"))

    def test_the_collector_reads_the_same_measurement_from_that_stdout(self):
        """The seam, crossed: what the driver emits is what the collector must be able to read."""
        proc = self.run_driver()
        self.assertEqual(proc.returncode, 0, proc.stderr)

        logs = self.root / "joblogs"
        logs.mkdir()
        job_log = (
            f"EXACT_HEAD_OK {self.commit}\n"
            f"EXACT_TREE_OK {self.tree}\n"
            "LADDER_PINS_OK driver=a recipe=b\n"
        ) + proc.stdout
        (logs / "77.log").write_text(job_log, encoding="utf-8")
        jobs = self.root / "jobs.json"
        jobs.write_text(json.dumps({"total_count": 1, "jobs": [
            {"id": 77, "run_id": 5, "head_sha": "w" * 40, "run_attempt": 1,
             "name": "stage (only)", "conclusion": "success"},
        ]}), encoding="utf-8")
        out = self.root / "collected.json"
        collected = subprocess.run(
            [sys.executable, str(ROOT / "harness" / "genesis_collect.py"),
             "--jobs", str(jobs), "--logs-dir", str(logs), "--recipe", str(self.recipe),
             "--run-id", "5", "--workflow-commit", "w" * 40, "--commit", self.commit,
             "--tree", self.tree, "--output", str(out)],
            cwd=self.root, capture_output=True, text=True, timeout=600,
        )
        self.assertEqual(collected.returncode, 0, collected.stderr)
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(document["verdict"], "pass")
        self.assertEqual(document["stages"][0]["measurements"]["n"], 42)

        driver_said = json.loads((self.root / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(
            driver_said["stage"]["measurements"]["n"],
            document["stages"][0]["measurements"]["n"],
            "the two independent parses of the same stage must agree",
        )



if __name__ == "__main__":
    unittest.main()
