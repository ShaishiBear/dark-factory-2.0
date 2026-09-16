"""Observation arithmetic must not invent cost, causes, completion or coverage."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes
from factory_kernel.trajectory import summarize
from factory_kernel.trajectory_analysis import build_report, distribution
from factory_kernel.trajectory_report import AnalysisRefused, load_snapshot, main, markdown

REPO = "owner/factory"


def record(run: int = 42, attempt: int = 1, **changes) -> dict:
    source = {"id": run, "run_attempt": attempt, "repository": {"full_name": REPO},
              "path": ".github/workflows/dark-factory-worker.yml", "head_branch": "main",
              "head_sha": "a" * 40, "event": "workflow_dispatch", "status": "completed",
              "conclusion": "failure", "created_at": "2026-09-16T12:00:00Z",
              "updated_at": "2026-09-16T13:00:00Z"}
    result = summarize(source, repository=REPO, directories={}, models=set())
    result.update(changes)
    return result


def stage(ordinal: int = 1, **changes) -> dict:
    result = {"ordinal": ordinal, "kind": "agent", "stage": "test_author", "model": "vendor/model",
              "effort": "medium", "result": "failed", "attempt": 1, "provider_attempts": 1,
              "cost": {"currency": "USD", "amount": 2.0}, "wall_seconds": 30, "turns": 5,
              "started_at": "2026-09-16T12:00:00Z", "ended_at": "2026-09-16T12:00:30Z",
              "termination_flags": {"cap_reached": None, "idle_killed": None, "draft_deadline_missed": None}}
    result.update(changes)
    return result


def with_stages(*stages: dict, run: int = 42, attempt: int = 1, **changes) -> dict:
    return record(run, attempt, attempts=[{"phase": "dispatch", "kernel_run": "issue-7-a1-0123456789",
                                         "stages": list(stages), "gaps": []}], gaps=[], **changes)


class AnalysisTests(unittest.TestCase):
    def report(self, *rows):
        return build_report(list(rows), repository=REPO)

    def test_known_arithmetic_preserves_missing_cost_and_does_not_multiply_retries(self):
        first = stage(provider_attempts=3, wall_seconds=10)
        second = stage(2, attempt=2, result="ok", cost={"currency": "USD", "amount": None}, wall_seconds=20)
        third = stage(3, kind="exec", stage="proof", cost={"currency": "USD", "amount": None}, wall_seconds=50)
        result = self.report(with_stages(first, second, third, outcome="success"))
        total = result["totals"]
        self.assertEqual(total["agent_cost_usd"], {"observed": 1, "missing": 1, "sum": 2.0, "p50": 2.0, "p95": 2.0, "max": 2.0})
        self.assertEqual(total["additional_provider_attempts_recorded"], 2)
        self.assertEqual(result["repeated_stage_observations"], 1)
        self.assertEqual(total["stage_seconds"]["sum"], 80)
        self.assertEqual(result["workflow_elapsed_seconds"]["sum"], 3600)
        self.assertEqual(result["non_ok_stages"][0]["workflow_outcome"], "success")
        self.assertIsNone(result["verified_completions"])
        self.assertIsNone(result["cost_per_verified_completion_usd"])

    def test_null_zero_and_empty_are_different(self):
        self.assertEqual(distribution([None, 0])["sum"], 0)
        self.assertEqual(distribution([None, 0])["missing"], 1)
        self.assertIsNone(distribution([None])["sum"])
        empty = self.report()
        self.assertFalse(empty["coverage"]["input_complete"])
        self.assertIsNone(empty["totals"]["agent_cost_usd"]["sum"])

    def test_nearest_rank_percentiles_and_input_order_are_reproducible(self):
        self.assertEqual(distribution([1, 2, 3, 100])["p50"], 2)
        self.assertEqual(distribution([1, 2, 3, 100])["p95"], 100)
        a, b = with_stages(stage()), record(43)
        self.assertEqual(canonical_bytes(self.report(a, b)), canonical_bytes(self.report(b, a)))

    def test_identical_duplicates_deduplicate_but_reruns_are_distinct(self):
        a = with_stages(stage())
        result = self.report(a, deepcopy(a), with_stages(stage(), attempt=2))
        self.assertEqual(result["coverage"]["identical_duplicates"], 1)
        self.assertEqual(result["coverage"]["accepted_attempts"], 2)
        self.assertEqual(result["totals"]["agent_cost_usd"]["sum"], 4)
        self.assertEqual(result["runs"][1]["url"], "https://github.com/owner/factory/actions/runs/42/attempts/2")

    def test_conflicting_identity_excludes_both_versions(self):
        a, b = with_stages(stage()), with_stages(stage(result="ok"))
        result = self.report(a, b, record(43))
        self.assertEqual(result["coverage"]["accepted_attempts"], 1)
        self.assertEqual(result["coverage"]["conflicting_identities"], 1)
        self.assertFalse(result["coverage"]["input_complete"])
        self.assertEqual(result["totals"]["stage_observations"], 0)

    def test_source_only_success_is_not_verified_completion_or_zero_cost(self):
        result = self.report(record(outcome="success"))
        self.assertEqual(result["workflow_outcomes"], {"success": 1})
        self.assertEqual(result["coverage"]["runs_without_stages"], 1)
        self.assertEqual(result["coverage"]["runs_with_collection_gaps"], 1)
        self.assertIsNone(result["totals"]["agent_cost_usd"]["sum"])
        self.assertIsNone(result["human_interventions"])
        self.assertFalse(result["proof_reuse_allowed"])

    def test_invalid_conflicting_version_cannot_leave_valid_version_counted(self):
        result = self.report(record(), record(authority="merge"))
        self.assertEqual(result["coverage"]["accepted_attempts"], 0)
        self.assertEqual(result["coverage"]["conflicting_identities"], 1)

    def test_real_retention_count_shape_is_projected_without_inventing_failures(self):
        result = self.report(record(evidence_retention=[{
            "phase": "merge", "authority": "observation-only", "proof_reuse_allowed": False,
            "gaps": [], "index": {"gaps": [{"kernel_run": "merge-7-0123456789ab",
                                            "counts": {"absent": 74, "invalid": 0, "over-bound": 0}}]}}]))
        retained = [g for g in result["collection_gaps"] if g["scope"] == "retention-index"]
        self.assertEqual(retained, [{"scope": "retention-index", "phase": "merge", "reason": "retained-files-absent", "count": 74}])
        self.assertEqual(result["totals"]["non_ok_observations"], 0)

    def test_invalid_metadata_is_excluded_not_echoed(self):
        for change in ({"repository": "another/repo"}, {"schema_version": "2.0"},
                       {"authority": "merge"}, {"learning_scope": "global"}, {"run_id": True},
                       {"trajectory_id": "other"}, {"source_revision": "secret"},
                       {"ended_at": "2026-09-15T13:00:00Z"}, {"outcome": "SECRET"},
                       {"source_workflow": "other.yml"}):
            with self.subTest(change=change):
                result = self.report(record(**change))
                self.assertEqual(result["coverage"]["rejected_records"], 1)
                self.assertFalse(result["coverage"]["input_complete"])
                self.assertNotIn("SECRET", json.dumps(result))

    def test_invalid_measurements_and_duplicate_ordinals_refuse_record(self):
        for change in ({"wall_seconds": -1}, {"wall_seconds": float("nan")},
                       {"wall_seconds": True}, {"turns": 1.5}, {"provider_attempts": 0},
                       {"cost": {"currency": "EUR", "amount": 5}},
                       {"termination_flags": {"idle_killed": "yes"}}, {"stage": "<script>"}):
            with self.subTest(change=change):
                self.assertEqual(self.report(with_stages(stage(**change)))["coverage"]["rejected_records"], 1)
        self.assertEqual(self.report(with_stages(stage(), stage()))["coverage"]["rejected_records"], 1)

    def test_retention_gaps_are_observations_and_private_payload_is_ignored(self):
        row = with_stages(stage(result="ok"), evidence_retention=[{
            "phase": "merge", "authority": "observation-only", "proof_reuse_allowed": False,
            "gaps": [{"reason": "artifact-expired", "artifact": "evidence"}],
            "index": {"gaps": [{"reason": "sensitive-description SECRET"}]},
            "private": "SECRET"}])
        row["attempts"][0]["artifact_refs"] = [{"path": "post-merge.json", "body": "SECRET"}]
        row["attempts"][0]["stages"][0]["exception"] = "SECRET"
        result = self.report(row)
        self.assertEqual({g["reason"] for g in result["collection_gaps"]}, {"artifact-expired", "other-recorded-gap"})
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertIsNone(result["verified_completions"])

    def test_repair_flags_and_models_remain_distinct(self):
        a = stage(stage="repair", model=None, result="ok", termination_flags={"draft_deadline_missed": True})
        b = stage(2, stage="repair", model="vendor/other", effort="high", provider_attempts=None)
        result = self.report(with_stages(a, b))
        self.assertEqual(result["totals"]["repair_invocations"], 2)
        self.assertEqual(result["termination_signals"], {"draft_deadline_missed": 1})
        self.assertEqual(result["totals"]["non_ok_observations"], 1)
        self.assertEqual(len(result["by_stage_model"]), 2)


class ReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "42-1.json"
        self.path.write_bytes(canonical_bytes(with_stages(stage())))

    def test_cli_reads_only_snapshot_and_json_and_markdown_agree(self):
        before = self.path.read_bytes()
        output = StringIO()
        with patch("subprocess.run", side_effect=AssertionError("no external operations")), redirect_stdout(output):
            status = main(["--archive", str(self.root), "--repository", REPO, "--format", "json"])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["totals"]["agent_cost_usd"]["sum"], 2)
        self.assertEqual(result["source_files"][0]["file"], "42-1.json")
        rendered = markdown(result)
        self.assertIn("Recorded agent cost: USD 2", rendered)
        self.assertIn("No causal inference", rendered)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_malformed_filename_json_and_duplicate_keys_have_no_partial_report(self):
        for raw in (b'{"run_id":42,"run_id":43}', b'not-json SECRET', b'[]'):
            self.path.write_bytes(raw)
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(["--archive", str(self.root), "--repository", REPO])
            self.assertEqual(status, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn("SECRET", stderr.getvalue())
        self.path.write_bytes(canonical_bytes(record(43)))
        with self.assertRaises(AnalysisRefused):
            load_snapshot(self.root)

    def test_empty_snapshot_is_explicitly_incomplete_and_has_nonzero_exit(self):
        self.path.unlink()
        output = StringIO()
        with redirect_stdout(output):
            status = main(["--archive", str(self.root), "--repository", REPO, "--format", "json"])
        self.assertEqual(status, 2)
        self.assertFalse(json.loads(output.getvalue())["coverage"]["input_complete"])

    def test_symlinks_and_unexpected_entries_are_refused(self):
        (self.root / "extra.txt").write_text("SECRET")
        with self.assertRaises(AnalysisRefused):
            load_snapshot(self.root)
        (self.root / "extra.txt").unlink()
        target = self.root / "43-1.json"
        try:
            target.symlink_to(self.path)
        except OSError:
            self.skipTest("symlink permission unavailable")
        with self.assertRaises(AnalysisRefused):
            load_snapshot(self.root)

    def test_input_bounds_fail_before_reporting(self):
        with patch("factory_kernel.trajectory_report.MAX_RECORD", 2), self.assertRaises(AnalysisRefused):
            load_snapshot(self.root)
        with patch("factory_kernel.trajectory_report.MAX_RECORDS", 0), self.assertRaises(AnalysisRefused):
            load_snapshot(self.root)
        with patch("factory_kernel.trajectory_report.MAX_SNAPSHOT_BYTES", 2), self.assertRaises(AnalysisRefused):
            load_snapshot(self.root)


if __name__ == "__main__":
    unittest.main()
