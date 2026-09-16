"""Read the real protected-input parser; unknown or changing remote facts fail closed."""
import base64
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.programme import ACTIVE_PATH, ProgrammeRefused
from factory_kernel.publication_source import observe_publication_source
from tests.factory import test_frontdoor_programme as review_tests


class PublicationSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = review_tests.ProgrammeReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.active = self.fixture.prepare()["input"]
        self.github = Mock(repository=self.fixture.store.repository)
        self.metadata = {"full_name": self.github.repository, "private": False, "default_branch": "main"}
        self.branch = {"protected": True, "commit": {"sha": "a" * 40}}
        self.branch_reads = 0
        self.tree_truncated = False
        self.github.programme_issues.return_value = []
        self.github.json.side_effect = self.read

    def read(self, args):
        endpoint = args[1]
        prefix = f"repos/{self.github.repository}"
        if endpoint == prefix:
            return deepcopy(self.metadata)
        if endpoint == prefix + "/branches/main":
            self.branch_reads += 1
            return deepcopy(self.branch)
        if endpoint == prefix + "/git/trees/" + "a" * 40 + "?recursive=1":
            return {"truncated": self.tree_truncated,
                    "tree": [{"path": ACTIVE_PATH, "mode": "100644", "sha": "b" * 40}] if self.active else []}
        if endpoint == prefix + "/git/blobs/" + "b" * 40:
            raw = json.dumps(self.active).encode()
            return {"encoding": "base64", "size": len(raw), "content": base64.b64encode(raw).decode()}
        raise AssertionError(args)

    def test_reads_existing_programme_without_effects_or_completion_claims(self):
        result = observe_publication_source(self.github)
        self.assertEqual(result["active_input"], self.active)
        self.assertEqual(result["stop"], {"state": "clear", "issues": []})
        self.assertEqual(result["visibility"], "public")
        self.assertNotIn("completion", result)
        self.github.run.assert_not_called()
        self.github.run_as_app.assert_not_called()

    def test_no_programme_is_established_by_complete_tree_not_a_missing_api_response(self):
        self.active = None
        self.assertIsNone(observe_publication_source(self.github)["active_input"])
        self.tree_truncated = True
        with self.assertRaises(ProgrammeRefused):
            observe_publication_source(self.github)

    def test_stop_is_reported_without_claiming_publication_permission(self):
        self.github.programme_issues.return_value = [
            {"number": 17, "state": "open", "labels": [{"name": "factory:stop"}]}]
        self.assertEqual(observe_publication_source(self.github)["stop"], {"state": "stopped", "issues": [17]})

    def test_unknown_visibility_foreign_repository_or_unprotected_source_refuses(self):
        for key, value in (("private", None), ("full_name", "another/repo"), ("default_branch", "other")):
            original = self.metadata[key]
            self.metadata[key] = value
            with self.subTest(key=key), self.assertRaises(IntentRefused):
                observe_publication_source(self.github)
            self.metadata[key] = original
        self.branch["protected"] = False
        with self.assertRaises(IntentRefused):
            observe_publication_source(self.github)

    def test_main_moving_while_reading_active_input_refuses(self):
        def read(args):
            value = self.read(args)
            if args[1].endswith("/branches/main") and self.branch_reads == 3:
                value["commit"]["sha"] = "c" * 40
            return value
        self.github.json.side_effect = read
        with self.assertRaisesRegex(IntentRefused, "changed during observation"):
            observe_publication_source(self.github)


if __name__ == "__main__":
    unittest.main()
