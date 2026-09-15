"""An untrusted decomposition cannot replace or silently advance owner-approved scope."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore
from factory_kernel.frontdoor_programme import prepare_programme
from factory_kernel.programme import ProgrammeRefused, compile_programme
from tests.factory.test_frontdoor_intent import OWNER, REPO, WORKER, example_spec


class ProgrammeReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = IntentStore(Path(tmp.name), repository=REPO, owner=OWNER.identity)
        self.version = 0
        self.spec = example_spec()
        self.write("record-intent", {"wording": "Show me the cited words.\nPreserve playback."})
        self.propose()
        self.approve()
        self.request = self.request_for_latest()

    def write(self, operation, payload):
        result = self.store.execute("citations", {
            "idempotency_key": f"command-{self.version}", "expected_project_version": self.version,
            "operation": operation, "payload": payload,
        }, principal=OWNER)
        self.version = result["project_version"]
        return result

    def propose(self):
        return self.write("propose-spec", {"spec": self.spec, "assumptions": [],
                                          "open_questions": [], "technical_questions": []})

    def approve(self):
        draft = self.store.snapshot("citations", principal=OWNER)["draft"]
        return self.write("approve-spec", {"draft_version": draft["draft_version"],
                                           "spec_sha256": draft["spec_sha256"],
                                           "wording": "Approve this exact citation scope."})

    def request_for_latest(self):
        state = self.store.snapshot("citations", principal=OWNER)
        approval = state["approvals"][-1]
        return {"expected_project_version": self.version, "approval_version": approval["project_version"],
                "spec_sha256": approval["spec_sha256"],
                "proposal": {"spec_sha256": approval["spec_sha256"], "items": [
                    {"id": "snippet", "acceptance": ["AC1"], "blocked_by": []}]}}

    def prepare(self, request=None, principal=OWNER):
        return prepare_programme(self.store, "citations", request or self.request,
                                 principal=principal, app_login="factory[bot]")

    def test_review_compiles_from_store_and_retains_original_approval_without_effect(self):
        before = self.store.snapshot("citations", principal=OWNER)
        review = self.prepare()
        compiled = compile_programme(review["input"], repository=REPO)
        self.assertEqual(compiled.sha256, review["programme_sha256"])
        self.assertEqual(review["input_sha256"], sha256_value(review["input"]))
        self.assertEqual(review["input"]["spec"], self.spec)
        self.assertEqual(review["approval"]["original_intent"], "Show me the cited words.\nPreserve playback.")
        self.assertEqual(review["approval"]["wording"], "Approve this exact citation scope.")
        self.assertEqual(review["activation"], "requires-protected-main-review")
        review["input"]["spec"]["outcome"] = "Mutated caller object"
        self.assertEqual(self.store.snapshot("citations", principal=OWNER), before)

    def test_proposal_worker_cannot_prepare_an_activation_artifact(self):
        with self.assertRaisesRegex(IntentRefused, "only the owner"):
            self.prepare(principal=WORKER)

    def test_stale_project_or_wrong_approval_identity_refuses(self):
        for field, value in (("expected_project_version", 2), ("expected_project_version", True),
                             ("approval_version", 2), ("approval_version", True), ("spec_sha256", "a" * 64)):
            request = deepcopy(self.request)
            request[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(IntentRefused):
                self.prepare(request)

    def test_unapproved_draft_never_replaces_approved_spec(self):
        self.spec["revision"] = 2
        self.spec["outcome"] = "A proposed changed outcome"
        self.propose()
        self.request["expected_project_version"] = self.version
        try:
            review = self.prepare()
        except ProgrammeRefused as exc:
            self.fail(f"an unapproved draft must not invalidate review of approved scope: {exc}")
        self.assertEqual(review["input"]["spec"]["revision"], 1)
        self.approve()
        self.request["expected_project_version"] = self.version
        with self.assertRaisesRegex(IntentRefused, "latest exact"):
            self.prepare()
        self.assertEqual(self.prepare(self.request_for_latest())["input"]["spec"]["revision"], 2)

    def test_caller_spec_actor_app_or_extra_scope_is_not_accepted(self):
        for key in ("spec", "actor", "app_login", "approved"):
            with self.subTest(key=key), self.assertRaises(IntentRefused):
                self.prepare({**self.request, key: "caller-controlled"})
        for mutation in ("hash", "unknown-acceptance", "extra-instructions"):
            request = deepcopy(self.request)
            if mutation == "hash":
                request["proposal"]["spec_sha256"] = "f" * 64
            elif mutation == "unknown-acceptance":
                request["proposal"]["items"][0]["acceptance"] = ["UNAPPROVED"]
            else:
                request["proposal"]["items"][0]["instructions"] = "Invent more work"
            with self.subTest(mutation=mutation), self.assertRaises(ProgrammeRefused):
                self.prepare(request)

    def test_missing_approval_and_cross_project_reads_refuse(self):
        with self.assertRaisesRegex(IntentRefused, "approval is required"):
            prepare_programme(
                self.store, "empty", {**self.request, "expected_project_version": 0},
                principal=OWNER, app_login="factory[bot]",
            )
        with self.assertRaises(IntentRefused):
            prepare_programme(self.store, "../citations", self.request,
                              principal=OWNER, app_login="factory[bot]")


if __name__ == "__main__":
    unittest.main()
