from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("factory_lease", ROOT / "scripts" / "factory_lease.py")
assert spec and spec.loader
lease = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lease)

NOW = datetime(2026, 8, 23, 21, 0, tzinfo=timezone.utc)


def active(age_seconds: int = 0) -> dict:
    return {
        "lease_id": "lease-1",
        "workflow_id": "wf-1",
        "heartbeat_at": lease.iso(NOW - timedelta(seconds=age_seconds)),
        "stage": "implement",
        "state": "active",
        "pr": None,
    }


class LeasePolicyTests(unittest.TestCase):
    def decide(self, record, *, accepted=True, marker_seen=True, handoff="none",
               updated_age=0, active_ttl=21600, legacy_ttl=86400):
        return lease.decide_reap(
            NOW, accepted, NOW - timedelta(seconds=updated_age), record,
            marker_seen, handoff, active_ttl, legacy_ttl,
        )

    def test_fresh_lease_is_never_reaped(self) -> None:
        self.assertEqual(self.decide(active(60))[0], "keep")

    def test_stale_lease_without_pr_is_redispatchable(self) -> None:
        self.assertEqual(self.decide(active(21601))[0], "reap")

    def test_stale_lease_with_validator_handoff_is_released(self) -> None:
        action, reason = self.decide(active(21601), handoff="ready")
        self.assertEqual(action, "reap")
        self.assertIn("PR", reason)

    def test_unlabelled_open_pr_fails_closed(self) -> None:
        self.assertEqual(self.decide(active(999999), handoff="unlabeled")[0], "protect")

    def test_malformed_marker_fails_closed(self) -> None:
        self.assertEqual(self.decide(None, marker_seen=True, updated_age=999999)[0], "protect")

    def test_legacy_claim_gets_long_grace_then_reaps(self) -> None:
        self.assertEqual(self.decide(None, marker_seen=False, updated_age=86399)[0], "keep")
        self.assertEqual(self.decide(None, marker_seen=False, updated_age=86401)[0], "reap")

    def test_unaccepted_in_progress_issue_is_not_mutated(self) -> None:
        self.assertEqual(self.decide(active(999999), accepted=False)[0], "protect")

    def test_finished_handoff_can_clear_failed_cleanup(self) -> None:
        record = active()
        record["state"] = "finished"
        self.assertEqual(self.decide(record, handoff="ready")[0], "reap")

    def test_marker_round_trip(self) -> None:
        record = active()
        self.assertEqual(lease.parse_lease(lease.render(record)), record)

    def test_pr_handoff_requires_closing_link_and_factory_label(self) -> None:
        prs = [
            {"body": "Fixes #42", "labels": [{"name": "factory:needs-review"}]},
            {"body": "Fixes #7", "labels": []},
        ]
        self.assertEqual(lease.pr_handoff(42, prs), "ready")
        self.assertEqual(lease.pr_handoff(7, prs), "unlabeled")
        self.assertEqual(lease.pr_handoff(4, prs), "none")


if __name__ == "__main__":
    unittest.main()
