"""Billing reconciliation: three states, unresolved stays unresolved, adapters fail closed."""
from __future__ import annotations

import unittest

from factory_kernel.billing_reconciliation import ADAPTERS, Receipt, ReconciliationRefused, fetch_receipts, reconcile


def invocation(call_id, state="observed", ceiling=1000, reported=None, pid=None):
    return {"call_id": call_id, "state": state, "ceiling_microusd": ceiling, "reported_microusd": reported, "provider_request_id": pid}


class ReconciliationTests(unittest.TestCase):
    def test_no_receipt_complete_and_conflicting_are_distinguished(self):
        result = reconcile([
            invocation("a", pid="p-a", reported=400),                    # complete: receipt equals telemetry
            invocation("b", pid="p-b"),                                    # complete: unknown telemetry, within ceiling
            invocation("c", pid="p-c", reported=400),                    # conflicting: receipt differs from telemetry
            invocation("d", pid="p-d"),                                    # conflicting: above ceiling
            invocation("e", pid="p-e"),                                    # no receipt
            invocation("f", state="not_started", pid="p-f"),               # conflicting: billed but never started
            invocation("g", state="reserved"),                             # no receipt, never started
            invocation("h", pid="p-h"),                                    # conflicting: duplicate receipts
        ], [
            {"provider_request_id": "p-a", "billed_microusd": 400}, {"provider_request_id": "p-b", "billed_microusd": 999},
            {"provider_request_id": "p-c", "billed_microusd": 401}, {"provider_request_id": "p-d", "billed_microusd": 1001},
            {"provider_request_id": "p-f", "billed_microusd": 1}, {"provider_request_id": "p-h", "billed_microusd": 1},
            {"provider_request_id": "p-h", "billed_microusd": 1}, {"provider_request_id": "p-orphan", "billed_microusd": 5},
        ])
        verdicts = {row["call_id"]: row["verdict"] for row in result["rows"]}
        self.assertEqual(verdicts, {"a": "complete", "b": "complete", "c": "conflicting", "d": "conflicting", "e": "no_receipt",
                                    "f": "conflicting", "g": "no_receipt", "h": "conflicting"})
        self.assertEqual(result["counts"], {"no_receipt": 2, "complete": 2, "conflicting": 4})
        self.assertEqual((result["orphan_receipts"], result["resolved"], result["authority"]), (["p-orphan"], False, "observation-only"))
        clean = reconcile([invocation("a", pid="p-a", reported=400)], [Receipt("p-a", 400, "provider")])
        self.assertTrue(clean["resolved"])

    def test_malformed_receipts_are_refused_not_ignored(self):
        with self.assertRaises(ReconciliationRefused):
            reconcile([], [{"provider_request_id": "", "billed_microusd": 1}])
        with self.assertRaises(ReconciliationRefused):
            reconcile([], [{"provider_request_id": "p", "billed_microusd": -1}])
        with self.assertRaises(ReconciliationRefused):
            reconcile([], [{"provider_request_id": "p", "billed_microusd": True}])

    def test_every_adapter_is_disabled_and_fails_closed(self):
        self.assertTrue(all(not a["enabled"] and not a["interface_verified"] for a in ADAPTERS.values()))
        with self.assertRaises(ReconciliationRefused) as ctx:
            fetch_receipts("openrouter")
        self.assertIn("disabled", str(ctx.exception))
        with self.assertRaises(ReconciliationRefused):
            fetch_receipts("stripe")


if __name__ == "__main__":
    unittest.main()
