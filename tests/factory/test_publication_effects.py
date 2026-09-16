"""Publication spends are current, exact, journaled and never blindly retried."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.publication_effects import EffectJournal, ProgrammePublisher
from factory_kernel import publication_policy as policy
from tests.factory.test_publication_admission import publication_facts


class PublicationEffectsTests(unittest.TestCase):
    def setUp(self):
        self.facts = publication_facts()
        self.manifest = self.facts["manifest"]
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "effects.json"
        self.journal = EffectJournal(self.path, self.manifest)
        self.github = Mock(repository=policy.REPOSITORY)
        self.source = Mock(return_value={"repository": policy.REPOSITORY, "protected": True,
                                        "main_sha": "a" * 40, "active_input": None,
                                        "stop": {"state": "clear", "issues": []}})
        current = {key: self.manifest[key] for key in
                   ("repository", "project", "request_id", "request_sha256", "input_sha256", "programme_sha256")}
        current.update(decision="current-owner-request", main_sha="a" * 40)
        self.currency = Mock(return_value=current)
        self.publisher = ProgrammePublisher(self.github, self.manifest, self.journal,
                                            currency=self.currency, source=self.source)
        self.github.json.side_effect = self.read
        self.github.run_as_app.side_effect = self.effect
        self.effects = []
        self.pr = deepcopy(self.facts["pr"])
        self.checks = [{"name": name, "bucket": "pass"} for name in ("quick-authority", "trust-root-authority")]

    def read(self, args):
        endpoint = args[1]
        if args[:2] == ["pr", "checks"]:
            return deepcopy(self.checks)
        if "/git/matching-refs/" in endpoint or "/pulls?" in endpoint:
            return []
        if "/git/ref/heads/" in endpoint:
            return {"object": {"sha": "a" * 40}}
        if "/pulls/" in endpoint:
            return deepcopy(self.pr)
        raise AssertionError(args)

    def effect(self, args, *, operation):
        record = json.loads(self.path.read_text())
        self.assertEqual(record["effects"][-1]["state"], "pending", "journal must be durable BEFORE effect")
        self.assertEqual(len(self.source.call_args_list), len(self.effects) + 1)
        self.assertEqual(len(self.currency.call_args_list), len(self.effects) + 1)
        self.effects.append((args, operation))
        if args[:2] == ["pr", "merge"]:
            return ""
        payload = json.loads(Path(args[args.index("--input") + 1]).read_text())
        endpoint = args[3]
        if endpoint.endswith("/git/refs"):
            return json.dumps({"ref": payload["ref"], "object": {"sha": payload["sha"]}})
        if "/contents/" in endpoint:
            self.assertEqual(payload["branch"], self.publisher.branch)
            self.assertNotIn("sha", payload, "initial publication must never replace existing content")
            self.assertNotIn("author", payload)
            return json.dumps({"commit": {"sha": "b" * 40, "parents": [{"sha": "a" * 40}]}})
        if endpoint.endswith("/pulls"):
            self.assertEqual(payload["base"], "main")
            self.assertNotIn("labels", payload)
            return json.dumps(self.pr)
        raise AssertionError(args)

    def test_publish_has_three_separate_fresh_app_spends_and_durable_observations(self):
        result = self.publisher.publish()
        self.assertEqual(result, {"pr": 12, "head_sha": "b" * 40})
        self.assertEqual([operation for _, operation in self.effects], ["push_branch", "push_branch", "create_pr"])
        self.assertEqual([call.args[1] for call in self.currency.call_args_list], ["branch", "branch", "pull-request"])
        self.assertTrue(all(row["state"] == "observed" for row in json.loads(self.path.read_text())["effects"]))
        self.github.run.assert_not_called()

    def test_stop_or_source_movement_between_spends_prevents_the_next_post(self):
        for changed in ({"stop": {"state": "stopped", "issues": [1]}}, {"main_sha": "f" * 40}):
            with self.subTest(changed=changed):
                self.setUp()
                original = deepcopy(self.source.return_value)
                self.source.side_effect = [original, {**original, **changed}]
                with self.assertRaisesRegex(IntentRefused, "source or stop"):
                    self.publisher.publish()
                self.assertEqual(len(self.effects), 1)

    def test_foreign_payload_or_unreadable_currency_prevents_all_effects(self):
        self.currency.return_value["input_sha256"] = "f" * 64
        with self.assertRaises(IntentRefused):
            self.publisher.publish()
        self.currency.side_effect = TimeoutError("host unavailable")
        with self.assertRaises(TimeoutError):
            self.publisher.publish()
        self.github.run_as_app.assert_not_called()

    def test_unknown_post_outcome_is_durable_and_cannot_be_replayed(self):
        self.github.run_as_app.side_effect = TimeoutError("response lost after server may have acted")
        with self.assertRaisesRegex(IntentRefused, "uncertain"):
            self.publisher.publish()
        self.assertEqual(json.loads(self.path.read_text())["effects"][0]["state"], "uncertain")
        with self.assertRaises(IntentRefused):
            self.publisher.publish()
        self.assertEqual(self.github.run_as_app.call_count, 1)
        with self.assertRaisesRegex(IntentRefused, "already exists"):
            EffectJournal(self.path, self.manifest)

    def test_existing_branch_is_not_reset_or_reused(self):
        self.github.json.side_effect = None
        self.github.json.return_value = [{"ref": "refs/heads/" + self.publisher.branch}]
        with self.assertRaisesRegex(IntentRefused, "branch exists"):
            self.publisher.publish()
        self.github.run_as_app.assert_not_called()

    def test_merge_requires_both_real_required_authorities_then_fresh_exact_head_effect(self):
        self.publisher.merge(12, "b" * 40)
        args, operation = self.effects[0]
        self.assertEqual(operation, "merge_squash")
        self.assertIn("--match-head-commit", args)
        self.assertEqual(args[-1], "b" * 40)
        self.assertNotIn("--auto", args)
        self.assertNotIn("--admin", args)
        self.currency.assert_called_once_with(self.manifest, "merge")

    def test_pending_failed_missing_or_unrelated_checks_never_merge(self):
        for checks in ([], [{"name": "other", "bucket": "pass"}],
                       [{"name": name, "bucket": "fail"} for name in ("quick-authority", "trust-root-authority")]):
            self.checks = checks
            with self.subTest(checks=checks), self.assertRaises(IntentRefused):
                self.publisher.merge(12, "b" * 40)
        self.github.run_as_app.assert_not_called()

    def test_changed_pr_head_never_spends_merge_identity(self):
        self.pr["head"]["sha"] = "f" * 40
        with self.assertRaises(IntentRefused):
            self.publisher.merge(12, "b" * 40)
        self.github.run_as_app.assert_not_called()

    def test_post_merge_receipt_requires_same_tree_parent_and_current_main(self):
        merge = "f" * 40
        tree = "e" * 40
        commit = {"sha": merge, "tree": {"sha": tree}, "parents": [{"sha": "a" * 40}]}
        head = {"sha": "b" * 40, "tree": {"sha": tree}}
        pr = {**self.pr, "merged": True, "state": "closed", "merge_commit_sha": merge}
        main = {"object": {"sha": merge}}
        def observe():
            self.github.json.side_effect = [deepcopy(pr), deepcopy(commit), deepcopy(head), deepcopy(main)]
            return self.publisher.observe_merged(12, "b" * 40)
        receipt = observe()
        self.assertEqual(receipt["main_sha"], merge)
        self.assertEqual(receipt["tree_sha"], tree)
        self.assertFalse(receipt["product_complete"])
        for target, key, wrong in ((commit, "tree", {"sha": "d" * 40}),
                                   (commit, "parents", [{"sha": "d" * 40}]),
                                   (main, "object", {"sha": "d" * 40}), (pr, "merged", False)):
            original = deepcopy(target[key])
            target[key] = wrong
            with self.subTest(key=key), self.assertRaises(IntentRefused):
                observe()
            target[key] = original
        self.github.run_as_app.assert_not_called()

    def test_journal_write_failure_prevents_post_and_repeat_phase_is_refused(self):
        operation = Mock(return_value={"ack": True})
        from unittest.mock import patch
        with patch.object(self.journal, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.journal.spend("branch", operation)
        operation.assert_not_called()
        with self.assertRaises(IntentRefused):
            self.journal.spend("branch", operation)


if __name__ == "__main__":
    unittest.main()
