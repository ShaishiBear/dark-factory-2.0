"""The owner stop path uses the existing remote stop, never a second execution queue."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from factory_kernel import frontdoor_control as control
from factory_kernel.github_cli import GitHubClient

REQUEST = "a" * 32
REASON = "Pause this programme."


class OwnerStopTests(unittest.TestCase):
    def client(self, rows=()):
        client = GitHubClient("owner/product", cwd=".")
        client.programme_issues = Mock(return_value=list(rows))
        client.run_as_app = Mock()
        return client

    def row(self, **changes):
        return {"number": 42, "title": "Owner requested factory stop", "state": "open",
                "body": f"<!-- dark-factory-owner-stop:{REQUEST} -->\n\n{REASON}\n",
                "labels": [{"name": "factory:stop"}], **changes}

    def test_labelled_post_is_atomic_and_requires_observed_stop_before_claiming_stopped(self):
        client = self.client()
        captured = []

        def spend(argv, *, operation):
            captured.append((argv[:4], operation, json.loads(Path(argv[-1]).read_text())))
            return '{"number": 42}'

        client.run_as_app.side_effect = spend
        result = control.request_stop(client, request_id=REQUEST, reason=REASON)
        self.assertEqual(result["state"], "stop-post-acknowledged")
        self.assertEqual(captured, [(["api", "repos/owner/product/issues", "--method", "POST"],
                                     "request_emergency_stop", {"title": self.row()["title"],
                                     "body": self.row()["body"], "labels": ["factory:stop"]})])
        self.assertEqual(control.stop_status(client), {"state": "clear", "issues": []})
        client.programme_issues.return_value = [self.row()]
        self.assertEqual(control.stop_status(client), {"state": "stopped", "issues": [42]})

    def test_existing_stop_or_replayed_cleared_request_never_posts_again(self):
        for row, state in ((self.row(body="A different stop"), "already-stopped"),
                           (self.row(), "already-recorded"),
                           (self.row(state="closed", labels=[]), "already-recorded")):
            client = self.client([row])
            with self.subTest(row=row):
                self.assertEqual(control.request_stop(client, request_id=REQUEST, reason=REASON)["state"], state)
                client.run_as_app.assert_not_called()

    def test_duplicate_or_edited_request_identity_refuses(self):
        for rows in ([self.row(), self.row(number=43)], [self.row(body=self.row()["body"] + "edited")]):
            client = self.client(rows)
            with self.assertRaises(control.StopRefused):
                control.request_stop(client, request_id=REQUEST, reason=REASON)
            client.run_as_app.assert_not_called()

    def test_invalid_input_or_partial_inventory_cannot_spend(self):
        for request_id, reason in (("bad", REASON), (REQUEST, ""), (REQUEST, "x" * 2001),
                                   (REQUEST, "<!-- forged -->")):
            client = self.client()
            with self.subTest(request_id=request_id, reason=reason), self.assertRaises(control.StopRefused):
                control.request_stop(client, request_id=request_id, reason=reason)
            client.run_as_app.assert_not_called()
        client = self.client()
        client.programme_issues.side_effect = RuntimeError("partial inventory")
        with self.assertRaisesRegex(RuntimeError, "partial inventory"):
            control.request_stop(client, request_id=REQUEST, reason=REASON)
        client.run_as_app.assert_not_called()

    def test_uncertain_post_is_not_retried(self):
        client = self.client()
        client.run_as_app.side_effect = RuntimeError("response lost")
        with self.assertRaisesRegex(RuntimeError, "response lost"):
            control.request_stop(client, request_id=REQUEST, reason=REASON)
        self.assertEqual(client.run_as_app.call_count, 1)

    def test_stop_spend_requires_fresh_app_identity_without_personal_fallback(self):
        client = self.client()
        with patch.dict("os.environ", {"GH_TOKEN": "ordinary"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "requires DARK_FACTORY_APP_TOKEN"):
                client._autonomous_identity("request_emergency_stop")
        with patch.dict("os.environ", {"DARK_FACTORY_APP_TOKEN": "app"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "does not state"):
                client._autonomous_identity("request_emergency_stop")

    def test_workflow_entry_refuses_wrong_actor_ref_event_or_repository(self):
        valid = {"GITHUB_ACTOR": "owner", "GITHUB_REPOSITORY_OWNER": "owner",
                 "GITHUB_REPOSITORY": "owner/dark-factory-2.0", "GITHUB_REF": "refs/heads/main",
                 "GITHUB_EVENT_NAME": "workflow_dispatch", "STOP_REQUEST_ID": REQUEST, "STOP_REASON": REASON}
        with patch.object(control, "request_stop", return_value={"state": "stop-post-acknowledged"}) as stop:
            with patch.dict("os.environ", valid, clear=True):
                self.assertEqual(control.main(), 0)
            stop.assert_called_once()
            stop.reset_mock()
            for field, value in (("GITHUB_ACTOR", "agent[bot]"), ("GITHUB_REF", "refs/heads/feature"),
                                 ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REPOSITORY", "owner/other")):
                with self.subTest(field=field), patch.dict("os.environ", {**valid, field: value}, clear=True):
                    with self.assertRaises(control.StopRefused):
                        control.main()
                stop.assert_not_called()

    def test_workflow_mints_only_after_owner_main_guard_and_never_interpolates_reason_in_shell(self):
        workflow = (Path(__file__).parents[2] / ".github/workflows/dark-factory-owner-stop.yml").read_text()
        self.assertIn("github.actor == github.repository_owner && github.ref == 'refs/heads/main'", workflow)
        self.assertIn("github.repository == 'ShaishiBear/dark-factory-2.0'", workflow)
        self.assertIn("group: dark-factory-owner-stop", workflow)
        self.assertNotIn("group: dark-factory-worker", workflow)
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("permission-issues: write", workflow)
        self.assertNotIn("permission-contents: write", workflow)
        self.assertNotIn("permission-pull-requests: write", workflow)
        shell = workflow.split("        run: |", 1)[1]
        self.assertNotIn("${{", shell)
        self.assertIn("STOP_REASON: ${{ inputs.reason }}", workflow)
        self.assertIn("python -m factory_kernel.frontdoor_control", shell)


if __name__ == "__main__":
    unittest.main()
