"""The autonomous identity is spent fresh, and a stale one refuses before the API (ACP-004).

A GitHub App installation token lives 60 minutes. Run 34151427980 minted one in its second
step, validated PR #134 for 4970.989 s, passed merge pre-authorization, and then spent that
token on `gh pr merge` at +94m38s. GitHub answered 401 Bad credentials. `github-mutation`
carries no GH_TOKEN or GITHUB_TOKEN by design, so there was nothing to fall back to and
nothing to say what had happened.

The margin is judged against observed spend ages rather than the 60-minute limit: the run that
opened PR #134 called `create_pr` at 56m27s, three minutes and thirty-three seconds short of
expiry, so a five-minute margin would have permitted the spend that nearly failed.

The margin is the guardrail. The fix is the step split: the merge runs in its own workflow step
behind its own `create-github-app-token`, so the identity it spends is seconds old. These tests
pin both, and pin that the split has not been claimed for operations that do not have it.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import refusal as R  # noqa: E402
from factory_kernel.credential_env import identity_age_seconds, scoped_environment  # noqa: E402
from factory_kernel.github_cli import GitHubClient  # noqa: E402

WORKER = ROOT / ".github" / "workflows" / "dark-factory-worker.yml"
KERNEL_JSON = ROOT / ".factory" / "kernel.json"
MINTED_AT = "DARK_FACTORY_APP_TOKEN_MINTED_AT"


def client(limit: int = 1200) -> GitHubClient:
    return GitHubClient("o/r", cwd=str(ROOT), identity_max_age_seconds=limit)


def env_at(age_seconds: float | None) -> dict[str, str]:
    env = {"DARK_FACTORY_APP_TOKEN": "app-token"}
    if age_seconds is not None:
        env[MINTED_AT] = str(time.time() - age_seconds)
    return env


class AnIdentityStatesItsAge(unittest.TestCase):
    def test_a_missing_mint_time_is_not_a_free_pass(self):
        self.assertIsNone(identity_age_seconds(source={}))

    def test_an_unparseable_or_backwards_mint_time_establishes_nothing(self):
        for value in ("nonsense", "", "-1", "0"):
            with self.subTest(value):
                self.assertIsNone(identity_age_seconds(source={MINTED_AT: value}))
        self.assertIsNone(
            identity_age_seconds(now=100.0, source={MINTED_AT: "200"}),
            "a clock that runs backwards is not evidence of freshness",
        )

    def test_the_mint_time_travels_with_the_token_and_only_with_it(self):
        source = {"DARK_FACTORY_APP_TOKEN": "t", MINTED_AT: "123", "GH_TOKEN": "actions"}
        granted = scoped_environment(source=source, scope="github-mutation")
        self.assertEqual(granted[MINTED_AT], "123")
        for scope in ("none", "github", "validation", "github+validation"):
            with self.subTest(scope):
                self.assertNotIn(MINTED_AT, scoped_environment(source=source, scope=scope))


class AStaleIdentityRefusesBeforeTheAPI(unittest.TestCase):
    def test_the_merge_refuses_the_exact_age_that_produced_the_401(self):
        with mock.patch.dict(os.environ, env_at(5678), clear=True):
            with self.assertRaises(R.IdentityExpired) as ctx:
                client()._autonomous_identity("merge_squash")
        self.assertIn("merge_squash", str(ctx.exception))
        self.assertIn("5678s ago", str(ctx.exception))

    def test_the_merge_refuses_an_identity_that_cannot_state_its_age(self):
        with mock.patch.dict(os.environ, env_at(None), clear=True):
            with self.assertRaises(R.IdentityExpired):
                client()._autonomous_identity("merge_squash")

    def test_a_fresh_identity_is_spent(self):
        with mock.patch.dict(os.environ, env_at(30), clear=True):
            granted = client()._autonomous_identity("merge_squash")
        self.assertEqual(granted["GH_TOKEN"], "app-token")

    def test_the_observed_create_pr_age_is_over_the_margin(self):
        """56m27s is what run 34061371205 actually did. The margin exists to catch it, which is
        why it is not five minutes."""
        self.assertGreater(56 * 60 + 27, client().identity_max_age_seconds)

    def test_every_spend_says_its_age_so_the_margin_is_auditable(self):
        import contextlib
        import io

        for operation in ("push_branch", "create_pr", "merge_squash"):
            with self.subTest(operation):
                out = io.StringIO()
                with mock.patch.dict(os.environ, env_at(45), clear=True):
                    with contextlib.redirect_stdout(out):
                        client()._autonomous_identity(operation)
                line = out.getvalue()
                self.assertIn(f"FACTORY_IDENTITY_AGE operation={operation}", line)
                self.assertIn("seconds=45", line)
                self.assertIn("limit=1200", line)


class TheSplitIsNotClaimedWhereItDoesNotExist(unittest.TestCase):
    def test_only_the_merge_is_declared_split_today(self):
        """ACP-004 item 2 (the build's push/PR handoff) is unbuilt. Refusing there would break
        builds that currently succeed, so those operations report and proceed. When item 2
        lands, they join this set and this test changes with it."""
        self.assertEqual(GitHubClient.SPLIT_OPERATIONS, frozenset({"merge_squash"}))

    def test_an_unsplit_operation_reports_a_stale_identity_without_refusing(self):
        import contextlib
        import io

        for operation in ("push_branch", "create_pr"):
            with self.subTest(operation):
                out = io.StringIO()
                with mock.patch.dict(os.environ, env_at(5678), clear=True):
                    with contextlib.redirect_stdout(out):
                        client()._autonomous_identity(operation)
                self.assertIn("split=no verdict=proceeding", out.getvalue())


class TheRefusalNamesItself(unittest.TestCase):
    def test_identity_expired_is_its_own_reason_code_whatever_the_cursor_says(self):
        exc = R.IdentityExpired("minted 5678s ago")
        for cursor in (None, "merge_preauth", "evidence_spine"):
            with self.subTest(cursor=cursor):
                self.assertEqual(R.classify(cursor, exc), "identity_expired")
        self.assertIn("identity_expired", R.REASON_CODES)
        self.assertIn("too old to spend", R.AUTHORITY["identity_expired"])

    def test_it_does_not_read_as_a_verdict_about_the_candidate(self):
        """The candidate was never judged. The authority string must not suggest it was."""
        text = R.AUTHORITY["identity_expired"]
        for word in ("holdout", "certifier", "evidence spine", "merge pre-authorization"):
            self.assertNotIn(word, text)


class TheMergeRunsBehindItsOwnMint(unittest.TestCase):
    @unittest.skipUnless(WORKER.exists(), "repo-shaped copy without the workflow")
    def test_the_workflow_mints_again_immediately_before_the_merge(self):
        text = WORKER.read_text(encoding="utf-8")
        self.assertIn("Mint a fresh identity for the merge", text)
        mint_again = text.index("Mint a fresh identity for the merge")
        merge_step = text.index("Merge the PR the evidence authorised")
        self.assertLess(mint_again, merge_step, "the mint must precede the spend")
        self.assertEqual(
            text.count("uses: actions/create-github-app-token@"), 2,
            "the merge needs its own mint, and the private key stays with the action",
        )

    @unittest.skipUnless(WORKER.exists(), "repo-shaped copy without the workflow")
    def test_dispatch_defers_the_merge_so_a_later_step_can_do_it(self):
        text = WORKER.read_text(encoding="utf-8")
        self.assertIn("dispatch --once --defer-merge", text)
        self.assertIn(f"{MINTED_AT}=$(date +%s)", text)

    @unittest.skipUnless(KERNEL_JSON.exists(), "repo-shaped copy without kernel.json")
    def test_the_margin_is_configuration_and_not_a_literal_at_the_call_site(self):
        runtime = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))["runtime"]
        self.assertEqual(runtime["autonomous_identity_max_age_seconds"], 1200)
        source = (ROOT / "factory_kernel" / "github_cli.py").read_text(encoding="utf-8")
        self.assertIn("self.identity_max_age_seconds", source)

    def test_the_merge_entry_point_re_reads_the_authorization_it_did_not_write(self):
        """The exact-head guarantee survives the split because it is artifact-mediated."""
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        body = source.split("    def merge_authorized(", 1)[1].split("\n    # ----", 1)[0]
        self.assertIn("merge-authorization.json", body)
        self.assertIn("head moved after authorization", body)
        self.assertIn("merge_verify.py", body)


if __name__ == "__main__":
    unittest.main()
