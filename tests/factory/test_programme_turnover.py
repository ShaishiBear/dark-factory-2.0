"""Turnover observations preserve original completion authority and cannot activate work."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
from factory_kernel.programme import ProgrammeRefused
from factory_kernel.programme_runtime import ProgrammeQueue
from factory_kernel.programme_turnover import ACTIVE_RUN_STATES, observe_turnover, review_turnover
from factory_kernel.publication_request import PublicationRequests
from tests.factory.test_factory_programme import FakeGitHub, REPO, BOT


class TurnoverGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.pulls = []
        self.runs = {status: [] for status in ACTIVE_RUN_STATES}
        self.total_offset = 0

    def json(self, args):
        endpoint = args[1]
        if endpoint == f"repos/{REPO}":
            return {"full_name": REPO, "default_branch": "main", "private": False}
        if endpoint.endswith("/pulls?state=open&per_page=100"):
            return deepcopy(self.pulls)
        if "/actions/workflows/" in endpoint:
            status = endpoint.split("status=")[1].split("&")[0]
            rows = self.runs[status]
            return {"total_count": len(rows) + self.total_offset, "workflow_runs": deepcopy(rows)}
        return super().json(args)


class TurnoverTests(unittest.TestCase):
    def setUp(self):
        self.github = TurnoverGitHub()
        self.queue = ProgrammeQueue(self.github, "main")
        self.proposed = deepcopy(self.github.source)
        self.proposed["proposal"]["items"][1]["id"] = "close-again"

    def observe(self):
        return observe_turnover(self.github, self.proposed)

    def complete_first(self):
        self.queue.sync(Mock())
        self.github.rows[0]["state"] = "closed"
        with patch.dict("os.environ", {"GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}):
            self.queue.record_completion(self.github.issue(1), 9,
                {"head_sha": "a" * 40, "merge_sha": "b" * 40, "verdict": "verified"})

    def test_empty_frontier_is_observation_not_a_lock_permission_or_zero_budget(self):
        original = deepcopy(self.github.source)
        result = self.observe()
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["preserved_completed_work"], [])
        self.assertEqual(len(result["pending_work"]), 2)
        self.assertEqual(result["authority"], "review-only")
        self.assertEqual(result["activation"], "requires-persistent-fence-and-serialized-transition")
        self.assertEqual(result["execution_budget"], {"status": "requires-cumulative-ledger",
                         "refund_allowed": False, "reset_allowed": False})
        self.assertEqual(result["qualification_status"], "UNPROVEN")
        self.assertIs(result["proof_reuse_allowed"], False)
        self.assertEqual(self.github.source, original)
        self.assertEqual(self.github.rows, [])

    def test_verified_completed_work_keeps_original_receipt_and_pending_work_separate(self):
        self.complete_first()
        self.queue.sync(Mock())
        original = deepcopy((self.github.rows, self.github.comments))
        result = self.observe()
        completed = result["preserved_completed_work"][0]
        self.assertEqual(completed["completion"]["programme"], self.queue.current().sha256)
        self.assertEqual(completed["completion"]["run"], 42)
        self.assertEqual(completed["issue"], 1)
        self.assertEqual(result["pending_work"][0]["issue"], 2)
        self.assertEqual(result["blockers"], [{"kind": "open-pending-work", "item_id": "close", "issue": 2}])
        self.assertEqual((self.github.rows, self.github.comments), original)

    def test_completed_item_cannot_be_renamed_repartitioned_or_reordered_as_preserved(self):
        self.complete_first()
        self.proposed["proposal"]["items"] = [{"id": "combined", "acceptance": ["AC1", "AC2"], "blocked_by": []}]
        result = self.observe()
        self.assertEqual(result["blockers"], [{"kind": "completed-work-changed", "item_id": "open"}])
        self.assertEqual(result["preserved_completed_work"][0]["completion"]["item"], "open")

    def test_closure_forgery_edited_receipt_and_failed_run_do_not_preserve_completion(self):
        self.complete_first()
        original = deepcopy(self.github.comments)
        for change in (lambda: self.github.comments.clear(),
                       lambda: self.github.comments[0]["user"].update(login="attacker"),
                       lambda: self.github.comments[0].update(updated_at="2026-09-16T01:00:00Z"),
                       lambda: self.github.run.update(conclusion="failure")):
            self.github.comments = deepcopy(original)
            change()
            self.assertEqual(self.observe()["preserved_completed_work"], [])

    def test_all_active_run_states_and_open_app_pulls_are_explicit_blockers(self):
        for number, status in enumerate(ACTIVE_RUN_STATES, 1):
            self.github.runs[status] = [{"id": number, "run_attempt": 1, "status": status,
                "head_sha": "c" * 40, "path": ".github/workflows/dark-factory-worker.yml"}]
        self.github.pulls = [{"number": 17, "state": "open", "user": {"login": BOT}, "head": {"sha": "a" * 40}}]
        result = self.observe()
        self.assertEqual([row["run_id"] for row in result["blockers"] if row["kind"] == "active-worker"], list(range(1, 6)))
        self.assertIn({"kind": "open-app-pull", "pr": 17}, result["blockers"])

    def test_incomplete_duplicate_and_malformed_platform_inventories_refuse(self):
        self.github.total_offset = 1
        with self.assertRaises(IntentRefused):
            self.observe()
        self.github.total_offset = 0
        self.github.pulls = [{}] * 100
        with self.assertRaises(IntentRefused):
            self.observe()
        self.github.pulls = [{"number": 17, "state": "open", "user": {"login": BOT}, "head": {"sha": "a" * 40}}] * 2
        with self.assertRaises(IntentRefused):
            self.observe()
        self.github.pulls = []
        self.github.runs["queued"] = [{"id": True, "run_attempt": 1, "status": "queued"}]
        with self.assertRaises(IntentRefused):
            self.observe()

    def test_changed_completion_and_source_during_read_refuse(self):
        self.complete_first()
        original = self.github.json
        reads = 0
        def unstable(args):
            nonlocal reads
            if "/comments?" in args[1]:
                reads += 1
                if reads == 2:
                    self.github.run["conclusion"] = "failure"
            return original(args)
        with patch.object(self.github, "json", side_effect=unstable):
            with self.assertRaisesRegex(IntentRefused, "execution changed"):
                self.observe()
        from factory_kernel.publication_source import observe_publication_source
        source = observe_publication_source(self.github)
        changed = {**source, "main_sha": "d" * 40}
        with self.assertRaisesRegex(IntentRefused, "execution changed"):
            observe_turnover(self.github, self.proposed, source=Mock(side_effect=[source, changed]))

    def test_missing_unchanged_foreign_scope_or_identity_cannot_be_replacement(self):
        with self.assertRaises(IntentRefused):
            observe_turnover(self.github, self.github.source)
        self.proposed["spec"]["revision"] = 2
        self.proposed["proposal"]["spec_sha256"] = sha256_value(self.proposed["spec"])
        with self.assertRaisesRegex(ProgrammeRefused, "scope"):
            self.observe()
        self.proposed = deepcopy(self.github.source)
        self.proposed["app_login"] = "other[bot]"
        with self.assertRaisesRegex(ProgrammeRefused, "identity"):
            self.observe()
        self.github.source = None
        with self.assertRaises(IntentRefused):
            self.observe()


class OwnerTurnoverTests(unittest.TestCase):
    def setUp(self):
        fixture = TurnoverTests()
        fixture.setUp()
        self.github, self.proposed = fixture.github, fixture.proposed
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.store = IntentStore(Path(directory), repository=REPO, owner="owner")
        self.owner = Principal("owner", "owner")
        self.version = 0
        self.write("record-intent", {"wording": "Improve citations."})
        self.write("propose-spec", {"spec": self.github.source["spec"], "assumptions": [], "open_questions": [], "technical_questions": []})
        state = self.store.snapshot("citations", principal=self.owner)
        self.write("approve-spec", {"draft_version": state["draft"]["draft_version"],
            "spec_sha256": state["draft"]["spec_sha256"], "wording": "Approve this scope."})
        self.request = {"expected_project_version": 3, "approval_version": 3,
            "spec_sha256": sha256_value(self.github.source["spec"]), "proposal": self.proposed["proposal"]}
        self.publications = PublicationRequests(self.store, app_login=BOT)

    def write(self, operation, payload):
        result = self.store.execute("citations", {"idempotency_key": "command-" + str(self.version),
            "expected_project_version": self.version, "operation": operation, "payload": payload}, principal=self.owner)
        self.version = result["project_version"]

    def review(self, principal=None):
        return review_turnover(self.publications, self.github, "citations", self.request, principal=principal or self.owner)

    def test_owner_review_is_read_only_and_does_not_invent_zero_exploration_spend(self):
        before = self.store.snapshot("citations", principal=self.owner)
        result = self.review()
        self.assertEqual(result["project_version"], 3)
        self.assertIsNone(result["exploration_budget"]["budget"])
        digest = result.pop("review_sha256")
        self.assertEqual(digest, sha256_value(result))
        self.assertEqual(before, self.store.snapshot("citations", principal=self.owner))
        self.assertEqual(list(self.publications.directory.glob("*.json")), [])

    def test_worker_stale_project_and_new_unapproved_intent_refuse(self):
        with self.assertRaises(IntentRefused):
            self.review(Principal("owner", "worker"))
        self.request["expected_project_version"] = 2
        with self.assertRaises(IntentRefused):
            self.review()
        self.write("record-intent", {"wording": "A different outcome."})
        self.request["expected_project_version"] = 4
        with self.assertRaisesRegex(IntentRefused, "new intent"):
            self.review()

    def test_owner_change_during_remote_reads_refuses_old_review(self):
        from factory_kernel.programme_turnover import observe_turnover as observe
        def changed(*args, **kwargs):
            result = observe(*args, **kwargs)
            self.write("record-intent", {"wording": "A changed outcome."})
            return result
        with patch("factory_kernel.programme_turnover.observe_turnover", side_effect=changed):
            with self.assertRaises(IntentRefused):
                self.review()

    def test_cumulative_reservations_and_uncertain_spend_survive_review_without_refund(self):
        from factory_kernel.exploration import Exploration
        from factory_kernel.exploration_reasoner import ExplorationReasoner
        from tests.factory.test_exploration import policy
        context = {"commit": "a" * 40, "files": {}, "policies": {},
                   "coverage": "selected-committed-source-only", "proof_status": "not-established"}
        context["identity"] = sha256_value(context)
        engine = Exploration(self.store, lambda: deepcopy(context), check_stop=Mock(), app_login=BOT)
        engine.open("citations", {"idempotency_key": "open", "expected_project_version": 3,
            "session_id": "choice", "request": {"question": "Which architecture?",
            "parent_session": None, "policy": policy()}}, principal=self.owner)
        provider = Mock(config=None)
        provider.run.side_effect = TimeoutError("response was lost")
        result = ExplorationReasoner(engine, provider).advance("citations", {
            "idempotency_key": "reason", "expected_project_version": 4, "session_id": "choice",
            "request": {"purpose": "Compare alternatives.", "max_usd": 1}}, principal=self.owner)
        self.request["expected_project_version"] = result["state"]["project_version"]
        report = self.review()
        budget = report["exploration_budget"]["budget"]
        self.assertEqual(budget["calls"], 1)
        self.assertEqual(budget["usd"], 1)
        self.assertTrue(budget["uncertain"])
        reservation = report["exploration_budget"]["reservations"][0]["reservations"][0]
        self.assertEqual(reservation["usd"], 1)
        self.assertNotIn("payload", reservation)
        self.assertEqual(self.review()["exploration_budget"], report["exploration_budget"])
        provider.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
