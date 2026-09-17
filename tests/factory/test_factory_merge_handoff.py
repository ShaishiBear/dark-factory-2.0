"""A job boundary preserves bytes and refuses substituted/replayed authority."""
from copy import deepcopy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.merge_handoff import FILES, export_handoff, import_handoff
from factory_kernel.runtime import FactoryStopped, KernelRuntime, NeedsHuman


class MergeHandoffTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.subject = {"repository": "owner/repo", "run": "123", "attempt": "1",
                        "kernel": "a" * 40, "pr": 9}
        for name in FILES:
            (self.artifacts / name).write_bytes(b'{ "unicode": "\xc3\xa9" }\r\n')
        self.transport = self.root / "handoff.json"
        self.digest = export_handoff(self.artifacts, self.transport, subject=self.subject)

    def test_exact_bytes_survive_a_new_working_directory(self):
        destination = self.root / "another-runner" / "input"
        import_handoff(self.transport, destination, expected_sha256=self.digest, subject=self.subject)
        for name in FILES:
            self.assertEqual((destination / name).read_bytes(), (self.artifacts / name).read_bytes())

    def test_substituted_artifact_refuses_before_writing(self):
        self.transport.write_bytes(self.transport.read_bytes() + b" ")
        destination = self.root / "input"
        with self.assertRaisesRegex(ValueError, "digest"):
            import_handoff(self.transport, destination, expected_sha256=self.digest, subject=self.subject)
        self.assertFalse(destination.exists())

    def test_other_run_attempt_kernel_repository_or_pr_cannot_replay(self):
        for field in self.subject:
            changed = deepcopy(self.subject)
            changed[field] = "different"
            destination = self.root / field
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "identity"):
                import_handoff(self.transport, destination, expected_sha256=self.digest, subject=changed)
            self.assertFalse(destination.exists())

    def test_existing_import_is_never_overwritten(self):
        destination = self.root / "input"
        import_handoff(self.transport, destination, expected_sha256=self.digest, subject=self.subject)
        with self.assertRaises(FileExistsError):
            import_handoff(self.transport, destination, expected_sha256=self.digest, subject=self.subject)

    def test_missing_proof_cannot_be_exported(self):
        (self.artifacts / FILES[1]).unlink()
        with self.assertRaisesRegex(ValueError, "input"):
            export_handoff(self.artifacts, self.transport, subject=self.subject)


class HostedJobBoundaryTests(unittest.TestCase):
    def test_merge_consumes_only_the_successful_same_run_handoff(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / ".github/workflows/dark-factory-worker.yml").read_text()
        dispatch, merge = source.split("\n  merge:\n")
        self.assertNotIn("python -m factory_kernel merge --pr", dispatch)
        self.assertIn("needs: dispatch", merge)
        self.assertIn("if: needs.dispatch.outputs.handoff_sha256 != ''", merge)
        self.assertIn("ref: ${{ needs.dispatch.outputs.kernel_sha }}", merge)
        self.assertIn('merge-handoff-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT', merge)
        self.assertIn('merge-import --pr', merge)
        self.assertLess(merge.index("merge-import --pr"), merge.index("Mint a fresh identity"))
        self.assertIn("cancel-in-progress: false", dispatch)
        mint = dispatch.split("id: factory_identity", 1)[1].split("- name:", 1)[0]
        for permission in ("contents", "pull-requests", "issues"):
            self.assertIn(f"permission-{permission}: write", mint)
        self.assertIn("npm install -g @anthropic-ai/claude-code@2.1.245", dispatch)
        self.assertIn("persist-credentials: false", merge)
        self.assertNotIn("persist-credentials: true", merge)
        self.assertIn("image: pgvector/pgvector:pg16", merge)
        self.assertIn("npm install -g @anthropic-ai/claude-code@2.1.245", merge)

    def test_job_clocks_cover_separated_proof_budgets(self):
        import re
        from harness import budget
        from factory_kernel.worker_policy import stage_timeout_seconds

        root = Path(__file__).resolve().parents[2]
        dispatch, merge = (root / ".github/workflows/dark-factory-worker.yml").read_text().split("\n  merge:\n")
        # The read-only `plan` job precedes `dispatch` since WP00 and has its own short clock;
        # the proof clock under test is the dispatch job's.
        dispatch = dispatch.split("\n  dispatch:\n", 1)[1]
        record = budget.load()
        qualification = budget.budget_seconds(record, "evidence-spine")
        qualification += 5 * stage_timeout_seconds("holdout") + 900
        post = budget.budget_seconds(record, "post-merge") + 900
        for text, required in ((dispatch, qualification), (merge, post)):
            minutes = int(re.search(r"timeout-minutes: (\d+)", text).group(1))
            self.assertLessEqual(minutes, 360)
            self.assertGreaterEqual(minutes * 60, required)


class BuildPublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        run = self.root / "runs" / "build"
        artifacts = run / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "final-green-proof.json").write_text('{}')
        self.runtime = rt = object.__new__(KernelRuntime)
        rt.repo_root = self.root / "kernel"
        rt.config = SimpleNamespace(runtime=SimpleNamespace(work_root=self.root), default_branch="main",
                                    labels={"accepted": "factory:accepted"})
        rt.check_stop = Mock()
        rt._assert_clean = Mock()
        rt._run_env = Mock(return_value={})
        rt._lease_heartbeat = Mock()
        rt._publish_build = Mock(return_value=17)
        rt.github = Mock()
        issue = {"number": 5, "title": "Approved task", "body": "scope"}
        rt.github.issue.return_value = issue
        rt.github.labels.return_value = {"factory:accepted"}
        rt._git = Mock(side_effect=lambda *args, **kwargs: "factory/test" if args[0] == "branch" else "a" * 40)
        value = {"version": "1.0", "issue": 5, "attempt": 1, "head": "a" * 40,
                 "base": "b" * 40, "kernel": "a" * 40, "branch": "factory/test",
                 "worktree": str(self.root / "worktrees" / "build"),
                 "issue_sha256": rt._json_sha({"title": issue["title"], "body": issue["body"]}),
                 "artifacts": rt._publication_artifacts(artifacts)}
        self.handoff = run / "build-publication.json"
        self.handoff.write_text(json.dumps(value))
        self.digest = hashlib.sha256(self.handoff.read_bytes()).hexdigest()

    def test_publication_rechecks_identity_evidence_issue_and_lease_without_models(self):
        with patch("factory_kernel.runtime.remove"):
            self.assertEqual(self.runtime.publish_prepared(self.handoff, expected_sha256=self.digest), 17)
        self.runtime._lease_heartbeat.assert_called_once()
        self.runtime._publish_build.assert_called_once()
        self.assertEqual(self.runtime.check_stop.call_count, 2)

    def test_changed_prepared_evidence_never_reaches_publication(self):
        (self.handoff.parent / "artifacts" / "final-green-proof.json").write_text('{"changed": true}')
        with self.assertRaisesRegex(NeedsHuman, "evidence changed"):
            self.runtime.publish_prepared(self.handoff, expected_sha256=self.digest)
        self.runtime._publish_build.assert_not_called()

    def test_changed_issue_never_reaches_publication(self):
        self.runtime.github.issue.return_value["body"] = "Different scope"
        with self.assertRaisesRegex(NeedsHuman, "issue changed"):
            self.runtime.publish_prepared(self.handoff, expected_sha256=self.digest)
        self.runtime._publish_build.assert_not_called()

    def test_changed_head_never_reaches_publication(self):
        self.runtime._git.side_effect = ["a" * 40, "b" * 40]
        with self.assertRaisesRegex(NeedsHuman, "identity or evidence changed"):
            self.runtime.publish_prepared(self.handoff, expected_sha256=self.digest)
        self.runtime._publish_build.assert_not_called()

    def test_stop_at_either_publication_checkpoint_releases_without_failure(self):
        for checkpoint in (1, 2):
            with self.subTest(checkpoint=checkpoint):
                self.runtime.check_stop.side_effect = [None] * (checkpoint - 1) + [FactoryStopped("stop #9")]
                with patch.object(self.runtime, "_release_stopped_build") as release, patch.object(self.runtime, "_mark_issue_human") as failure:
                    with self.assertRaises(FactoryStopped):
                        self.runtime.publish_prepared(self.handoff, expected_sha256=self.digest)
                    release.assert_called_once()
                    failure.assert_not_called()
                self.runtime._publish_build.assert_not_called()

    def test_changed_handoff_never_reaches_publication(self):
        self.handoff.write_text('{}')
        with self.assertRaisesRegex(NeedsHuman, "handoff"):
            self.runtime.publish_prepared(self.handoff, expected_sha256=self.digest)
        self.runtime._publish_build.assert_not_called()

    def test_fresh_publication_passes_captured_base_to_the_real_publisher(self):
        rt = self.runtime
        rt.config.repository = "owner/repo"
        rt.config.labels["in_progress"] = "factory:in-progress"
        rt._run_env = KernelRuntime._run_env.__get__(rt)
        rt._publish_build = KernelRuntime._publish_build.__get__(rt)
        rt._exec = Mock()
        rt._hand_to_review = Mock()
        rt.github.create_pr.return_value = {"number": 17}
        (self.handoff.parent / "artifacts" / "task-contract.json").write_text('{}')
        value = json.loads(self.handoff.read_text())
        value["artifacts"] = rt._publication_artifacts(self.handoff.parent / "artifacts")
        self.handoff.write_text(json.dumps(value))
        digest = hashlib.sha256(self.handoff.read_bytes()).hexdigest()
        with patch("factory_kernel.runtime.remove"), patch("factory_kernel.runtime.render_pr_body", return_value="body"), patch.dict("os.environ", {"FACTORY_BASE_SHA": "c" * 40}):
            try:
                result = rt.publish_prepared(self.handoff, expected_sha256=digest)
            except Exception as exc:
                self.fail(f"valid prepared build must publish its captured base: {exc}")
        self.assertEqual(result, 17)
        publish = next(c for c in rt._exec.call_args_list if "publish" in c.args[0])
        argv = publish.args[0]
        self.assertEqual(argv[argv.index("--base") + 1], "b" * 40)
        self.assertEqual(publish.kwargs["env"]["FACTORY_BASE_SHA"], "b" * 40)

    def test_malformed_captured_base_refuses_before_any_publication_effect(self):
        value = json.loads(self.handoff.read_text())
        for base in (None, "main", "a" * 41, "a" * 63, 123):
            value["base"] = base
            self.handoff.write_text(json.dumps(value))
            digest = hashlib.sha256(self.handoff.read_bytes()).hexdigest()
            with self.subTest(base=base), patch("factory_kernel.runtime.remove"), self.assertRaisesRegex(NeedsHuman, "identity or evidence"):
                self.runtime.publish_prepared(self.handoff, expected_sha256=digest)
            self.runtime._publish_build.assert_not_called()
            self.runtime._lease_heartbeat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
