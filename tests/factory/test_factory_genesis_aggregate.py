"""Adversarial coverage for the genesis aggregation authority.

Fanning validation out to one disposable runner per stage buys isolation and costs an obligation:
the pieces must be reassembled without anything going missing, arriving twice, or coming from
somewhere else. A stage that never ran and a stage whose artifact was dropped look identical
downstream unless something insists on the full set.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
AGGREGATE = ROOT / "harness" / "genesis_aggregate.py"
COMMIT = "a" * 40
TREE = "b" * 40
DRIVER_SHA = "c" * 64
NAMES = ("alpha", "beta", "gamma")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recipe = self.root / "recipe.json"
        self.recipe.write_text(
            json.dumps({"version": "1.0", "stages": [{"name": n, "argv": ["x"]} for n in NAMES]}),
            encoding="utf-8",
        )
        self.recipe_sha = sha256(self.recipe.read_bytes())
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()

    def artifact(self, name: str, **over) -> Path:
        value = {
            "version": "2.0",
            "driver_sha256": DRIVER_SHA,
            "recipe_sha256": self.recipe_sha,
            "candidate_sha": COMMIT,
            "candidate_tree": TREE,
            "stage": {"name": name, "argv": ["x"], "cwd": ".", "exit": 0,
                      "measurements": {f"{name}_count": 1}, "output_sha256": "0" * 64},
        }
        value.update(over)
        path = self.artifacts / f"stage-{name}-{len(list(self.artifacts.iterdir()))}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def aggregate(self, *, commit: str = COMMIT):
        out = self.root / "validation-result.json"
        proc = subprocess.run(
            [sys.executable, str(AGGREGATE), "--recipe", str(self.recipe),
             "--commit", commit, "--stage-results", str(self.artifacts), "--output", str(out)],
            cwd=self.root, capture_output=True, text=True, timeout=180,
        )
        result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
        return proc, result

    def test_complete_set_aggregates(self):
        for name in NAMES:
            self.artifact(name)
        proc, result = self.aggregate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual([s["name"] for s in result["stages"]], list(NAMES))
        self.assertEqual(result["stage_isolation"], "one-disposable-runner-per-stage")
        self.assertEqual(result["candidate_tree"], TREE)
        self.assertEqual(len(result["aggregator_sha256"]), 64)

    def test_missing_stage_artifact_fails_closed(self):
        for name in NAMES[:-1]:
            self.artifact(name)
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing for: gamma", proc.stderr)

    def test_duplicate_stage_artifact_fails_closed(self):
        for name in NAMES:
            self.artifact(name)
        self.artifact("beta")
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("reported more than once", proc.stderr)

    def test_no_artifacts_at_all_fails_closed(self):
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no stage artifacts", proc.stderr)

    def test_stage_the_recipe_does_not_define_fails_closed(self):
        for name in NAMES:
            self.artifact(name)
        self.artifact("smuggled")
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not define", proc.stderr)

    def test_artifact_from_another_commit_fails_closed(self):
        for name in NAMES[:-1]:
            self.artifact(name)
        self.artifact("gamma", candidate_sha="9" * 40)
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("different commit", proc.stderr)

    def test_artifact_from_another_tree_fails_closed(self):
        for name in NAMES[:-1]:
            self.artifact(name)
        self.artifact("gamma", candidate_tree="9" * 40)
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("disagrees on the driver digest or candidate tree", proc.stderr)

    def test_artifact_from_another_driver_fails_closed(self):
        for name in NAMES[:-1]:
            self.artifact(name)
        self.artifact("gamma", driver_sha256="9" * 64)
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("disagrees on the driver digest or candidate tree", proc.stderr)

    def test_artifact_from_another_recipe_fails_closed(self):
        for name in NAMES[:-1]:
            self.artifact(name)
        self.artifact("gamma", recipe_sha256="9" * 64)
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not execute the recipe", proc.stderr)

    def test_failed_stage_fails_closed(self):
        for name in NAMES[:-1]:
            self.artifact(name)
        self.artifact("gamma", stage={"name": "gamma", "argv": ["x"], "cwd": ".", "exit": 1,
                                      "measurements": {}, "output_sha256": "0" * 64})
        proc, result = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(result["verdict"], "fail")
        self.assertIn("gamma", result["failed_stages"])

    def test_unsupported_artifact_version_fails_closed(self):
        for name in NAMES[:-1]:
            self.artifact(name)
        self.artifact("gamma", version="1.0")
        proc, _ = self.aggregate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unsupported version", proc.stderr)

    def test_aggregator_imports_nothing_from_the_trust_root(self):
        source = AGGREGATE.read_text(encoding="utf-8")
        for banned in ("factory_kernel", "factory_security", "harness.", "from scripts"):
            self.assertNotIn(f"import {banned}", source)
            self.assertNotIn(f"from {banned}", source)


if __name__ == "__main__":
    unittest.main()
