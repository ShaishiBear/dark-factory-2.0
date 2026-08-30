"""Adversarial coverage for the external genesis validation driver.

The driver exists because searching a shared log is not measurement. Candidate programs write to
the same stream, so an unconstrained regex over an aggregated log let one early line decide what
the authority believed. These tests pin the properties that replace it: exit status is the
verdict, each count is read from its own stage's output, and ambiguity is refused rather than
resolved by taking the first match.
"""
from __future__ import annotations

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
        path = self.repo / f"{name}.py"
        path.write_text(script, encoding="utf-8")
        stage = {"name": name, "argv": [sys.executable, f"{name}.py"], "timeout_seconds": 60}
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
                "--log-dir", str(self.root / "logs"), "--output", str(out),
            ],
            cwd=self.root, capture_output=True, text=True, timeout=300,
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

    def test_driver_imports_nothing_from_the_trust_root_it_measures(self):
        source = DRIVER.read_text(encoding="utf-8")
        for banned in ("factory_kernel", "factory_security", "harness.", "from scripts"):
            self.assertNotIn(f"import {banned}", source)
            self.assertNotIn(f"from {banned}", source)


if __name__ == "__main__":
    unittest.main()
