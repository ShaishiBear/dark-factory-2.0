"""Retained evidence is complete enough to explain, bounded, and never a proof upgrade."""
from copy import deepcopy
import base64
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import canonical_bytes, sha256_bytes
from factory_kernel.claim_explanation import explain_run
from factory_kernel.evidence_retention import EVIDENCE_PATHS, safe_index, source_binding, stage
from factory_kernel.evidence_retention_archive import artifact_observation, collect_retention
from factory_kernel.trajectory import TrajectoryRefused
from factory_kernel.trajectory_archive import collect
from tests.factory import test_factory_evidence_closure as closure
from tests.factory.test_factory_trajectory import REPOSITORY, source

ROOT = Path(__file__).parents[2]
ATTEMPT = "issue-181-a1-0123456789"
BINDING = source_binding(repository=REPOSITORY, run_id=42, attempt=1, source_revision="a" * 40, phase="dispatch")
CHECKOUT = {"checkout_revision": "b" * 40, "trust_root_tree_sha256": "c" * 64, "python_version": "3.12.0"}


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runs = self.root / "runs"
        self.artifacts = self.runs / ATTEMPT / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.output = self.root / "retained"
        self.index_dir = self.root / "index"

    def retain(self):
        return stage(runs=self.runs, destination=self.output, index_directory=self.index_dir,
                     binding=BINDING, checkout_observation=CHECKOUT)

    def write(self, name, raw=b'{"private":"SECRET raw evidence"}'):
        path = self.artifacts / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_complete_closure_survives_byte_exact_and_still_cannot_be_reused(self):
        fixture = closure.EvidenceClosureTests()
        pack, legacy = fixture.fixture(self.artifacts)
        with patch("factory_kernel.evidence_closure._load_immunity", return_value=closure.IMMUNITY_RESULT):
            manifest, index = fixture.close(self.artifacts, pack, legacy, fixture.certificates())
        self.write("evidence-bundle.json", canonical_bytes({**legacy, "spine": index, "run_manifest_sha256": manifest.sha256()}))
        receipt = self.retain()
        for row in receipt["files"]:
            self.assertEqual((self.output / row["path"]).read_bytes(), (self.runs / row["path"]).read_bytes())
            self.assertEqual(sha256_bytes((self.output / row["path"]).read_bytes()), row["sha256"])
        args = {"policy_path": ROOT / ".factory/evidence-spine.json", "expected_head_sha": closure.HEAD,
                "expected_base_sha": closure.BASE, "current_claim_hashes": {c.claim_id: c.artifact.sha256 for c in manifest.claims}}
        original = explain_run(artifact_root=self.artifacts, **args)
        retained = explain_run(artifact_root=self.output / ATTEMPT / "artifacts", **args)
        self.assertEqual(retained, original)
        self.assertEqual(len(retained["claims"]), 21)
        self.assertTrue(all(row["recorded_dependency_status"] == "current" for row in retained["claims"]))
        self.assertIs(retained["proof_reuse_allowed"], False)
        self.assertTrue(all(row["evidence_status"] == "insufficient" for row in retained["claims"]))

    def test_only_fixed_evidence_paths_are_copied_and_index_contains_no_contents(self):
        for name in ("spine/run-manifest.json", "independent/design.json", "spine/validator/immunity-verification.json"):
            self.write(name)
        for name in ("spine/private.json", "transcripts/agent-plan.json", "credential.json", "spine/builder/.env"):
            self.write(name)
        receipt = self.retain()
        self.assertEqual(len(receipt["files"]), 3)
        self.assertNotIn("SECRET", (self.index_dir / "retention-index.json").read_text())
        self.assertEqual(len(list(self.output.rglob("*.json"))), 3)
        self.assertNotEqual(receipt["source"]["source_revision"], receipt["checkout_observation"]["checkout_revision"])

    def test_missing_nested_records_stay_missing_without_reconstruction(self):
        self.write("evidence-bundle.json")
        receipt = self.retain()
        self.assertEqual(len(receipt["files"]), 1)
        self.assertGreater(receipt["gaps"][0]["counts"]["absent"], 0)
        self.assertFalse((self.output / ATTEMPT / "artifacts/spine/run-manifest.json").exists())

    def test_existing_validation_refusal_survives_without_entering_public_index(self):
        raw = b'{"detail":"PRIVATE refusal observation","reason_code":"code_holdout"}'
        self.write("validation-refusal.json", raw)
        receipt = self.retain()
        self.assertEqual(len(receipt["files"]), 1)
        self.assertEqual((self.output / ATTEMPT / "artifacts/validation-refusal.json").read_bytes(), raw)
        self.assertNotIn("PRIVATE", (self.index_dir / "retention-index.json").read_text())

    def test_invalid_duplicate_nonobject_and_oversized_json_are_not_retained(self):
        for name, raw in zip(sorted(EVIDENCE_PATHS), (b'{"x":1,"x":2}', b'[]', b'broken', b'{"x":"' + b'a' * 250000 + b'"}')):
            self.write(name, raw)
        receipt = self.retain()
        self.assertEqual(receipt["files"], [])
        self.assertEqual(receipt["gaps"][0]["counts"]["invalid"], 4)

    def test_symlinked_parent_cannot_read_outside_artifact_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "run-manifest.json").write_text('{"private":"SECRET"}')
        (self.artifacts / "spine").symlink_to(outside, target_is_directory=True)
        receipt = self.retain()
        self.assertEqual(receipt["files"], [])
        self.assertGreater(receipt["gaps"][0]["counts"]["invalid"], 0)

    def test_supplied_symlink_root_is_refused(self):
        link = self.root / "linked-runs"
        link.symlink_to(self.runs, target_is_directory=True)
        with self.assertRaises(TrajectoryRefused):
            stage(runs=link, destination=self.output, index_directory=self.index_dir,
                  binding=BINDING, checkout_observation=CHECKOUT)

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(TrajectoryRefused):
            self.retain()

    def test_total_byte_and_file_limits_record_omissions(self):
        for name in sorted(EVIDENCE_PATHS)[:4]:
            self.write(name)
        with patch("factory_kernel.evidence_retention.MAX_FILES", 2):
            receipt = self.retain()
        self.assertEqual(len(receipt["files"]), 2)
        self.assertEqual(receipt["gaps"][0]["counts"]["over-bound"], 2)

    def test_total_bytes_are_bounded_separately(self):
        for name in sorted(EVIDENCE_PATHS)[:4]:
            self.write(name, b'{}')
        with patch("factory_kernel.evidence_retention.MAX_BYTES", 5):
            try:
                receipt = self.retain()
            except TrajectoryRefused:
                self.fail("size-limited staging must finish with explicit omissions")
        self.assertEqual(receipt["retained_bytes"], 4)
        self.assertEqual(receipt["gaps"][0]["counts"]["over-bound"], 2)

    def test_attempt_inventory_is_bounded(self):
        for i in range(21):
            (self.runs / str(i)).mkdir()
        with self.assertRaises(TrajectoryRefused):
            self.retain()

    def test_index_refuses_cross_run_attempt_source_phase_and_proof_upgrade(self):
        value = self.retain()
        for change in ({"run_id": 43}, {"run_attempt": 2}, {"run_attempt": True},
                       {"source_revision": "f" * 40}, {"phase": "merge"}):
            bad = deepcopy(value)
            bad["source"].update(change)
            with self.subTest(change=change), self.assertRaises(TrajectoryRefused):
                safe_index(canonical_bytes(bad), binding=BINDING)
        value["proof_reuse_allowed"] = True
        with self.assertRaises(TrajectoryRefused):
            safe_index(canonical_bytes(value), binding=BINDING)

    def test_index_refuses_arbitrary_paths_duplicates_and_incorrect_hashes_or_counts(self):
        self.write("evidence-bundle.json")
        receipt = self.retain()
        def duplicate(value):
            value["files"].append(value["files"][0])
            value["retained_bytes"] *= 2
        mutations = [lambda x: x["files"][0].update(path="../private.json"), duplicate,
                     lambda x: x["files"][0].update(sha256="SECRET"),
                     lambda x: x.update(retained_bytes=0)]
        for mutate in mutations:
            bad = deepcopy(receipt)
            mutate(bad)
            with self.assertRaises(TrajectoryRefused):
                safe_index(canonical_bytes(bad), binding=BINDING)

    def test_index_drops_unrecognized_text_fields(self):
        value = deepcopy(self.retain())
        value["private"] = "SECRET"
        value["checkout_observation"]["notes"] = "SECRET"
        report = safe_index(canonical_bytes(value), binding=BINDING)
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertEqual(report["file_identity_source"], "recorded-index")

    def test_workflow_stages_and_uploads_both_phases_without_credentials_or_gating_proof(self):
        workflow = (ROOT / ".github/workflows/dark-factory-worker.yml").read_text()
        for phase in ("dispatch", "merge"):
            job = re.split(r"\n  [a-z][a-z_-]*:", workflow.split(f"\n  {phase}:")[1])[0]
            steps = job.split("      - name:")
            staging = next(step for step in steps if "id: retain_evidence" in step)
            self.assertIn(f"--phase {phase}", staging)
            self.assertIn("if: always()", staging)
            self.assertIn("continue-on-error: true", staging)
            self.assertNotIn("env:", staging)
            uploads = [step for step in steps if "name: dark-factory-evidence-" in step]
            self.assertEqual(len(uploads), 2)
            for upload in uploads:
                self.assertIn("retention-days: 90", upload)
                self.assertIn("steps.retain_evidence.outcome == 'success'", upload)
                self.assertIn("continue-on-error: true", upload)
                self.assertNotIn("**", upload)


class RetentionArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = source(repository={"full_name": REPOSITORY, "id": 123})
        self.github = Mock(repository=REPOSITORY)
        self.index = {"schema": "dark-factory/evidence-retention", "schema_version": "1.0",
                      "authority": "observation-only", "proof_reuse_allowed": False, "source": BINDING,
                      "checkout_observation": CHECKOUT, "files": [], "gaps": [], "retained_bytes": 0}
        self.raw = canonical_bytes(self.index)
        self.rows = [self.artifact("evidence"), self.artifact("evidence-index")]
        self.github.json.return_value = self.rows[1]
        self.github.run.side_effect = lambda argv: (Path(argv[-1]) / "retention-index.json").write_bytes(self.raw)

    def artifact(self, kind):
        return {"id": 501 if kind == "evidence" else 502, "name": f"dark-factory-{kind}-dispatch-42-1",
                "digest": "sha256:" + "d" * 64, "size_in_bytes": 2000, "expired": False,
                "created_at": "2026-09-15T19:50:00Z", "expires_at": "2026-12-14T19:50:00Z",
                "workflow_run": {"id": 42, "head_sha": "a" * 40, "head_branch": "main",
                                 "repository_id": 123, "head_repository_id": 123}}

    def collect(self):
        return collect_retention(self.github, source=self.source, inventory=self.rows, directory=self.root)

    def test_archive_downloads_only_bounded_index_and_records_platform_expiry_identity(self):
        result = self.collect()
        self.assertEqual(result[0]["index"]["checkout_observation"], CHECKOUT)
        self.assertEqual(result[0]["artifacts"]["evidence"]["id"], 501)
        self.assertEqual(result[0]["artifacts"]["evidence"]["expires_at"], "2026-12-14T19:50:00Z")
        self.assertEqual(self.github.run.call_count, 1)
        self.assertIn("dark-factory-evidence-index-dispatch-42-1", self.github.run.call_args.args[0])
        self.github.run_as_app.assert_not_called()
        self.assertIs(result[0]["proof_reuse_allowed"], False)
        self.assertEqual(len(result[1]["gaps"]), 2)

    def test_old_artifact_inventory_preserves_legacy_record(self):
        self.rows = []
        self.assertIsNone(self.collect())
        self.github.run.assert_not_called()

    def test_later_rerun_artifacts_do_not_change_a_legacy_attempt(self):
        self.rows[0]["name"] = "dark-factory-evidence-dispatch-42-2"
        self.rows[1]["name"] = "dark-factory-evidence-index-dispatch-42-2"
        self.assertIsNone(self.collect())
        self.github.run.assert_not_called()

    def test_trajectory_collector_includes_retention_and_preserves_completed_attempt(self):
        config = {"provider": {"model": "public/model", "architecture_model": "public/independent"}}
        self.github.json.side_effect = [self.source, {"encoding": "base64", "size": 100,
            "content": base64.b64encode(canonical_bytes(config)).decode()},
            {"total_count": 2, "artifacts": self.rows}, self.rows[1]]
        record = collect(self.github, run_id=42, attempt=1)
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["evidence_retention"][0]["index"]["source"], BINDING)
        self.assertEqual(len(record["gaps"]), 3)
        self.github.run_as_app.assert_not_called()

    def test_combined_metadata_bound_retains_source_observation_with_explicit_gap(self):
        from factory_kernel.trajectory import summarize
        baseline = summarize(self.source, repository=REPOSITORY, directories={}, models=set())
        config = {"provider": {"model": "public/model", "architecture_model": "public/independent"}}
        self.github.json.side_effect = [self.source, {"encoding": "base64", "size": 100,
            "content": base64.b64encode(canonical_bytes(config)).decode()},
            {"total_count": 2, "artifacts": self.rows}, self.rows[1]]
        with patch("factory_kernel.trajectory_archive.MAX_RECORD", len(canonical_bytes(baseline)) + 150):
            record = collect(self.github, run_id=42, attempt=1)
        self.assertEqual(record["outcome"], "failure")
        self.assertNotIn("evidence_retention", record)
        self.assertIn({"reason": "retention-metadata-over-bound"}, record["gaps"])

    def test_expired_raw_artifact_remains_explicit_even_when_index_survives(self):
        self.rows[0]["expired"] = True
        result = self.collect()[0]
        self.assertIn({"artifact": "evidence", "reason": "artifact-expired"}, result["gaps"])
        self.assertIn("index", result)

    def test_expired_index_is_not_downloaded(self):
        self.rows[1]["expired"] = True
        self.assertNotIn("index", self.collect()[0])
        self.github.run.assert_not_called()

    def test_wrong_platform_run_head_repository_and_bounds_are_refused(self):
        for key, value in (("id", 43), ("head_sha", "f" * 40), ("head_branch", "other"),
                           ("repository_id", 456), ("head_repository_id", 456)):
            row = deepcopy(self.rows[1])
            row["workflow_run"][key] = value
            with self.subTest(key=key), self.assertRaises(TrajectoryRefused):
                artifact_observation(row, source=self.source, name=row["name"], limit=260000)
        self.rows[1]["size_in_bytes"] = 300000
        self.assertNotIn("index", self.collect()[0])
        self.github.run.assert_not_called()

    def test_duplicate_platform_artifacts_are_refused(self):
        self.rows.append(deepcopy(self.rows[1]))
        self.assertNotIn("index", self.collect()[0])
        self.github.run.assert_not_called()

    def test_wrong_attempt_in_index_is_a_gap_not_a_trusted_record(self):
        self.index["source"] = {**BINDING, "run_attempt": 2}
        self.raw = canonical_bytes(self.index)
        result = self.collect()[0]
        self.assertNotIn("index", result)
        self.assertIn({"artifact": "evidence-index", "reason": "artifact-metadata-refused"}, result["gaps"])

    def test_overwrite_during_download_is_refused(self):
        self.github.json.return_value = {**self.rows[1], "digest": "sha256:" + "e" * 64}
        self.assertNotIn("index", self.collect()[0])

    def test_unknown_private_text_never_enters_archive(self):
        self.index["secret"] = "SECRET"
        self.rows[0]["archive_download_url"] = "SECRET"
        self.raw = canonical_bytes(self.index)
        self.assertNotIn("SECRET", json.dumps(self.collect()))


if __name__ == "__main__":
    unittest.main()
