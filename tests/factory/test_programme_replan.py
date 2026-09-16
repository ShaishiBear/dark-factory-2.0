"""A replanning review exposes changes in HOW without approving WHAT or spending effects."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from factory_kernel.canonical import sha256_value
from factory_kernel.programme import ProgrammeRefused, compile_programme
from factory_kernel.programme_replan import review_replan
from factory_kernel.publication_policy import PROJECT
from tests.factory.test_frontdoor_intent import REPO, example_spec
from tests.factory import test_publication_dispatch as dispatch_fixtures


class ReplanReviewTests(unittest.TestCase):
    def setUp(self):
        spec = example_spec()
        spec["requirements"][0]["acceptance"].append({"id": "AC2", "text": "Restore keyboard focus."})
        self.current = {"version": "1.0", "spec": spec, "app_login": "factory[bot]",
                        "proposal": {"spec_sha256": sha256_value(spec), "items": [
                            {"id": "snippet", "acceptance": ["AC1"], "blocked_by": []},
                            {"id": "keyboard", "acceptance": ["AC2"], "blocked_by": ["snippet"]}]}}
        self.proposed = deepcopy(self.current)

    def review(self):
        return review_replan(self.current, self.proposed, repository=REPO, source_sha="b" * 40)

    def test_equal_graph_is_unchanged_even_with_different_input_order(self):
        self.proposed["proposal"]["items"].reverse()
        result = self.review()
        self.assertEqual(result["disposition"], "unchanged")
        self.assertEqual(result["current_programme_sha256"], result["proposed_programme_sha256"])

    def test_new_input_versions_require_explicit_review_adapter_even_if_compiler_can_accept_them(self):
        compiled = compile_programme(self.current, repository=REPO)
        for value in (self.current, self.proposed):
            value["version"] = "1.1"
            with patch("factory_kernel.programme_replan.compile_programme", return_value=compiled) as compiler:
                with self.assertRaisesRegex(ProgrammeRefused, "input v1.0"):
                    self.review()
                compiler.assert_not_called()
            value["version"] = "1.0"

    def test_merge_exposes_each_acceptance_owner_and_preserves_input_and_historical_proof(self):
        self.proposed["proposal"]["items"] = [{"id": "inspection", "acceptance": ["AC1", "AC2"], "blocked_by": []}]
        original = deepcopy((self.current, self.proposed))
        result = self.review()
        self.assertEqual(result["disposition"], "decomposition-change")
        self.assertEqual(result["added_items"], ["inspection"])
        self.assertEqual(result["retired_items"], ["keyboard", "snippet"])
        self.assertEqual(result["coverage"], [
            {"acceptance": "AC1", "current_item": "snippet", "proposed_item": "inspection"},
            {"acceptance": "AC2", "current_item": "keyboard", "proposed_item": "inspection"}])
        self.assertEqual(result["authority"], "review-only")
        self.assertEqual(result["activation"], "requires-governed-transition")
        self.assertEqual(result["evidence"], "historical-outcomes-remain-bound-to-original-programme")
        self.assertEqual((self.current, self.proposed), original)

    def test_same_named_item_with_changed_dependencies_is_not_unchanged(self):
        self.proposed["proposal"]["items"][1]["blocked_by"] = []
        result = self.review()
        self.assertEqual(result["changed_items"], ["keyboard"])
        self.assertEqual(result["dependency_changes"], [{"item": "keyboard", "current": ["snippet"], "proposed": []}])
        self.assertEqual(result["disposition"], "decomposition-change")

    def test_scope_changes_cannot_be_laundered_as_replanning_even_with_valid_new_hash(self):
        for field in ("revision", "constraints", "non_goals", "requirements"):
            with self.subTest(field=field):
                self.proposed = deepcopy(self.current)
                if field == "revision":
                    self.proposed["spec"][field] = 2
                elif field == "requirements":
                    self.proposed["spec"][field][0]["acceptance"][0]["text"] = "Different intent."
                else:
                    self.proposed["spec"][field] = ["Different scope."]
                self.proposed["proposal"]["spec_sha256"] = sha256_value(self.proposed["spec"])
                with self.assertRaisesRegex(ProgrammeRefused, "approved scope"):
                    self.review()

    def test_identity_injection_and_lost_duplicate_or_cyclic_coverage_refuse(self):
        bad = []
        changed = deepcopy(self.current)
        changed["app_login"] = "other[bot]"
        bad.append(changed)
        changed = deepcopy(self.current)
        changed["proposal"]["items"].pop()
        bad.append(changed)
        changed = deepcopy(self.current)
        changed["proposal"]["items"][1]["acceptance"] = ["AC1"]
        bad.append(changed)
        changed = deepcopy(self.current)
        changed["proposal"]["items"][0]["blocked_by"] = ["keyboard"]
        bad.append(changed)
        changed = deepcopy(self.current)
        changed["continuation_remaining"] = 8
        bad.append(changed)
        changed = deepcopy(self.current)
        changed["proof_reuse"] = True
        bad.append(changed)
        for proposed in bad:
            with self.subTest(proposed=proposed), self.assertRaises(ProgrammeRefused):
                review_replan(self.current, proposed, repository=REPO, source_sha="b" * 40)
        with self.assertRaises(ProgrammeRefused):
            review_replan(self.current, self.current, repository=REPO, source_sha="main")

    def test_live_preview_distinguishes_renamed_decomposition_without_a_reservation_or_dispatch(self):
        fixture = dispatch_fixtures.PublicationDispatchTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        active = fixture.service.preview(PROJECT, fixture.review, principal=fixture.owner)["input"]
        active["proposal"]["items"][0]["id"] = "existing-snippet"
        fixture.observation["active_input"] = active
        preview = fixture.service.preview(PROJECT, fixture.review, principal=fixture.owner)
        self.assertEqual(preview["state"], "already-active")
        self.assertEqual(preview["replanning"]["disposition"], "decomposition-change")
        self.assertEqual(preview["replanning"]["retired_items"], ["existing-snippet"])
        self.assertEqual(list(fixture.service.requests.directory.glob("*.json")), [])
        fixture.github.run.assert_not_called()
        fixture.github.run_as_app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
