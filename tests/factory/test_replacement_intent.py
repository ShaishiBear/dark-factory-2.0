"""Owner-reviewed replacement facts survive crashes without granting execution authority."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
import os
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.decision_history import explain_history
from factory_kernel.execution_budget import ExecutionBudget
from factory_kernel.frontdoor_http import FrontDoorApplication
from factory_kernel.frontdoor_intent import IntentRefused, Principal
from factory_kernel.programme_runtime import ProgrammeQueue
from factory_kernel.replacement_intent import ReplacementIntents, OPERATION, plans
from tests.factory import test_programme_turnover as turnover


class ReplacementIntentTests(unittest.TestCase):
    def setUp(self):
        self.case = turnover.OwnerTurnoverTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.service = ReplacementIntents(self.case.publications, self.case.github)

    def command(self):
        return {"idempotency_key": "a" * 32, "expected_project_version": self.case.version,
                "review_sha256": self.case.review()["review_sha256"], "review": deepcopy(self.case.request)}

    def freeze(self, command=None, principal=None):
        return self.service.freeze("citations", command or self.command(), principal=principal or self.case.owner)

    def events(self):
        with self.case.store._locked("citations") as path:
            return self.case.store._read(path)

    def complete(self):
        queue = ProgrammeQueue(self.case.github, "main")
        queue.sync(Mock())
        self.case.github.rows[0]["state"] = "closed"
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}):
            queue.record_completion(self.case.github.issue(1), 9,
                {"head_sha": "a" * 40, "merge_sha": "b" * 40, "verdict": "verified"})
        queue.sync(Mock())

    def test_exact_plan_keeps_original_receipts_pending_work_and_budget_without_effects(self):
        self.complete()
        before = deepcopy((self.events(), self.case.github.rows, self.case.github.comments, self.case.github.source))
        result = self.freeze()
        plan = result["plan"]
        self.assertEqual(self.events()[:-1], before[0])
        self.assertEqual((self.case.github.rows, self.case.github.comments, self.case.github.source), before[1:])
        self.assertEqual(plan["recorded_project_version"], 4)
        self.assertEqual(plan["review"]["preserved_completed_work"][0]["completion"]["run"], 42)
        self.assertEqual(plan["review"]["pending_work"][0]["issue"], 2)
        self.assertEqual(plan["review"]["execution_budget"]["ledger"]["status"], "not-approved")
        self.assertEqual(plan["proposed_input"], self.case.proposed)
        self.assertEqual(plan["authority"], "frozen-plan-only")
        self.assertEqual(plan["qualification_status"], "UNPROVEN")
        self.assertFalse(plan["proof_reuse_allowed"])
        self.assertFalse(plan["activation_allowed"])
        budget = ExecutionBudget(self.case.store).snapshot("citations", principal=self.case.owner)
        self.assertEqual((budget["calls"], budget["reserved_microusd"], budget["allowance"]), (0, 0, None))
        history = explain_history(self.case.store, "citations", principal=self.case.owner)
        self.assertEqual(history["events"][-1]["operation"], OPERATION)
        self.assertEqual(history["replacement_intents"], [plan])

    def test_restart_lost_response_and_replay_return_one_historical_plan(self):
        command = self.command()
        initial = self.freeze(command)
        self.service = ReplacementIntents(self.case.publications, self.case.github)
        with patch("factory_kernel.replacement_intent.review_turnover", side_effect=AssertionError("replay observed remote state")):
            replay = self.freeze(command)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["plan"], initial["plan"])
        self.assertEqual(len(self.events()), 4)

    def test_changed_content_cannot_reuse_identity(self):
        command = self.command()
        self.freeze(command)
        command["review_sha256"] = "f" * 64
        with self.assertRaisesRegex(IntentRefused, "identity reused"):
            self.freeze(command)

    def test_worker_forged_review_and_stale_owner_version_cannot_freeze(self):
        command = self.command()
        with self.assertRaises(IntentRefused):
            self.freeze(command, Principal("worker", "proposal"))
        command["review_sha256"] = "0" * 64
        with self.assertRaisesRegex(IntentRefused, "reviewed plan"):
            self.freeze(command)
        command["expected_project_version"] = 2
        with self.assertRaisesRegex(IntentRefused, "stale"):
            self.freeze(command)
        self.assertEqual(len(self.events()), 3)

    def test_remote_changes_after_owner_review_require_a_new_review(self):
        command = self.command()
        ProgrammeQueue(self.case.github, "main").sync(Mock())
        with self.assertRaisesRegex(IntentRefused, "reviewed plan"):
            self.freeze(command)
        self.assertEqual(len(self.events()), 3)

    def test_completed_obligations_cannot_be_changed_even_with_matching_review_hash(self):
        self.complete()
        self.case.request["proposal"]["items"] = [{"id": "combined", "acceptance": ["AC1", "AC2"], "blocked_by": []}]
        with self.assertRaisesRegex(IntentRefused, "completed obligation"):
            self.freeze()

    def test_owner_or_spending_change_during_final_read_refuses_append(self):
        command = self.command()
        original = self.case.publications.review
        count = 0
        def changed(*args, **kwargs):
            nonlocal count
            value = original(*args, **kwargs)
            count += 1
            if count == 3:
                self.case.write("add-exploration", {"wording": "New evidence before replacement."})
            return value
        with patch.object(self.case.publications, "review", side_effect=changed):
            with self.assertRaisesRegex(IntentRefused, "spending changed"):
                self.freeze(command)
        self.assertEqual(len(self.service.snapshot("citations", principal=self.case.owner)), 0)

    def test_later_owner_history_marks_plan_stale_without_mutating_its_contents(self):
        command = self.command()
        original = self.freeze(command)["plan"]
        self.case.version = 4
        self.case.write("add-exploration", {"wording": "Further investigation."})
        historical = self.freeze(command)["plan"]
        self.assertEqual(historical["currency"], "owner-history-changed")
        self.assertEqual(historical["plan_sha256"], original["plan_sha256"])
        self.assertEqual(historical["review"], original["review"])
        self.assertFalse(historical["activation_allowed"])

    def test_parallel_freezes_append_at_most_one_record(self):
        command = self.command()
        def attempt():
            try:
                return self.freeze(command)
            except (IntentRefused, OSError):
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertTrue(any(result is not None for result in results))
        self.assertEqual(len(self.events()), 4)

    def test_content_hash_detects_damaged_plan_and_generic_commands_cannot_inject_it(self):
        self.freeze()
        events = self.events()
        events[-1]["command"]["payload"]["plan"]["qualification_status"] = "QUALIFIED"
        with self.assertRaises(IntentRefused):
            plans(events)
        with self.assertRaises(IntentRefused):
            self.case.store.execute("citations", {"idempotency_key": "inject", "expected_project_version": 4,
                "operation": OPERATION, "payload": events[-1]["command"]["payload"]}, principal=self.case.owner)

    def test_http_requires_owner_and_same_origin_and_exposes_recorded_plan(self):
        app = FrontDoorApplication(store=self.case.store, project="citations", token="a" * 64,
            origin="https://factory.example", github=self.case.github, labels={}, app_login=turnover.BOT, publication_key=b"x" * 32)
        command = self.command()
        def post(authorization, origin):
            body = canonical_bytes(command)
            response = {}
            environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/programme-replacement-intent",
                "HTTP_HOST": "factory.example", "HTTP_ORIGIN": origin, "HTTP_AUTHORIZATION": authorization,
                "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)), "wsgi.input": BytesIO(body)}
            result = b"".join(app(environ, lambda status, headers: response.update(status=status)))
            return response["status"], json.loads(result)
        self.assertEqual(post("", app.origin)[0], "401 Unauthorized")
        self.assertEqual(post("Bearer " + "a" * 64, "https://foreign.example")[0], "403 Forbidden")
        status, result = post("Bearer " + "a" * 64, app.origin)
        self.assertEqual(status, "200 OK")
        self.assertFalse(result["plan"]["activation_allowed"])
        self.assertEqual(app._snapshot()["replacement_intents"], [result["plan"]])


if __name__ == "__main__":
    unittest.main()
