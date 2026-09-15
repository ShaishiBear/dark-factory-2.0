"""Successful work may schedule a bounded successor; scheduling cannot qualify work."""
import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.continuation import continue_programme
from factory_kernel.programme import ProgrammeRefused


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.queue = SimpleNamespace(github=Mock(repository="owner/repo"), default_branch="main",
                                     current=Mock(return_value=SimpleNamespace(sha256="a" * 64)))
        self.stop = Mock()
        self.context = {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_REF": "refs/heads/main",
                        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "42",
                        "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/dark-factory-worker.yml@refs/heads/main"}
        self.args = dict(programme="a" * 64, remaining="8", advanced="true",
                         dispatch_result="success", merge_result="success", context=self.context)

    def invoke(self, **changes):
        return continue_programme(self.queue, self.stop, **{**self.args, **changes})

    def test_one_dispatch_decrements_the_bound_and_preserves_programme_and_parent(self):
        result = self.invoke()
        self.assertEqual(result["remaining"], 7)
        self.assertEqual(self.stop.call_count, 2)
        self.queue.github.run.assert_called_once_with([
            "workflow", "run", "dark-factory-worker.yml", "-R", "owner/repo", "--ref", "main",
            "-f", "continuation_programme=" + "a" * 64, "-f", "continuation_remaining=7",
            "-f", "continuation_parent=42"])

    def test_failed_cancelled_or_unfinished_action_never_continues(self):
        for dispatch, merge in (("failure", "success"), ("cancelled", "skipped"),
                                ("success", "failure"), ("success", "cancelled"),
                                ("success", ""), ("", "success")):
            with self.subTest(dispatch=dispatch, merge=merge):
                self.assertEqual(self.invoke(dispatch_result=dispatch, merge_result=merge).get("reason"),
                                 "prior-action-not-successful")
        self.queue.github.run.assert_not_called()

    def test_successful_build_with_no_merge_job_can_continue(self):
        self.assertEqual(self.invoke(merge_result="skipped")["status"], "dispatched")

    def test_idle_and_exhausted_chain_stop_without_remote_spend(self):
        self.assertEqual(self.invoke(advanced="false")["reason"], "no-progress")
        self.assertEqual(self.invoke(remaining="0")["reason"], "continuation-limit")
        self.queue.github.run.assert_not_called()

    def test_invalid_or_expanded_counter_refuses(self):
        for count in ("", "-1", "9", "99", "8.0", "08", " 8", "true"):
            with self.subTest(count=count), self.assertRaises(ProgrammeRefused):
                self.invoke(remaining=count)
        self.queue.github.run.assert_not_called()

    def test_unbound_changed_or_retired_programme_stops(self):
        self.assertEqual(self.invoke(programme="")["reason"], "no-bound-programme")
        self.queue.current.return_value = SimpleNamespace(sha256="b" * 64)
        self.assertEqual(self.invoke()["reason"], "programme-changed")
        self.queue.current.return_value = None
        self.assertEqual(self.invoke()["reason"], "programme-changed")
        self.queue.github.run.assert_not_called()

    def test_context_cannot_redirect_to_another_repository_branch_or_workflow(self):
        for field in self.context:
            with self.subTest(field=field), self.assertRaises(ProgrammeRefused):
                self.invoke(context={**self.context, field: "different"})
        self.queue.github.run.assert_not_called()

    def test_stop_or_unreadable_scope_prevents_continuation(self):
        for checkpoint in (1, 2):
            self.stop.side_effect = [None] * (checkpoint - 1) + [RuntimeError("stopped")]
            with self.subTest(checkpoint=checkpoint), self.assertRaisesRegex(RuntimeError, "stopped"):
                self.invoke()
        self.stop.side_effect = None
        self.queue.current.side_effect = TimeoutError("offline")
        with self.assertRaises(TimeoutError):
            self.invoke()
        self.queue.github.run.assert_not_called()

    def test_uncertain_dispatch_is_not_retried(self):
        self.queue.github.run.side_effect = TimeoutError("response lost")
        with self.assertRaises(TimeoutError):
            self.invoke()
        self.queue.github.run.assert_called_once()

    def test_workflow_scheduling_failure_cannot_mask_a_failed_proof_job(self):
        source = (ROOT / ".github/workflows/dark-factory-worker.yml").read_text()
        proof, continuation = source.split("\n  continuation:\n")
        self.assertNotIn("continue-on-error: true", proof)
        self.assertNotIn("actions: write", proof)
        self.assertIn("needs: [dispatch, merge]", continuation)
        self.assertIn("needs.dispatch.result == 'success'", continuation)
        self.assertIn("needs.merge.result == 'success' || needs.merge.result == 'skipped'", continuation)
        self.assertIn("needs.dispatch.outputs.action_advanced == 'true'", continuation)
        self.assertIn("continue-on-error: true", continuation)
        self.assertIn("actions: write", continuation)
        self.assertIn('DISPATCH_RESULT: ${{ needs.dispatch.result }}', continuation)
        self.assertIn('MERGE_RESULT: ${{ needs.merge.result }}', continuation)
        self.assertIn('REMAINING: ${{ inputs.continuation_remaining', continuation)
        self.assertIn('programme-sync --expected-programme "$EXPECTED_PROGRAMME"', proof)
        self.assertIn('cancel-in-progress: false', proof)
        for forbidden in ("DARK_FACTORY_APP", "OPENROUTER_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                          "services:", "persist-credentials: true"):
            self.assertNotIn(forbidden, continuation)

    def test_cli_idle_with_no_triage_progress_does_not_start_a_chain(self):
        from factory_kernel import cli

        for count, expected in ((0, "false"), (1, "true")):
            rt = SimpleNamespace(pending_merge=None,
                                 dispatch_once=Mock(return_value=SimpleNamespace(kind="idle")))
            with self.subTest(count=count), patch("sys.argv", ["factory_kernel", "dispatch", "--once"]), patch.object(cli, "runtime", return_value=rt), patch.object(cli, "TriageEngine") as triage, patch.object(cli, "_emit_step_outputs") as emit, contextlib.redirect_stdout(io.StringIO()):
                triage.return_value.run_once.return_value = count
                self.assertEqual(cli.main(), 0)
                emit.assert_called_once_with(action_advanced=expected)


if __name__ == "__main__":
    unittest.main()
