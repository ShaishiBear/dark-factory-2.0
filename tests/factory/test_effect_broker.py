"""The effect broker (SPECIFICATION 5, C05): grant consumed once and durably, fresh controls before
the one remote call, exact-head merge, independent observation, timeouts uncertain, no replay."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.capabilities import DEFAULT_POLICY, Grant, authorize  # noqa: E402
from factory_kernel.effect_broker import EffectBroker, EffectJournal, EffectRefused, EffectUncertain  # noqa: E402

HEAD, BASE, TREE, EVIDENCE = "a" * 40, "b" * 40, "d" * 40, "e" * 64
EPOCH = DEFAULT_POLICY["revocation_epoch"]


class FakeGitHub:
    """`pr` reads and `merge_squash` spends, recorded; the merge flips the PR to MERGED unless told otherwise."""

    def __init__(self, *, state="OPEN", head=HEAD, merge_effect="merged"):
        self.state, self.head, self.merge_effect = state, head, merge_effect
        self.calls: list[tuple] = []

    def pr(self, number, *, holdout_safe=False):
        self.calls.append(("pr", number, holdout_safe))
        return {"number": number, "state": self.state, "headRefOid": self.head, "baseRefOid": BASE,
                "mergeCommit": {"oid": "f" * 40} if self.state == "MERGED" else None}

    def merge_squash(self, number, *, expected_head):
        self.calls.append(("merge_squash", number, expected_head))
        if self.merge_effect == "timeout":
            raise subprocess.TimeoutExpired(["gh"], 180)
        if self.merge_effect == "error":
            raise RuntimeError("gh pr merge failed rc=1: 405 Method Not Allowed")
        if self.merge_effect == "merged":
            self.state = "MERGED"
        if self.merge_effect == "observe_fails":
            self.pr = self._boom  # type: ignore[method-assign]

    def _boom(self, number, *, holdout_safe=False):
        raise RuntimeError("gh pr view failed rc=1: 502")


def grant_for(**overrides) -> Grant:
    subject = {"repository": "octo/dynachat", "pr_number": 134, "head_sha": HEAD, "base_sha": BASE, "head_tree_sha": TREE,
               "evidence_sha256": EVIDENCE}
    subject.update(overrides.pop("subject", {}))
    request = {"operation": "merge_exact_head", "caller_role": "merge-executor", "caller_instance": "kernel:run-1",
               "request_id": "merge-134-aaaaaaaaaaaa", "source_sha": BASE}
    request.update(overrides.pop("request", {}))
    evidence = {"base_sha": subject["base_sha"], "head_sha": subject["head_sha"], "head_tree_sha": subject["head_tree_sha"],
                "evidence_sha256": subject["evidence_sha256"]}
    grant = authorize(request, subject, DEFAULT_POLICY, evidence, None, None, now=overrides.pop("now", 1000))
    assert isinstance(grant, Grant), grant
    return grant


class BrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = EffectJournal(Path(self.tmp.name) / "artifacts" / "effect-journal.jsonl")
        self.stops = 0
        self.stop_raises: Exception | None = None
        self.now = 1001

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def stop_check(self) -> None:
        self.stops += 1
        if self.stop_raises is not None:
            raise self.stop_raises

    def broker(self, github, *, epoch=EPOCH) -> EffectBroker:
        return EffectBroker(github, self.journal, stop_check=self.stop_check, epoch=epoch, clock=lambda: self.now)

    def states(self) -> list[str]:
        return [row["state"] for row in self.journal.rows()]

    def test_a_granted_merge_is_journaled_started_before_the_call_and_observed_after(self) -> None:
        gh = FakeGitHub()
        result = self.broker(gh).merge_exact_head(grant_for(), expected_head=HEAD)
        self.assertEqual(result.state, "observed_success")
        self.assertEqual([c[0] for c in gh.calls], ["pr", "merge_squash", "pr"])
        self.assertEqual(gh.calls[1], ("merge_squash", 134, HEAD))
        self.assertEqual(self.states(), ["started", "observed_success"])
        rows = self.journal.rows()
        self.assertLess(rows[0]["at"], rows[1]["at"] + 1)
        self.assertEqual(rows[1]["remote_state"], "merged")
        self.assertEqual(rows[1]["observation"]["state"], "MERGED")
        self.assertEqual(self.stops, 1)  # fresh controls once, immediately before the call
        self.assertEqual(rows[0]["grant"]["authority"], "grant-record-only")

    def test_the_stop_is_reread_immediately_before_the_call_and_a_stop_means_no_call(self) -> None:
        gh = FakeGitHub()
        self.stop_raises = RuntimeError("FACTORY_STOPPED: owner stop observed")
        with self.assertRaises(RuntimeError):
            self.broker(gh).merge_exact_head(grant_for(), expected_head=HEAD)
        self.assertEqual([c[0] for c in gh.calls], ["pr"])  # observed, never merged
        self.assertEqual(self.states(), ["started", "observed_failure"])
        last = self.journal.rows()[-1]
        self.assertEqual((last["remote_call"], last["remote_state"]), (False, "not_called"))
        self.assertIn("control changed before the call", last["detail"])

    def test_refusals_before_consumption_journal_no_start_and_touch_no_remote(self) -> None:
        cases = {
            "grant_expired": (FakeGitHub(), grant_for(), HEAD, dict(now=1000 + DEFAULT_POLICY["grant_ttl_seconds"])),
            "grant_revoked_by_epoch": (FakeGitHub(), grant_for(), HEAD, dict(epoch="epoch-9")),
            "expected_head_differs_from_grant": (FakeGitHub(), grant_for(), "c" * 40, {}),
            "subject_head_moved": (FakeGitHub(head="c" * 40), grant_for(), HEAD, {}),
            "subject_not_open": (FakeGitHub(state="MERGED"), grant_for(), HEAD, {}),
        }
        for code, (gh, grant, expected, opts) in cases.items():
            with self.subTest(code):
                self.now = opts.get("now", 1001)
                broker = self.broker(gh, epoch=opts.get("epoch", EPOCH))
                with self.assertRaises(EffectRefused) as ctx:
                    broker.merge_exact_head(grant, expected_head=expected)
                self.assertIn(code, ctx.exception.reason_codes)
                self.assertNotIn("merge_squash", [c[0] for c in gh.calls])
                self.assertEqual(self.journal.rows()[-1]["state"], "refused")
        self.assertNotIn("started", self.states())
        self.assertEqual(self.stops, 0)

    def test_a_wrong_role_or_operation_never_reaches_the_gate(self) -> None:
        observer = grant_for(request={"operation": "observe", "caller_role": "observer"})
        gh = FakeGitHub()
        with self.assertRaises(EffectRefused) as ctx:
            self.broker(gh).merge_exact_head(observer, expected_head=HEAD)
        self.assertIn("operation_mismatch", ctx.exception.reason_codes)
        self.assertEqual(gh.calls, [])
        reserved = grant_for(request={"operation": "publish_candidate", "caller_role": "build-executor"})
        with self.assertRaises(EffectRefused) as ctx:
            self.broker(gh).merge_exact_head(reserved, expected_head=HEAD)
        self.assertIn("operation_mismatch", ctx.exception.reason_codes)

    def test_a_consumed_grant_cannot_be_spent_twice_but_an_identical_replay_reads_the_observation(self) -> None:
        gh = FakeGitHub()
        grant = grant_for()
        first = self.broker(gh).merge_exact_head(grant, expected_head=HEAD)
        gh.state = "OPEN"  # even if the platform looked open again, nothing executes twice
        again = self.broker(gh).merge_exact_head(grant, expected_head=HEAD)
        self.assertTrue(again.replayed)
        self.assertEqual(again.observation, first.observation)
        self.assertEqual([c[0] for c in gh.calls].count("merge_squash"), 1)
        self.assertEqual(self.states(), ["started", "observed_success"])

    def test_a_grant_spent_on_observe_or_on_a_failed_merge_is_not_replayed_as_a_merge(self) -> None:
        gh = FakeGitHub()
        observer = grant_for(request={"operation": "observe", "caller_role": "observer"})
        self.broker(gh).observe(observer)
        with self.assertRaises(EffectRefused) as ctx:
            self.broker(gh).merge_exact_head(observer, expected_head=HEAD)
        self.assertTrue({"operation_mismatch", "grant_uses_exhausted"} & set(ctx.exception.reason_codes))
        self.assertNotIn("merge_squash", [c[0] for c in gh.calls])
        # A merge grant whose call was refused by a stop stays refused on replay: never a success.
        stopped = FakeGitHub()
        grant = grant_for()
        self.stop_raises = RuntimeError("FACTORY_STOPPED")
        with self.assertRaises(RuntimeError):
            self.broker(stopped).merge_exact_head(grant, expected_head=HEAD)
        self.stop_raises = None
        with self.assertRaises(EffectRefused) as ctx:
            self.broker(stopped).merge_exact_head(grant, expected_head=HEAD)
        self.assertIn("grant_uses_exhausted", ctx.exception.reason_codes)
        self.assertNotIn("merge_squash", [c[0] for c in stopped.calls])
        # And a replay presented with a different expected head is not a replay at all.
        merged = FakeGitHub()
        fresh = grant_for(now=2000)
        self.broker(merged).merge_exact_head(fresh, expected_head=HEAD)
        with self.assertRaises(EffectRefused) as ctx:
            self.broker(merged).merge_exact_head(fresh, expected_head="c" * 40)
        self.assertIn("expected_head_differs_from_grant", ctx.exception.reason_codes)

    def test_a_started_or_uncertain_operation_refuses_reissue(self) -> None:
        gh = FakeGitHub(merge_effect="timeout")
        grant = grant_for()
        with self.assertRaises(EffectUncertain):
            self.broker(gh).merge_exact_head(grant, expected_head=HEAD)
        self.assertEqual(self.states(), ["started", "uncertain"])
        self.assertEqual(self.journal.rows()[-1]["remote_state"], "unknown")
        gh.merge_effect = "merged"
        with self.assertRaises(EffectRefused) as ctx:
            self.broker(gh).merge_exact_head(grant, expected_head=HEAD)
        self.assertIn("prior_operation_pending_or_uncertain", ctx.exception.reason_codes)
        self.assertEqual([c[0] for c in gh.calls].count("merge_squash"), 1)
        # A crash between `started` and any observation leaves the same refusal for the next process.
        crashed = EffectJournal(Path(self.tmp.name) / "crash.jsonl")
        crashed.record("started", grant, at=5)
        with self.assertRaises(EffectRefused) as ctx:
            EffectBroker(FakeGitHub(), crashed, stop_check=self.stop_check, epoch=EPOCH, clock=lambda: 6).merge_exact_head(grant, expected_head=HEAD)
        self.assertIn("prior_operation_pending_or_uncertain", ctx.exception.reason_codes)

    def test_a_failed_call_is_observed_failure_with_the_remote_state_unverified(self) -> None:
        gh = FakeGitHub(merge_effect="error")
        with self.assertRaises(RuntimeError):
            self.broker(gh).merge_exact_head(grant_for(), expected_head=HEAD)
        last = self.journal.rows()[-1]
        self.assertEqual((last["state"], last["remote_call"], last["remote_state"]), ("observed_failure", True, "unverified"))
        self.assertIn("405", last["detail"])

    def test_a_returned_call_whose_observation_fails_is_uncertain_not_success(self) -> None:
        gh = FakeGitHub(merge_effect="observe_fails")
        with self.assertRaises(EffectUncertain):
            self.broker(gh).merge_exact_head(grant_for(), expected_head=HEAD)
        last = self.journal.rows()[-1]
        self.assertEqual((last["state"], last["remote_state"]), ("uncertain", "returned_unobserved"))

    def test_observe_is_read_only_and_still_consumes_a_use(self) -> None:
        gh = FakeGitHub()
        grant = grant_for(request={"operation": "observe", "caller_role": "observer"})
        result = self.broker(gh).observe(grant)
        self.assertEqual((result.state, result.observation["state"]), ("observed_success", "OPEN"))
        self.assertEqual([c[0] for c in gh.calls], ["pr"])
        with self.assertRaises(EffectRefused) as ctx:
            self.broker(gh).observe(grant)
        self.assertIn("grant_uses_exhausted", ctx.exception.reason_codes)

    def test_reserved_operations_refuse_as_not_served(self) -> None:
        grant = grant_for(request={"operation": "publish_candidate", "caller_role": "build-executor"})
        with self.assertRaises(EffectRefused) as ctx:
            self.broker(FakeGitHub())._gate(grant, "publish_candidate", expected_head=None)
        self.assertIn("operation_not_served", ctx.exception.reason_codes)

    def test_the_journal_is_durable_json_lines_no_credential(self) -> None:
        gh = FakeGitHub()
        self.broker(gh).merge_exact_head(grant_for(), expected_head=HEAD)
        text = self.journal.path.read_text(encoding="utf-8")
        self.assertEqual(len(text.splitlines()), 2)
        for line in text.splitlines():
            row = json.loads(line)
            self.assertEqual(row["schema"], "dark-factory/effect-journal")
        self.assertNotIn("GH_TOKEN", text)
        self.assertNotIn("DARK_FACTORY_APP_TOKEN", text)


if __name__ == "__main__":
    unittest.main()
