"""Intent storage tests exercise persistence, approval identity and conflict boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
from factory_kernel.programme import ProgrammeRefused, compile_spec

REPO = "owner/product"


def example_spec():
    return {"id": "citations", "revision": 1, "repository": REPO,
            "title": "Inspect citations", "outcome": "Viewers can inspect cited words.",
            "requirements": [{"id": "R1", "acceptance": [
                {"id": "AC1", "text": "Opening a citation displays its transcript snippet."}]}],
            "constraints": ["Preserve video playback."], "non_goals": ["No sharing."]}

OWNER = Principal("maintainer", "owner")
WORKER = Principal("intake-agent", "proposal")


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = IntentStore(self.directory, repository=REPO, owner=OWNER.identity)
        self.version = 0

    def command(self, operation, payload, *, key=None, principal=OWNER):
        command = {"idempotency_key": key or f"command-{self.version}",
                   "expected_project_version": self.version, "operation": operation, "payload": payload}
        result = self.store.execute("citations", command, principal=principal)
        self.version = result["project_version"]
        return result

    def draft(self, **overrides):
        self.command("record-intent", {"wording": "Show me the cited words.\nKeep video playback."})
        payload = {"spec": example_spec(), "assumptions": ["Keep the current modal."],
                   "open_questions": [], "technical_questions": ["Choose a focus control."]}
        payload.update(overrides)
        return self.command("propose-spec", payload, principal=WORKER)

    def approve(self, draft):
        return self.command("approve-spec", {"draft_version": draft["draft_version"],
                                             "spec_sha256": draft["spec_sha256"],
                                             "wording": "Approve this scope."})

    def test_complete_intent_needs_no_question_and_explicit_approval_is_durable(self):
        draft = self.draft()["draft"]
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["approvals"], [])
        approved = self.approve(draft)
        reopened = IntentStore(self.directory, repository=REPO, owner=OWNER.identity)
        self.assertEqual(reopened.snapshot("citations", principal=OWNER), approved)
        self.assertEqual(approved["ledger"][0]["wording"], "Show me the cited words.\nKeep video playback.")
        self.assertEqual(approved["approvals"][0]["spec_sha256"], draft["spec_sha256"])
        self.assertEqual(approved["approvals"][0]["actor"], {"identity": "maintainer", "role": "owner"})
        self.assertEqual(approved["execution_status"], "not-activated-by-intake")

    def test_worker_cannot_approve_or_impersonate_the_owner(self):
        draft = self.draft()["draft"]
        for principal in (WORKER, Principal("attacker", "owner"), Principal("maintainer", "anonymous"), None):
            with self.subTest(principal=principal), self.assertRaises(IntentRefused):
                self.command("approve-spec", {"draft_version": draft["draft_version"],
                                              "spec_sha256": draft["spec_sha256"], "wording": "Approved"},
                             principal=principal)
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["approvals"], [])

    def test_unanswered_product_question_blocks_but_technical_uncertainty_does_not(self):
        draft = self.draft(open_questions=["Should this be visible to other users?"])["draft"]
        with self.assertRaisesRegex(IntentRefused, "product questions"):
            self.approve(draft)
        revised = {key: draft[key] for key in ("spec", "assumptions", "open_questions", "technical_questions")}
        revised["open_questions"] = []
        ready = self.command("propose-spec", revised, principal=WORKER)["draft"]
        self.assertEqual(len(self.approve(ready)["approvals"]), 1)

    def test_changed_draft_or_new_intent_invalidates_old_approval(self):
        old = self.draft()["draft"]
        proposed = {key: old[key] for key in ("spec", "assumptions", "open_questions", "technical_questions")}
        proposed["spec"]["title"] = "A revised title"
        newer = self.command("propose-spec", proposed, principal=WORKER)["draft"]
        with self.assertRaisesRegex(IntentRefused, "current reviewed draft"):
            self.approve(old)
        self.command("record-intent", {"wording": "Actually, change the behavior."})
        with self.assertRaisesRegex(IntentRefused, "current reviewed draft"):
            self.approve(newer)

    def test_wrong_hash_and_double_approval_are_refused(self):
        draft = self.draft()["draft"]
        with self.assertRaisesRegex(IntentRefused, "current reviewed draft"):
            self.approve({**draft, "spec_sha256": "f" * 64})
        self.approve(draft)
        with self.assertRaisesRegex(IntentRefused, "already approved"):
            self.approve(draft)

    def test_exploration_never_changes_approved_scope_and_revisions_preserve_history(self):
        draft = self.draft()["draft"]
        first = deepcopy(self.approve(draft)["approvals"][0])
        state = self.command("add-exploration", {"wording": "Could we add sharing?"})
        self.assertEqual(state["approvals"], [first])
        spec = deepcopy(first["spec"])
        spec["revision"] = 2
        spec["title"] = "Revised citation navigation"
        next_draft = self.draft(spec=spec)["draft"]
        result = self.approve(next_draft)
        self.assertEqual(result["approvals"][0], first)
        self.assertEqual(result["approvals"][1]["spec"]["revision"], 2)

    def test_retry_after_lost_response_returns_original_outcome_without_another_event(self):
        command = {"idempotency_key": "once", "expected_project_version": 0,
                   "operation": "record-intent", "payload": {"wording": "Original wording"}}
        first = self.store.execute("citations", command, principal=OWNER)
        self.version = 1
        self.command("add-exploration", {"wording": "A later question"})
        self.assertEqual(self.store.execute("citations", command, principal=OWNER), first)
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["project_version"], 2)
        for modified, actor in (({**command, "payload": {"wording": "Different"}}, OWNER),
                                (command, Principal("different", "proposal"))):
            with self.assertRaises(IntentRefused):
                self.store.execute("citations", modified, principal=actor)

    def test_stale_writer_cannot_overwrite_newer_intent(self):
        self.command("record-intent", {"wording": "Original"})
        before = (self.directory / "citations.json").read_bytes()
        self.version = 0
        with self.assertRaisesRegex(IntentRefused, "stale project version"):
            self.command("record-intent", {"wording": "Stale edit"}, key="different")
        self.assertEqual((self.directory / "citations.json").read_bytes(), before)

    def test_atomic_write_failure_preserves_previous_history(self):
        self.command("record-intent", {"wording": "Original"})
        before = (self.directory / "citations.json").read_bytes()
        with patch("factory_kernel.frontdoor_intent.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.command("record-intent", {"wording": "Next"})
        self.assertEqual((self.directory / "citations.json").read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), ["citations.json", "citations.lock"])

    def test_simultaneous_writer_is_refused_by_the_store_lock(self):
        with self.store._locked("citations"):
            with self.assertRaises(OSError):
                self.command("record-intent", {"wording": "Concurrent"})
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["project_version"], 0)

    def test_corrupt_history_and_cross_repository_reads_refuse(self):
        self.draft()
        path = self.directory / "citations.json"
        original = path.read_bytes()
        rows = json.loads(original)
        rows[0]["command"]["payload"]["wording"] = "Changed history"
        path.write_text(json.dumps(rows), encoding="utf-8")
        with self.assertRaisesRegex(IntentRefused, "history cannot be verified"):
            self.store.snapshot("citations", principal=OWNER)
        path.write_bytes(original)
        other = IntentStore(self.directory, repository="other/repo", owner=OWNER.identity)
        with self.assertRaises(IntentRefused):
            other.snapshot("citations", principal=OWNER)

    def test_unknown_fields_invalid_paths_and_non_json_values_fail_closed(self):
        for project in ("../escape", "other/repo", "Uppercase", "", "/absolute"):
            with self.subTest(project=project), self.assertRaises(IntentRefused):
                self.store.snapshot(project, principal=OWNER)
        with self.assertRaises(IntentRefused):
            self.command("record-intent", {"wording": "Original", "approved": True})
        self.version = True
        with self.assertRaises(IntentRefused):
            self.command("record-intent", {"wording": "Original"})

    def test_spec_compiler_is_shared_and_returns_detached_data(self):
        original = example_spec()
        compiled = compile_spec(original, repository=REPO)
        original["requirements"][0]["acceptance"][0]["text"] = "Mutated later"
        self.assertNotEqual(compiled, original)
        with self.assertRaises(ProgrammeRefused):
            self.draft(spec={**compiled, "approved": True})


if __name__ == "__main__":
    unittest.main()
