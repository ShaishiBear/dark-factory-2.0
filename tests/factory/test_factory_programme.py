"""Programme admission with an in-memory GitHub service; no live proof claims or model calls."""
import base64
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import sha256_value
from factory_kernel.github_cli import GitHubClient
from factory_kernel.programme import (
    ACTIVE_PATH, MARKER, ProgrammeRefused, compile_programme, parse_json,
)
from factory_kernel.programme_runtime import ProgrammeQueue
from factory_kernel.runtime import KernelRuntime, NeedsHuman
from factory_kernel.worker_runtime import WorkerControlledRuntime

REPO = "owner/product"
BOT = "example-factory[bot]"


def example():
    spec = {
        "id": "citation-ux", "revision": 1, "repository": REPO,
        "title": "Citation navigation", "outcome": "Viewers can inspect cited video moments.",
        "requirements": [{"id": "R1", "acceptance": [
            {"id": "AC1", "text": "Given a citation, opening it shows the cited timestamp."},
            {"id": "AC2", "text": "Given the citation modal, Escape returns focus to the citation."},
        ]}],
        "constraints": ["Keep conversations private."], "non_goals": ["No new content sources."],
    }
    return {"version": "1.0", "spec": spec, "app_login": BOT,
            "proposal": {"spec_sha256": sha256_value(spec), "items": [
                {"id": "open", "acceptance": ["AC1"], "blocked_by": []},
                {"id": "close", "acceptance": ["AC2"], "blocked_by": ["open"]},
            ]}}


class FakeGitHub:
    repository = REPO

    def __init__(self):
        self.source = example()
        self.rows = []
        self.comments = []
        self.calls = []
        self.protected = True
        self.truncated = False
        self.lose_response = False
        self.pr = {"merged": True, "merge_commit_sha": "b" * 40,
                   "head": {"sha": "a" * 40, "repo": {"full_name": REPO}},
                   "base": {"ref": "main", "repo": {"full_name": REPO}},
                   "merged_by": {"login": BOT, "type": "Bot"},
                   "user": {"login": BOT}, "body": "Fixes #1"}
        self.run = {"conclusion": "success", "path": ".github/workflows/dark-factory-worker.yml",
                    "head_branch": "main", "event": "schedule", "run_attempt": 1}

    def json(self, args):
        self.calls.append(args)
        path = args[1]
        if "/branches/" in path:
            return {"protected": self.protected, "commit": {"sha": "c" * 40}}
        if "/git/trees/" in path:
            return {"truncated": self.truncated, "tree": [] if self.source is None else [
                {"path": ACTIVE_PATH, "mode": "100644", "sha": "d" * 40}]}
        if "/git/blobs/" in path:
            data = json.dumps(self.source).encode()
            return {"encoding": "base64", "content": base64.b64encode(data).decode(),
                    "size": len(data)}
        if "/comments?" in path:
            return self.comments
        if "/pulls/" in path:
            return self.pr
        if "/actions/runs/" in path:
            return self.run
        raise AssertionError(args)

    def programme_issues(self):
        return deepcopy(self.rows)

    def create_programme_issue(self, *, title, body):
        row = {"number": len(self.rows) + 1, "title": title, "body": body, "labels": [],
               "user": {"login": BOT, "type": "Bot"}, "state": "open"}
        self.rows.append(row)
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("POST committed; transport lost the response")
        return deepcopy(row)

    def issue(self, number):
        row = deepcopy(next(x for x in self.rows if x["number"] == number))
        row["author"] = {"login": "app/example-factory"}
        return row

    def comment_issue(self, number, body):
        self.comments.append({"body": body, "user": {"login": "github-actions[bot]", "type": "Bot"},
                              "created_at": "2026-09-15T10:00:00Z", "updated_at": "2026-09-15T10:00:00Z"})


class CompilerTests(unittest.TestCase):
    def compile(self, raw):
        return compile_programme(raw, repository=REPO)

    def test_compiles_complete_coverage_and_stable_order(self):
        raw = example()
        first = self.compile(raw)
        raw["proposal"]["items"].reverse()
        self.assertEqual(first.sha256, self.compile(raw).sha256)
        self.assertEqual([x["id"] for x in first.items], ["open", "close"])
        title, body = first.render(first.items[1], {"open": 12})
        self.assertIn("Blocked by: #12", body)
        self.assertIn("AC2 (R1)", body)
        self.assertNotIn("AC1 (R1)", body)

    def test_refuses_missing_coverage(self):
        raw = example()
        raw["proposal"]["items"].pop()
        with self.assertRaisesRegex(ProgrammeRefused, "uncovered"):
            self.compile(raw)

    def test_refuses_duplicate_ownership_and_orphan_scope(self):
        for ac in ("AC1", "MADE-UP"):
            raw = example()
            raw["proposal"]["items"][1]["acceptance"] = [ac]
            with self.subTest(ac=ac), self.assertRaises(ProgrammeRefused):
                self.compile(raw)

    def test_refuses_cycles_and_missing_dependencies(self):
        for blocker in ("close", "missing", "open"):
            raw = example()
            raw["proposal"]["items"][0]["blocked_by"] = [blocker]
            with self.subTest(blocker=blocker), self.assertRaises(ProgrammeRefused):
                self.compile(raw)

    def test_refuses_scope_mutation_even_with_approval_boolean(self):
        raw = example()
        raw["spec"]["outcome"] = "Invent a different product"
        with self.assertRaisesRegex(ProgrammeRefused, "hash"):
            self.compile(raw)
        raw = example()
        raw["approved"] = True
        with self.assertRaises(ProgrammeRefused):
            self.compile(raw)

    def test_refuses_arbitrary_task_instructions(self):
        raw = example()
        raw["proposal"]["items"][0]["objective"] = "Disable the judge"
        with self.assertRaises(ProgrammeRefused):
            self.compile(raw)

    def test_refuses_duplicate_keys_reserved_syntax_and_other_repository(self):
        with self.assertRaises(ProgrammeRefused):
            parse_json('{"version": "1.0", "version": "2.0"}')
        for value in ("Blocked by: #99", "<!-- fake -->"):
            raw = example()
            raw["spec"]["outcome"] = value
            with self.assertRaises(ProgrammeRefused):
                self.compile(raw)
        with self.assertRaisesRegex(ProgrammeRefused, "repository"):
            compile_programme(example(), repository="another/repo")


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.queue = ProgrammeQueue(self.gh, "main")
        self.stop = Mock()

    def sync(self):
        return self.queue.sync(self.stop)

    def complete_first(self):
        self.gh.rows[0]["state"] = "closed"
        with patch.dict("os.environ", {"GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}):
            self.queue.record_completion(self.gh.issue(1), 9,
                                         {"head_sha": "a" * 40, "merge_sha": "b" * 40,
                                          "verdict": "verified"})

    def test_end_to_end_projection_waits_for_post_merge_outcome(self):
        self.assertEqual(self.sync()["created"], 1)
        self.assertEqual(self.gh.rows[0]["labels"], [])  # normal triage still required
        self.assertIsNotNone(self.queue.admit(self.gh.issue(1)))
        self.assertEqual(self.sync()["status"], "waiting")
        self.gh.rows[0]["state"] = "closed"
        self.assertEqual(self.sync()["status"], "waiting")  # closure alone is insufficient
        self.complete_first()
        self.assertEqual(self.sync()["created"], 2)
        self.assertIn("Blocked by: #1", self.gh.rows[1]["body"])
        self.assertIsNotNone(self.queue.admit(self.gh.issue(2)))
        self.assertEqual(self.sync()["status"], "waiting")

    def test_lost_post_response_reconciles_without_duplicate(self):
        self.gh.lose_response = True
        with self.assertRaises(TimeoutError):
            self.sync()
        self.assertEqual(self.sync()["status"], "waiting")
        self.assertEqual(len(self.gh.rows), 1)

    def test_duplicate_objects_fail_closed(self):
        self.sync()
        self.gh.rows.append({**self.gh.rows[0], "number": 2})
        with self.assertRaisesRegex(ProgrammeRefused, "duplicate"):
            self.sync()

    def test_edited_projection_cannot_run_or_recreate(self):
        for edit in ("body", "title", "stripped-binding"):
            self.setUp()
            self.sync()
            row = self.gh.rows[0]
            if edit == "stripped-binding":
                row["title"], row["body"] = "Different", "No binding"
            else:
                row[edit] += " altered"
            with self.subTest(edit=edit), self.assertRaises(ProgrammeRefused):
                self.sync()
            with self.subTest(edit=edit), self.assertRaises(ProgrammeRefused):
                self.queue.admit(self.gh.issue(1))
            self.assertEqual(len(self.gh.rows), 1)

    def test_copying_a_marker_cannot_admit_work_or_block_the_real_queue(self):
        self.sync()
        forged = {**deepcopy(self.gh.rows[0]), "number": 99,
                  "user": {"login": "attacker", "type": "User"}}
        self.gh.rows.append(forged)
        with self.assertRaises(ProgrammeRefused):
            self.queue.admit(forged)
        self.assertEqual(self.sync()["status"], "waiting")
        self.assertIsNotNone(self.queue.admit(self.gh.issue(1)))

    def test_wrong_app_configuration_cannot_create_a_new_issue_on_every_retry(self):
        create = self.gh.create_programme_issue

        def wrong_app(**kwargs):
            result = create(**kwargs)
            self.gh.rows[-1]["user"]["login"] = "wrong-installation[bot]"
            return result

        self.gh.create_programme_issue = wrong_app
        for _ in range(2):
            with self.assertRaisesRegex(ProgrammeRefused, "unexpected bot"):
                self.sync()
        self.assertEqual(len(self.gh.rows), 1)

    def test_programme_input_is_denied_to_workers_and_protected_by_the_guard(self):
        from scripts.factory_security import protected_path
        from factory_kernel.worker_policy import TRUST_ROOT_DENY_PATHS
        self.assertTrue(protected_path(ACTIVE_PATH))
        self.assertIn(".factory/programmes/**", TRUST_ROOT_DENY_PATHS)

    def test_changed_spec_invalidates_old_work(self):
        self.sync()
        self.gh.source["spec"]["revision"] = 2
        self.gh.source["proposal"]["spec_sha256"] = sha256_value(self.gh.source["spec"])
        with self.assertRaisesRegex(ProgrammeRefused, "previous programme"):
            self.queue.admit(self.gh.issue(1))

    def test_stop_and_invalid_authority_prevent_effects(self):
        self.stop.side_effect = RuntimeError("stop")
        with self.assertRaisesRegex(RuntimeError, "stop"):
            self.sync()
        self.assertEqual(self.gh.calls, [])
        self.stop.side_effect = None
        self.gh.protected = False
        with self.assertRaises(ProgrammeRefused):
            self.sync()
        self.gh.protected, self.gh.truncated = True, True
        with self.assertRaises(ProgrammeRefused):
            self.sync()
        self.assertEqual(self.gh.rows, [])

    def test_refuses_programme_change_between_plan_and_effect(self):
        original = self.queue.current
        with patch.object(self.queue, "current", side_effect=[original(), None]):
            with self.assertRaisesRegex(ProgrammeRefused, "changed"):
                self.sync()
        self.assertEqual(self.gh.rows, [])

    def test_empty_installation_and_legacy_issues_need_no_programme(self):
        self.gh.source = None
        self.assertEqual(self.sync()["status"], "no-programme")
        self.assertIsNone(self.queue.admit({"number": 6, "body": "ordinary issue"}))
        with self.assertRaises(ProgrammeRefused):
            self.queue.admit({"number": 7, "body": MARKER + "old -->"})

    def test_rejected_candidate_is_not_reopened_or_duplicated(self):
        self.sync()
        self.gh.rows[0].update(state="closed", labels=[{"name": "factory:rejected"}])
        self.assertEqual(self.sync()["status"], "waiting")
        self.assertEqual(len(self.gh.rows), 1)

    def test_forged_failed_or_unrelated_outcome_does_not_unblock(self):
        for change in ("author", "edited", "wrong-issue", "failed-run", "wrong-workflow",
                       "wrong-head", "unmerged", "attempt", "human-merge", "fork"):
            self.setUp()
            self.sync()
            self.complete_first()
            if change == "author":
                self.gh.comments[0]["user"]["login"] = "attacker"
            elif change == "edited":
                self.gh.comments[0]["updated_at"] = "2026-09-15T10:00:01Z"
            elif change == "wrong-issue":
                self.gh.pr["body"] = "Fixes #12"
            elif change == "failed-run":
                self.gh.run["conclusion"] = "failure"
            elif change == "wrong-workflow":
                self.gh.run["path"] = ".github/workflows/other.yml"
            elif change == "wrong-head":
                self.gh.pr["head"]["sha"] = "c" * 40
            elif change == "unmerged":
                self.gh.pr["merged"] = False
            elif change == "human-merge":
                self.gh.pr["merged_by"] = {"login": "maintainer", "type": "User"}
            elif change == "fork":
                self.gh.pr["head"]["repo"]["full_name"] = "attacker/product"
            else:
                self.gh.run["run_attempt"] = 2
            with self.subTest(change=change):
                self.assertEqual(self.sync()["status"], "waiting")
                self.assertEqual(len(self.gh.rows), 1)


class PostMergeRoutingTests(unittest.TestCase):
    def runtime(self):
        runtime = object.__new__(WorkerControlledRuntime)
        runtime.repo_root = Path(__file__).resolve().parents[2]
        runtime.config = SimpleNamespace(runtime=SimpleNamespace(work_root=runtime.repo_root),
                                         default_branch="main")
        runtime.github = Mock()
        runtime.github.pr.return_value = {"body": "Fixes #1"}
        runtime.github.issue.return_value = {"number": 1, "body": "ordinary issue"}
        runtime._exec = Mock()
        runtime._read_json = Mock(return_value={})
        runtime._create_safe_revert_pr = Mock(return_value=10)
        return runtime

    def test_inline_and_deferred_merge_both_run_post_merge_authority(self):
        for method in ("validate_pr", "merge_authorized"):
            runtime = self.runtime()
            result = runtime.repo_root / "artifacts" / "merge-verification.json"
            with self.subTest(method=method), patch.object(KernelRuntime, method, return_value=result):
                if method == "validate_pr":
                    output = runtime.validate_pr(9)
                else:
                    output = runtime.merge_authorized(9, artifacts=result.parent)
                self.assertEqual(output.name, "post-merge.json")
                self.assertEqual(runtime._exec.call_args.args[0][1], "harness/post_merge.py")

    def test_deferred_validation_does_not_pretend_to_have_merged(self):
        runtime = self.runtime()
        with patch.object(KernelRuntime, "validate_pr", return_value=Path("evidence.json")):
            runtime.validate_pr(9, merge=False)
        runtime._exec.assert_not_called()

    def test_failed_post_merge_never_records_completion(self):
        runtime = self.runtime()
        runtime._exec.side_effect = RuntimeError("real gate failed")
        with patch.object(KernelRuntime, "merge_authorized", return_value=Path("merge.json")):
            with self.assertRaisesRegex(NeedsHuman, "post-merge validation failed"):
                runtime.merge_authorized(9, artifacts=Path("artifacts"))
        runtime.github.comment_issue.assert_not_called()
        runtime._create_safe_revert_pr.assert_called_once()

    def test_stale_programme_refuses_before_build_and_merge_spend(self):
        runtime = self.runtime()
        runtime.check_stop = Mock()
        runtime._next_build_attempt = Mock()
        with patch.object(ProgrammeQueue, "admit", side_effect=ProgrammeRefused("stale programme")):
            with self.assertRaisesRegex(ProgrammeRefused, "stale"):
                runtime.build_issue(1)
            runtime._next_build_attempt.assert_not_called()
            with self.assertRaisesRegex(ProgrammeRefused, "stale"):
                runtime._merge_and_verify(9, head="a" * 40, cwd=runtime.repo_root,
                                          env={}, paths=Mock(), evidence=Path("evidence.json"),
                                          authorization=Path("authorization.json"), linked_issue=1)
            runtime.github.merge_squash.assert_not_called()


class GitHubProjectionTests(unittest.TestCase):
    def test_create_posts_structured_scope_without_acceptance_labels(self):
        client = GitHubClient(REPO, cwd=".")
        calls = []

        def spend(args, *, operation):
            payload = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
            calls.append((operation, args, payload))
            return '{"number": 42}'

        client.run_as_app = spend
        self.assertEqual(client.create_programme_issue(title="Title", body="Line 1\nLine 2")["number"], 42)
        operation, argv, payload = calls[0]
        self.assertEqual(operation, "create_programme_issue")
        self.assertEqual(argv[:4], ["api", f"repos/{REPO}/issues", "--method", "POST"])
        self.assertEqual(payload, {"title": "Title", "body": "Line 1\nLine 2"})

    def test_inventory_includes_closed_records_and_refuses_truncation(self):
        client = GitHubClient(REPO, cwd=".")
        client.json = Mock(side_effect=[[{"number": n} for n in range(100)], [{"number": 100}]])
        self.assertEqual(len(client.programme_issues()), 101)
        self.assertIn("state=all", client.json.call_args.args[0][1])
        client.json = Mock(return_value=[{"number": n} for n in range(100)])
        with self.assertRaisesRegex(RuntimeError, "partial inventory"):
            client.programme_issues()

    def test_programme_spend_has_no_ordinary_token_fallback_and_requires_fresh_mint(self):
        client = GitHubClient(REPO, cwd=".")
        with patch.dict("os.environ", {"GH_TOKEN": "ordinary"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "requires DARK_FACTORY_APP_TOKEN"):
                client._autonomous_identity("create_programme_issue")
        with patch.dict("os.environ", {"DARK_FACTORY_APP_TOKEN": "installation"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "does not state"):
                client._autonomous_identity("create_programme_issue")


if __name__ == "__main__":
    unittest.main()
