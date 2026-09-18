"""The transition-status CLI (WP03 real reader): derives the phase from receipts, reports the
pending request and store agreement, decides release from an observation file, writes one
canonical record, refuses another owner's history and makes no remote call."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from factory_kernel import cli
from factory_kernel.canonical import canonical_bytes, sha256_bytes
from factory_kernel.frontdoor_intent import IntentStore, Principal
from factory_kernel.project_events import ProjectEvents
from factory_kernel.transition_journal import TransitionJournal, transition_operations

REPO = "owner/product"
OWNER = "maintainer"
SERVICE = Principal("transition-service", "service")
TID = "9" * 64
REQ, REQ_SHA = "a" * 32, "b" * 64


def run_cli(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with patch.object(sys, "argv", ["python -m factory_kernel", *argv]), contextlib.redirect_stdout(out):
        code = cli.main()
    return code, out.getvalue()


class TransitionStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="transition-views-")))
        self.enterContext(patch("factory_kernel.cli.load_config", return_value=SimpleNamespace(repository=REPO)))
        self.state = self.tmp / "state"
        store = IntentStore(self.state, repository=REPO, owner=OWNER)
        self.journal = TransitionJournal(ProjectEvents(store, transition_operations()), "p")
        self.out = self.tmp / "status.json"

    def status(self, *extra: str) -> tuple[int, str, dict | None]:
        code, text = run_cli("transition-status", "--state-dir", str(self.state), "--owner", OWNER, "--project", "p",
                             "--transition", TID, "--output", str(self.out), *extra)
        record = json.loads(self.out.read_bytes()) if self.out.exists() else None
        return code, text, record

    def test_phase_pending_request_agreement_and_release_are_derived_without_effects(self):
        code, text, record = self.status()
        self.assertEqual((code, record["phase"], record["receipts"], record["pending_request"], record["release"]), (0, "reviewed", 0, None, None))
        self.journal.record(transition_id=TID, event="request_fence", principal=SERVICE, expected_version=0, idempotency_key="k1",
                            request_id=REQ, request_sha256=REQ_SHA)
        observation = self.tmp / "obs.json"
        observation.write_text(json.dumps({"observed": True, "consent": True, "successor": False, "fenced": True, "queued": 0,
                                           "active": 2, "pending_effects": 0, "accounting_reconciled": True}), encoding="utf-8")
        code, text, record = self.status("--observation", str(observation), "--store-state", "started")
        self.assertEqual(code, 0, text)
        self.assertEqual((record["phase"], record["pending_request"]["request_id"], record["store_agreement"]), ("fence_requested", REQ, "consistent"))
        self.assertEqual(record["release"], {"status": "blocked", "reason_codes": ["successor_unobserved", "predecessor_not_drained"], "remote_calls": 0})
        self.assertEqual((record["authority"], record["remote_calls"], record["project_version"]), ("projection-only", 0, 1))
        self.assertIn(f"sha256={sha256_bytes(canonical_bytes(record))}", text)
        self.assertIn("phase=fence_requested", text)
        code, text, record = self.status("--store-state", "observed_success")
        self.assertEqual((code, record["store_agreement"]), (0, "reconciliation_required"))

    def test_refusals_write_nothing(self):
        # An owner-role receipt names the configured owner; a projection for another identity is refused.
        self.journal.record(transition_id=TID, event="request_fence", principal=Principal(OWNER, "owner"), expected_version=0,
                            idempotency_key="k1", request_id=REQ, request_sha256=REQ_SHA)
        for extra, reason in ((("--owner", "stranger"), "owner"), (("--store-state", "done"), "store state"),
                              (("--transition", "zz"), "hex")):
            with self.subTest(reason=reason):
                self.out.unlink(missing_ok=True)
                argv = ["transition-status", "--state-dir", str(self.state), "--owner", OWNER, "--project", "p",
                        "--transition", TID, "--output", str(self.out)]
                for i in range(0, len(extra), 2):
                    if extra[i] in argv:
                        argv[argv.index(extra[i]) + 1] = extra[i + 1]
                    else:
                        argv += [extra[i], extra[i + 1]]
                code, text = run_cli(*argv)
                self.assertEqual(code, 1, text)
                self.assertIn("FACTORY_TRANSITION_REFUSED", text)
                self.assertFalse(self.out.exists())
        self.out.unlink(missing_ok=True)
        bad = self.tmp / "bad.json"; bad.write_text(json.dumps({"observed": True, "surprise": 1}), encoding="utf-8")
        code, text, record = self.status("--observation", str(bad))
        self.assertEqual((code, record), (1, None))


if __name__ == "__main__":
    unittest.main()
