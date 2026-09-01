"""Adversarial coverage for the genesis collection authority.

A throwaway probe on a real runner disproved the previous boundary: a stage printed an honest
measurement, exited zero, and a detached child it left behind rewrote the driver's structured
result before the upload step read it. Every binding survived; only the measurement changed. So
evidence is no longer assembled from anything a candidate process can reach. It is built on a
separate runner from GitHub's own job record -- conclusions and logs, sealed when each job ended.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
COLLECT = ROOT / "harness" / "genesis_collect.py"
RUN_ID = "4242"
WORKFLOW_COMMIT = "w" * 40
CANDIDATE = "c" * 40
TREE = "t" * 40
NAMES = ("alpha", "beta")


def stage_log(commit: str = CANDIDATE, tree: str = TREE, *, extra: str = "") -> str:
    return (
        f"EXACT_HEAD_OK {commit}\n"
        f"EXACT_TREE_OK {tree}\n"
        "LADDER_PINS_OK\n"
        f"{extra}"
    )


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.recipe = self.root / "recipe.json"
        self.recipe.write_text(json.dumps({
            "version": "1.0",
            "stages": [
                {"name": "alpha", "argv": ["x"], "measures": {"alpha_count": r"A_OK n=(\d+)"}},
                {"name": "beta", "argv": ["y"], "measures": {"beta_count": r"B_OK n=(\d+)"}},
            ],
        }), encoding="utf-8")
        self.job_id = 1000

    def job(self, stage: str, *, conclusion: str = "success", run_id: str = RUN_ID,
            head: str = WORKFLOW_COMMIT, name: str | None = None) -> dict:
        self.job_id += 1
        return {
            "id": self.job_id,
            "run_id": int(run_id),
            "head_sha": head,
            "name": name or f"stage ({stage})",
            "conclusion": conclusion,
        }

    def write_log(self, job: dict, text: str) -> None:
        (self.logs / f"{job['id']}.log").write_text(text, encoding="utf-8")

    def default_jobs(self) -> list[dict]:
        jobs = []
        for stage, marker in (("alpha", "A_OK n=11"), ("beta", "B_OK n=22")):
            job = self.job(stage)
            self.write_log(job, stage_log(extra=marker + "\n"))
            jobs.append(job)
        jobs.append({"id": 9999, "run_id": int(RUN_ID), "head_sha": WORKFLOW_COMMIT,
                     "name": "collect", "conclusion": "success"})
        return jobs

    def collect(self, jobs: list[dict], *, commit: str = CANDIDATE, tree: str = TREE,
                workflow_commit: str = WORKFLOW_COMMIT, run_id: str = RUN_ID):
        record = self.root / "jobs.json"
        record.write_text(json.dumps({"total_count": len(jobs), "jobs": jobs}), encoding="utf-8")
        out = self.root / "validation-result.json"
        proc = subprocess.run(
            [sys.executable, str(COLLECT), "--jobs", str(record), "--logs-dir", str(self.logs),
             "--recipe", str(self.recipe), "--run-id", run_id,
             "--workflow-commit", workflow_commit, "--commit", commit, "--tree", tree,
             "--output", str(out)],
            cwd=self.root, capture_output=True, text=True, timeout=180,
        )
        result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
        return proc, result

    # ---------- the happy path ----------

    def test_complete_successful_run_collects(self):
        proc, result = self.collect(self.default_jobs())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["evidence_source"], "github-actions-job-record")
        self.assertEqual(result["stage_isolation"], "one-disposable-runner-per-stage")
        self.assertEqual(result["candidate_sha"], CANDIDATE)
        self.assertEqual(result["workflow_commit_sha"], WORKFLOW_COMMIT)
        measured = {s["name"]: s["measurements"] for s in result["stages"]}
        self.assertEqual(measured["alpha"]["alpha_count"], 11)
        self.assertEqual(measured["beta"]["beta_count"], 22)

    # ---------- the probe, kept as a regression ----------

    def test_a_detached_child_cannot_rewrite_the_record(self):
        """The probe rewrote a stage-written file. There is no such file to rewrite now.

        The collector's only inputs are GitHub's job record and logs, fetched on a different
        runner after the stage job ended. A stage result file, forged or otherwise, is not
        consulted at all -- so the demonstrated attack has nothing to act on.
        """
        jobs = self.default_jobs()
        forged = self.root / "stage-alpha.json"
        forged.write_text(json.dumps({
            "version": "2.0", "candidate_sha": CANDIDATE, "candidate_tree": TREE,
            "stage": {"name": "alpha", "exit": 0, "measurements": {"alpha_count": 999999}},
        }), encoding="utf-8")
        proc, result = self.collect(jobs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        measured = {s["name"]: s["measurements"] for s in result["stages"]}
        self.assertEqual(measured["alpha"]["alpha_count"], 11, "the forged file must be ignored")
        self.assertNotIn("999999", json.dumps(result))

    def test_a_forged_marker_alongside_the_real_one_is_refused(self):
        """A stage that prints a second, disagreeing value is refused rather than first-matched."""
        jobs = []
        alpha = self.job("alpha")
        self.write_log(alpha, stage_log(extra="A_OK n=999999\nA_OK n=11\n"))
        jobs.append(alpha)
        beta = self.job("beta")
        self.write_log(beta, stage_log(extra="B_OK n=22\n"))
        jobs.append(beta)
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ambiguously", proc.stderr)

    def test_candidate_environment_writes_cannot_reach_the_collector(self):
        """GITHUB_ENV and GITHUB_PATH are stage-job state; the collector never reads them."""
        source = COLLECT.read_text(encoding="utf-8")
        for banned in ("GITHUB_ENV", "GITHUB_PATH", "os.environ", "getenv"):
            self.assertNotIn(banned, source)

    def test_collector_makes_no_network_calls(self):
        """The workflow fetches with read-only authority and scrubs the token before this runs."""
        source = COLLECT.read_text(encoding="utf-8")
        for banned in ("urllib", "requests", "httpx", "socket", "subprocess", "curl"):
            self.assertNotIn(f"import {banned}", source)

    # ---------- missing, duplicate, failed ----------

    def test_missing_stage_job_fails_closed(self):
        jobs = [j for j in self.default_jobs() if j["name"] != "stage (beta)"]
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no stage job for: beta", proc.stderr)

    def test_duplicate_stage_job_fails_closed(self):
        jobs = self.default_jobs()
        extra = self.job("alpha")
        self.write_log(extra, stage_log(extra="A_OK n=999999\n"))
        jobs.append(extra)
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("more than one job", proc.stderr)

    def test_failed_stage_job_fails_closed(self):
        jobs = []
        alpha = self.job("alpha", conclusion="failure")
        self.write_log(alpha, stage_log(extra="A_OK n=11\n"))
        jobs.append(alpha)
        beta = self.job("beta")
        self.write_log(beta, stage_log(extra="B_OK n=22\n"))
        jobs.append(beta)
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("concluded 'failure'", proc.stderr)

    def test_stage_job_the_recipe_does_not_define_fails_closed(self):
        jobs = self.default_jobs()
        smuggled = self.job("gamma")
        self.write_log(smuggled, stage_log())
        jobs.append(smuggled)
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("recipe does not define: gamma", proc.stderr)

    # ---------- identity ----------

    def test_stage_job_from_another_run_fails_closed(self):
        jobs = []
        alpha = self.job("alpha", run_id="9999")
        self.write_log(alpha, stage_log(extra="A_OK n=11\n"))
        jobs.append(alpha)
        beta = self.job("beta")
        self.write_log(beta, stage_log(extra="B_OK n=22\n"))
        jobs.append(beta)
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("different workflow run", proc.stderr)

    def test_stage_job_from_another_workflow_commit_fails_closed(self):
        jobs = []
        alpha = self.job("alpha", head="9" * 40)
        self.write_log(alpha, stage_log(extra="A_OK n=11\n"))
        jobs.append(alpha)
        beta = self.job("beta")
        self.write_log(beta, stage_log(extra="B_OK n=22\n"))
        jobs.append(beta)
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("authorized workflow commit", proc.stderr)

    def test_log_not_proving_the_candidate_head_fails_closed(self):
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        self.write_log(alpha, stage_log(commit="9" * 40, extra="A_OK n=11\n"))
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("authorized candidate", proc.stderr)

    def test_log_not_proving_the_candidate_tree_fails_closed(self):
        """The checkout could be modified after the pin check; the tree assertion is the record."""
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        self.write_log(alpha, stage_log(tree="9" * 40, extra="A_OK n=11\n"))
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("authorized tree", proc.stderr)

    def test_absent_log_fails_closed(self):
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        (self.logs / f"{alpha['id']}.log").unlink()
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no GitHub log was captured", proc.stderr)

    def test_absent_measurement_fails_closed(self):
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        self.write_log(alpha, stage_log())
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not report alpha_count", proc.stderr)


if __name__ == "__main__":
    unittest.main()
