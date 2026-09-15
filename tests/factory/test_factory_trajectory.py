"""Attempt observations retain failures without gaining execution or proof authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import canonical_bytes
from factory_kernel.github_cli import GitHubClient
from factory_kernel.trajectory import TrajectoryRefused, summarize, validate_source
from factory_kernel.trajectory_archive import ARCHIVE_REF, REPOSITORY, WORKFLOW_PATH, TrajectoryArchive, authorize_context, collect


def source(**changes):
    return {"id": 42, "run_attempt": 1, "repository": {"full_name": REPOSITORY},
            "path": ".github/workflows/dark-factory-worker.yml", "head_branch": "main",
            "head_sha": "a" * 40, "event": "workflow_dispatch", "status": "completed",
            "conclusion": "failure", "created_at": "2026-09-15T19:00:00Z", "updated_at": "2026-09-15T20:00:00Z", **changes}


def stage(**changes):
    return {"kind": "agent", "name": "test_author", "stage_run": 1, "attempts": 1,
            "model": "public/model", "effort": "medium", "num_turns": 30, "seconds": 900.5,
            "cost_usd": 0.82, "outcome": "failed", "started_at": "2026-09-15T19:00:00Z",
            "ended_at": "2026-09-15T19:15:00Z", "error": "SECRET must not be archived", **changes}


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.attempt = self.root / "issue-181-a1-0123456789"
        (self.attempt / "transcripts").mkdir(parents=True)
        (self.attempt / "artifacts").mkdir()
        self.timings = self.attempt / "transcripts/stage-timings.jsonl"
        self.timings.write_text(json.dumps(stage()) + "\n" + json.dumps(stage(stage_run=2, outcome="ok", cost_usd=None)))
        (self.attempt / "artifacts/issue.json").write_text(json.dumps({"number": 181, "body": "SECRET original text"}))
        (self.attempt / "artifacts/contract.json").write_text('{"prompt":"SECRET raw proposal"}')
        (self.attempt / "transcripts/agent-test_author.json").write_text('{"content":"SECRET model answer"}')

    def summary(self, **kwargs):
        return summarize(source(), repository=REPOSITORY, directories={"dispatch": self.root},
                         models={"public/model"}, **kwargs)

    def test_failed_attempt_and_repeated_stage_are_retained_without_raw_private_text(self):
        result = self.summary()
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(result["issues"], [181])
        self.assertEqual(result["authority"], "observation-only")
        self.assertEqual(result["learning_scope"], "project-local")
        stages = result["attempts"][0]["stages"]
        self.assertEqual([row["attempt"] for row in stages], [1, 2])
        self.assertEqual([row["result"] for row in stages], ["failed", "ok"])
        self.assertEqual(stages[1]["cost"]["amount"], None, "unknown cost is never fabricated as zero")
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(len(result["attempts"][0]["artifact_refs"]), 2)
        self.assertEqual(result, self.summary(), "the same observations must serialize reproducibly")

    def test_cancelled_or_early_failure_without_artifacts_still_has_a_trajectory(self):
        result = summarize(source(conclusion="cancelled"), repository=REPOSITORY, directories={}, models=set())
        self.assertEqual(result["outcome"], "cancelled")
        self.assertEqual(result["attempts"], [])
        self.assertEqual(result["gaps"], [{"reason": "no-kernel-artifacts"}])

    def test_failed_subject_and_programme_are_bound_without_copying_issue_wording(self):
        issue = {"number": 181, "body": "<!-- dark-factory-programme:" + "b" * 64 + ":transcript -->\n"
                 "SECRET private wording\nSpecification SHA256: " + "c" * 64}
        (self.attempt / "artifacts/issue.json").write_text(json.dumps(issue))
        result = self.summary()
        self.assertEqual(result["attempts"][0]["programme_item"],
                         {"programme_sha256": "b" * 64, "spec_sha256": "c" * 64, "item_id": "transcript"})
        self.assertNotIn("SECRET", json.dumps(result))
        self.attempt.rename(self.root / "pr-184-0123456789ab")
        self.assertEqual(self.summary()["pull_requests"], [184])

    def test_source_ref_attempt_repository_and_completion_are_mandatory(self):
        validate_source(source(), repository=REPOSITORY, run_id=42, attempt=1)
        for change in ({"id": 43}, {"run_attempt": 2}, {"repository": {"full_name": "outsider/repo"}},
                       {"path": "other.yml"}, {"head_branch": "candidate"}, {"event": "pull_request"},
                       {"status": "in_progress"}, {"conclusion": None}, {"head_sha": "invalid"}):
            with self.subTest(change=change), self.assertRaises(TrajectoryRefused):
                validate_source(source(**change), repository=REPOSITORY, run_id=42, attempt=1)

    def test_invalid_stage_is_a_recorded_gap_and_unrecognized_model_is_not_leaked(self):
        self.timings.write_text(json.dumps(stage(model="SECRET_MODEL")) + "\ninvalid-json\n"
                               + json.dumps(stage(cost_usd=float("nan"))))
        result = self.summary()["attempts"][0]
        self.assertEqual(len(result["stages"]), 1)
        self.assertIsNone(result["stages"][0]["model"])
        self.assertEqual([gap["ordinal"] for gap in result["gaps"]], [2, 3])
        self.assertNotIn("SECRET_MODEL", json.dumps(result))

    def test_refused_exec_is_preserved_as_failure_measurement(self):
        self.timings.write_text(json.dumps(stage(kind="exec", name="evidence", outcome="refused", model=None)))
        self.assertEqual(self.summary()["attempts"][0]["stages"][0]["result"], "refused")

    def test_artifact_symlink_cannot_read_outside_the_download(self):
        path = self.attempt / "artifacts/issue.json"
        path.unlink()
        outside = self.root.parent / (self.root.name + "-outside.json")
        outside.write_text('{"number":999}')
        self.addCleanup(outside.unlink)
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(TrajectoryRefused):
            self.summary()

    def test_archive_context_only_allows_completed_event_or_owner_recovery_on_main(self):
        environ = {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/main",
                   "GITHUB_EVENT_NAME": "workflow_run"}
        event = {"workflow_run": source()}
        self.assertEqual(authorize_context(environ, event), (42, 1))
        for change in ({"GITHUB_REF": "refs/heads/candidate"}, {"GITHUB_EVENT_NAME": "pull_request"},
                       {"GITHUB_WORKFLOW_REF": "other"}, {"GITHUB_REPOSITORY": "other/repo"}):
            with self.subTest(change=change), self.assertRaises(TrajectoryRefused):
                authorize_context({**environ, **change}, event)
        environ.update(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_ACTOR="outsider", GITHUB_TRIGGERING_ACTOR="outsider")
        with self.assertRaises(TrajectoryRefused):
            authorize_context(environ, {"inputs": {"run_id": "42", "attempt": "1"}})
        environ.update(GITHUB_ACTOR="ShaishiBear", GITHUB_TRIGGERING_ACTOR="ShaishiBear")
        self.assertEqual(authorize_context(environ, {"inputs": {"run_id": "42", "attempt": "1"}}), (42, 1))

    def test_collection_uses_exact_attempt_and_source_policy_with_missing_artifacts_recorded(self):
        import base64
        github = Mock(repository=REPOSITORY)
        config = {"provider": {"model": "public/model", "architecture_model": "public/independent"}}
        github.json.side_effect = [source(), {"encoding": "base64", "size": 100,
            "content": base64.b64encode(json.dumps(config).encode()).decode()}, {"total_count": 0, "artifacts": []}]
        record = collect(github, run_id=42, attempt=1)
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["attempts"], [])
        self.assertEqual(len(record["gaps"]), 3)
        self.assertTrue(github.json.call_args_list[0].args[0][1].endswith("/runs/42/attempts/1"))
        github.run.assert_not_called()
        github.run_as_app.assert_not_called()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.record = summarize(source(), repository=REPOSITORY, directories={}, models=set())
        self.raw = canonical_bytes(self.record)
        self.blob = hashlib.sha1(f"blob {len(self.raw)}\0".encode() + self.raw).hexdigest()
        self.github = Mock(repository=REPOSITORY)
        self.archive = TrajectoryArchive(self.github)
        self.entries = {}
        self.head, self.tree = None, None
        self.effects = []
        self.uncertain, self.conflict = False, False
        self.archive._inventory = lambda: (self.head, self.tree, self.entries.copy())
        self.github.run_as_app.side_effect = self.effect

    def effect(self, args, *, operation):
        self.assertEqual(operation, "archive_trajectory")
        payload = json.loads(Path(args[-1]).read_text())
        self.effects.append((args[1], args[3], deepcopy(payload)))
        if args[1].endswith("/trees"):
            self.assertEqual(payload["tree"], [{"path": "42-1.json", "mode": "100644", "type": "blob", "content": self.raw.decode()}])
            return json.dumps({"sha": "b" * 40})
        if args[1].endswith("/commits"):
            self.assertNotIn("author", payload, "the App must not fabricate a human commit author")
            self.assertNotIn("committer", payload)
            return json.dumps({"sha": "c" * 40})
        if self.conflict:
            raise RuntimeError("non-fast-forward")
        self.entries["42-1.json"] = self.blob
        self.head, self.tree = "c" * 40, "b" * 40
        if self.uncertain:
            raise RuntimeError("response lost after ref update")
        return json.dumps({"ref": ARCHIVE_REF})

    def test_new_orphan_archive_and_exact_replay_never_touch_main_or_create_a_pr(self):
        self.assertEqual(self.archive.publish(self.record)["state"], "recorded")
        self.assertEqual(self.effects[-1], (f"repos/{REPOSITORY}/git/refs", "POST", {"ref": ARCHIVE_REF, "sha": "c" * 40}))
        self.assertNotIn("parents", self.effects[1][2])
        self.assertEqual(self.archive.publish(self.record)["state"], "already-recorded")
        self.assertEqual(len(self.effects), 3)
        self.assertFalse(any("main" in endpoint or "/pulls" in endpoint or "/issues" in endpoint for endpoint, _, _ in self.effects))

    def test_append_preserves_parent_tree_and_refuses_to_force_through_a_race(self):
        self.head, self.tree = "d" * 40, "e" * 40
        self.entries = {"41-1.json": "f" * 40}
        self.conflict = True
        with self.assertRaises(TrajectoryRefused):
            self.archive.publish(self.record)
        self.assertEqual(self.effects[0][2].get("base_tree"), "e" * 40)
        self.assertEqual(self.effects[1][2].get("parents"), ["d" * 40])
        self.assertEqual(self.effects[-1][1:], ("PATCH", {"sha": "c" * 40, "force": False}))
        self.assertEqual(self.entries, {"41-1.json": "f" * 40})
        self.assertEqual(len(self.effects), 3, "an uncertain publication is never repeated")

    def test_lost_ref_update_response_is_observed_without_repeated_publication(self):
        self.uncertain = True
        self.assertEqual(self.archive.publish(self.record)["state"], "recorded")
        self.assertEqual(len(self.effects), 3)

    def test_existing_attempt_cannot_be_replaced(self):
        self.entries = {"42-1.json": "f" * 40}
        with self.assertRaises(TrajectoryRefused):
            self.archive.publish(self.record)
        self.assertEqual(self.effects, [])

    def test_scope_and_observation_only_marker_are_required_before_effects(self):
        for change in ({"authority": "merge-authority"}, {"learning_scope": "global"},
                       {"repository": "other/repo"}, {"run_id": "42"}, {"trajectory_id": "different"}):
            with self.subTest(change=change), self.assertRaises(TrajectoryRefused):
                self.archive.publish({**self.record, **change})
        self.assertEqual(self.effects, [])

    def test_archive_spend_requires_fresh_app_identity_without_personal_fallback(self):
        github = GitHubClient(REPOSITORY, cwd=Path.cwd())
        with patch.dict("os.environ", {"GH_TOKEN": "ordinary"}, clear=True), self.assertRaises(RuntimeError):
            github._autonomous_identity("archive_trajectory")
        with patch.dict("os.environ", {"DARK_FACTORY_APP_TOKEN": "app"}, clear=True), self.assertRaises(RuntimeError):
            github._autonomous_identity("archive_trajectory")

    def test_archive_inventory_refuses_truncation_and_unrelated_branch_content(self):
        archive = TrajectoryArchive(self.github)
        ref = [{"ref": ARCHIVE_REF, "object": {"type": "commit", "sha": "a" * 40}}]
        commit = {"tree": {"sha": "b" * 40}}
        entry = {"path": "41-1.json", "type": "blob", "mode": "100644", "sha": "c" * 40}
        self.github.json.side_effect = [ref, commit, {"truncated": False, "tree": [entry]}]
        self.assertEqual(archive._inventory(), ("a" * 40, "b" * 40, {"41-1.json": "c" * 40}))
        for tree in ({"truncated": True, "tree": [entry]},
                     {"truncated": False, "tree": [{**entry, "path": "factory_kernel/runtime.py"}]},
                     {"truncated": False, "tree": [{**entry, "mode": "120000"}]},
                     {"truncated": False, "tree": [entry, entry]}):
            self.github.json.side_effect = [ref, commit, tree]
            with self.subTest(tree=tree), self.assertRaises(TrajectoryRefused):
                archive._inventory()
    def test_workflow_archives_all_conclusions_without_executing_source_or_accessing_provider_keys(self):
        workflow = (Path(__file__).parents[2] / WORKFLOW_PATH).read_text()
        self.assertIn("types: [completed]", workflow)
        self.assertNotIn("conclusion == 'success'", workflow)
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertNotIn("ref: ${{ github.event.workflow_run.head_sha }}", workflow)
        self.assertIn("permission-contents: write", workflow)
        self.assertNotIn("permission-issues: write", workflow)
        self.assertNotIn("OPENROUTER_API_KEY", workflow)
        self.assertIn("trajectory_archive collect", workflow)
        self.assertIn("trajectory_archive publish", workflow)
        self.assertLess(workflow.index("trajectory_archive collect"), workflow.index("Mint only"))


if __name__ == "__main__":
    unittest.main()
