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
            head: str = WORKFLOW_COMMIT, name: str | None = None,
            attempt: int | None = 1) -> dict:
        self.job_id += 1
        record = {
            "id": self.job_id,
            "run_id": int(run_id),
            "head_sha": head,
            "name": name or f"stage ({stage})",
            "conclusion": conclusion,
        }
        if attempt is not None:
            record["run_attempt"] = attempt
        return record

    def write_log(self, job: dict, text: str) -> None:
        (self.logs / f"{job['id']}.log").write_text(text, encoding="utf-8")

    def default_jobs(self) -> list[dict]:
        jobs = []
        for stage, marker in (("alpha", "A_OK n=11"), ("beta", "B_OK n=22")):
            job = self.job(stage)
            self.write_log(job, stage_log(extra=marker + "\n"))
            jobs.append(job)
        jobs.append({"id": 9999, "run_id": int(RUN_ID), "head_sha": WORKFLOW_COMMIT,
                     "run_attempt": 1, "name": "collect", "conclusion": "success"})
        return jobs

    def collect(self, jobs: list[dict], *, commit: str = CANDIDATE, tree: str = TREE,
                workflow_commit: str = WORKFLOW_COMMIT, run_id: str = RUN_ID,
                total_count: int | None = None):
        record = self.root / "jobs.json"
        stated = len(jobs) if total_count is None else total_count
        record.write_text(json.dumps({"total_count": stated, "jobs": jobs}), encoding="utf-8")
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

    # ---------- the record has to be the whole record ----------

    def test_a_truncated_job_listing_fails_closed(self):
        """A listing read only to page one can hide the honest half of a duplicate pair.

        The duplicate guard can only fire on jobs it can see. If a forged `stage (alpha)` sits on
        page one and the honest one is stranded on page two, the guard never sees two of them --
        so a listing that admits to more jobs than it carries is refused before that guard runs.
        """
        jobs = self.default_jobs()
        proc, result = self.collect(jobs, total_count=len(jobs) + 1)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("truncated", proc.stderr)
        self.assertIsNone(result, "a truncated record must not produce a document at all")

    def test_a_job_listing_that_states_no_total_fails_closed(self):
        jobs = self.default_jobs()
        proc, _ = self.collect(jobs, total_count="12")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("truncated", proc.stderr)

    def test_stages_from_different_run_attempts_fail_closed(self):
        """Partial re-runs are a re-roll channel: retry the one stage that failed until it passes.

        GitHub returns the latest attempt of each job, so a ladder re-run stage-by-stage would
        present every stage as successful. Requiring one attempt across the ladder closes it.
        """
        jobs = []
        alpha = self.job("alpha", attempt=1)
        self.write_log(alpha, stage_log(extra="A_OK n=11\n"))
        jobs.append(alpha)
        beta = self.job("beta", attempt=2)
        self.write_log(beta, stage_log(extra="B_OK n=22\n"))
        jobs.append(beta)
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("more than one run attempt", proc.stderr)

    def test_a_stage_job_without_a_run_attempt_fails_closed(self):
        jobs = []
        alpha = self.job("alpha", attempt=None)
        self.write_log(alpha, stage_log(extra="A_OK n=11\n"))
        jobs.append(alpha)
        beta = self.job("beta")
        self.write_log(beta, stage_log(extra="B_OK n=22\n"))
        jobs.append(beta)
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("run attempt", proc.stderr)

    def test_the_attempt_that_produced_the_evidence_is_recorded(self):
        jobs = []
        for stage, marker in (("alpha", "A_OK n=11"), ("beta", "B_OK n=22")):
            job = self.job(stage, attempt=3)
            self.write_log(job, stage_log(extra=marker + "\n"))
            jobs.append(job)
        jobs.append({"id": 9999, "run_id": int(RUN_ID), "head_sha": WORKFLOW_COMMIT,
                     "run_attempt": 3, "name": "collect", "conclusion": "success"})
        proc, result = self.collect(jobs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result["workflow_run_attempt"], 3)

    # ---------- a failed run must leave evidence of why ----------

    def test_a_failed_run_still_produces_an_auditable_document(self):
        """The workflow claims a failed run leaves auditable evidence. It has to be true.

        An earlier collector refused at the first bad stage and wrote nothing, so the workflow's
        `if: always()` collect job produced `(no result produced)` -- the comment asserted a
        property the code did not have.
        """
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        alpha["conclusion"] = "failure"
        proc, result = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIsNotNone(result, "a failed run must still write the document")
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual([f["name"] for f in result["failed_stages"]], ["alpha"])
        self.assertIn("not success", " ".join(result["failed_stages"][0]["reasons"]))

    def test_a_failed_stage_records_a_nonzero_exit(self):
        """The verifier checks each stage exit as well as the verdict; both must reject the run."""
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        alpha["conclusion"] = "failure"
        _, result = self.collect(jobs)
        by_name = {s["name"]: s for s in result["stages"]}
        self.assertEqual(by_name["alpha"]["exit"], 1)
        self.assertEqual(by_name["beta"]["exit"], 0)

    def test_every_failing_stage_is_reported_not_only_the_first(self):
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        beta = next(j for j in jobs if j["name"] == "stage (beta)")
        alpha["conclusion"] = "failure"
        self.write_log(beta, stage_log(tree="9" * 40, extra="B_OK n=22\n"))
        proc, result = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(sorted(f["name"] for f in result["failed_stages"]), ["alpha", "beta"])

    def test_a_structural_refusal_writes_no_document(self):
        """A record that is not this run's record must not be turned into evidence of anything."""
        jobs = self.default_jobs()
        for job in jobs:
            job["run_id"] = 9999
        proc, result = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIsNone(result)

    # ---------- the log binding is to the bytes GitHub served ----------

    def test_runner_escape_sequences_do_not_break_collection(self):
        """Real runner logs carry ANSI colour on every echoed command line.

        A live run proved this is not hypothetical: the fetch step used `gh api`, which refuses
        to write a body containing terminal escape sequences, and the collect job died before the
        collector ever ran. The collector itself must parse such a log unharmed.
        """
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        coloured = (
            "\x1b[36;1mecho \"EXACT_HEAD_OK $head\"\x1b[0m\n"
            + stage_log(extra="\x1b[0;32mA_OK n=11\x1b[0m\n")
        )
        self.write_log(alpha, coloured)
        proc, result = self.collect(jobs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        measured = {s["name"]: s["measurements"] for s in result["stages"]}
        self.assertEqual(measured["alpha"]["alpha_count"], 11)

    def test_the_recorded_log_digest_is_over_the_bytes_github_served(self):
        """Digesting the lossily-decoded text would bind to a transformation, not to the record."""
        import hashlib
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        raw = (stage_log(extra="A_OK n=11\n")).encode() + b"\xff\xfe undecodable\n"
        (self.logs / f"{alpha['id']}.log").write_bytes(raw)
        proc, result = self.collect(jobs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        by_name = {s["name"]: s for s in result["stages"]}
        self.assertEqual(by_name["alpha"]["output_sha256"], hashlib.sha256(raw).hexdigest())


    # ---------- which program ran, not just where ----------

    def test_a_stage_silent_about_the_driver_pins_fails_closed(self):
        """Head and tree prove where the stage ran. They do not prove what executed there."""
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        self.write_log(alpha, f"EXACT_HEAD_OK {CANDIDATE}\nEXACT_TREE_OK {TREE}\nA_OK n=11\n")
        proc, result = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("whether the driver pins were checked", proc.stderr)
        self.assertEqual(result["verdict"], "fail")

    def test_a_stage_claiming_both_pin_verdicts_fails_closed(self):
        """Printing the asserted marker next to a provisional one is a collision, not a pass."""
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        self.write_log(alpha, stage_log(extra="LADDER_PINS_PROVISIONAL\nA_OK n=11\n"))
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("both asserts and waives", proc.stderr)

    def test_a_provisional_run_collects_but_records_that_pins_were_not_asserted(self):
        """A calibration run is honest and must complete; the policy is what refuses it."""
        jobs = []
        for stage, marker in (("alpha", "A_OK n=11"), ("beta", "B_OK n=22")):
            job = self.job(stage)
            self.write_log(job, f"EXACT_HEAD_OK {CANDIDATE}\nEXACT_TREE_OK {TREE}\n"
                                f"LADDER_PINS_PROVISIONAL\n{marker}\n")
            jobs.append(job)
        jobs.append({"id": 9999, "run_id": int(RUN_ID), "head_sha": WORKFLOW_COMMIT,
                     "run_attempt": 1, "name": "collect", "conclusion": "success"})
        proc, result = self.collect(jobs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result["verdict"], "pass")
        self.assertIs(result["driver_pins_asserted"], False)

    def test_one_provisional_stage_is_enough_to_deny_the_asserted_claim(self):
        jobs = self.default_jobs()
        beta = next(j for j in jobs if j["name"] == "stage (beta)")
        self.write_log(beta, f"EXACT_HEAD_OK {CANDIDATE}\nEXACT_TREE_OK {TREE}\n"
                             "LADDER_PINS_PROVISIONAL\nB_OK n=22\n")
        proc, result = self.collect(jobs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIs(result["driver_pins_asserted"], False)

    def test_a_fully_asserted_run_records_the_claim(self):
        proc, result = self.collect(self.default_jobs())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIs(result["driver_pins_asserted"], True)


    def test_a_log_shaped_like_a_real_runner_log_collects(self):
        """The runner echoes each step's script into the log it also writes output to.

        This nearly defeated the pin-verdict check: a step whose source contained both
        `LADDER_PINS_OK` and `LADDER_PINS_PROVISIONAL` would put both literals into every stage
        log regardless of which branch ran, and every stage would be read as claiming both. The
        workflow assembles the marker suffix from a shell variable so its source contains
        neither. This fixture reproduces that log shape, echoed script included.
        """
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        self.write_log(alpha, (
            '\x1b[36;1mecho "EXACT_HEAD_OK $head"\x1b[0m\n'
            '\x1b[36;1m  verdict=PROVISIONAL\x1b[0m\n'
            '\x1b[36;1m    verdict=OK\x1b[0m\n'
            '\x1b[36;1m  echo "LADDER_PINS_${verdict} driver=$driver"\x1b[0m\n'
            f"EXACT_HEAD_OK {CANDIDATE}\n"
            f"EXACT_TREE_OK {TREE}\n"
            "LADDER_PINS_OK driver=abc recipe=def\n"
            "A_OK n=11\n"
        ))
        proc, result = self.collect(jobs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIs(result["driver_pins_asserted"], True)
        measured = {s["name"]: s["measurements"] for s in result["stages"]}
        self.assertEqual(measured["alpha"]["alpha_count"], 11)

    def test_a_workflow_that_echoes_both_markers_is_caught_not_guessed(self):
        """The shape the old pin step would have produced. It must refuse, not pick one."""
        jobs = self.default_jobs()
        alpha = next(j for j in jobs if j["name"] == "stage (alpha)")
        self.write_log(alpha, (
            f"EXACT_HEAD_OK {CANDIDATE}\n"
            f"EXACT_TREE_OK {TREE}\n"
            '\x1b[36;1m    echo "LADDER_PINS_OK"\x1b[0m\n'
            '\x1b[36;1m    echo "LADDER_PINS_PROVISIONAL digests printed for review"\x1b[0m\n'
            "LADDER_PINS_PROVISIONAL digests printed for review\n"
            "A_OK n=11\n"
        ))
        proc, _ = self.collect(jobs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("both asserts and waives", proc.stderr)



if __name__ == "__main__":
    unittest.main()
