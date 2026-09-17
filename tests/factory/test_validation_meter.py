"""Metered spend scopes (C06): one bundle on the existing ledger, per-call ceilings that sum
within it, one-use starts, unknown receipts that stay unknown, no borrowing between classes,
and a local record of every step."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import sha256_bytes
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.publication_source import observe_publication_source
from factory_kernel.validation_meter import (
    SPEND_CLASSES,
    ExchangeLedger,
    LocalLedger,
    MeterRefused,
    close_validation_scope,
    load_record,
    observe_call,
    open_validation_scope,
    reserve_call,
    start_call,
)

from tests.factory.test_execution_budget import BudgetTests

BINDING = {"run_id": 7, "run_attempt": 1, "source_sha": "a" * 40, "programme_sha256": "b" * 64}


def digest(text: str) -> str:
    return sha256_bytes(text.encode())


class FakeLedger:
    """Records the exact sequence the meter asks of the ledger; refuses like the real one."""

    def __init__(self, *, historical=False, consumed=False):
        self.reserved, self.started, self.observed = [], [], []
        self.historical, self.consumed = historical, consumed

    def reserve(self, call):
        if self.historical:
            raise MeterRefused("historical reservation cannot authorize a validation bundle")
        self.reserved.append(dict(call))
        return 41 + len(self.reserved)

    def start(self, call, reserved_version):
        if self.consumed:
            raise MeterRefused("validation bundle was already consumed")
        self.started.append((dict(call), reserved_version))

    def observe(self, call, *, reported_microusd, outcome):
        self.observed.append({"id": call["id"], "reported_microusd": reported_microusd, "outcome": outcome})


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.ledger = FakeLedger()
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def scope(self, **overrides):
        fields = dict(scope_class="validation-llm", binding=BINDING, execution_id="e2e-1", attempt=1, limit_microusd=1_000,
                      max_calls=3, request_sha256=digest("bundle"), record_dir=self.tmp)
        fields.update(overrides)
        return open_validation_scope(self.ledger, **fields)

    def test_opening_reserves_and_durably_starts_one_bundle_of_the_scopes_class(self):
        scope = self.scope()
        self.assertEqual(len(self.ledger.reserved), 1)
        bundle = self.ledger.reserved[0]
        self.assertEqual((bundle["role"], bundle["microusd"], bundle["execution_id"], bundle["attempt"], bundle["programme_sha256"]),
                         ("validation-llm", 1_000, "e2e-1", 1, "b" * 64))
        self.assertEqual(self.ledger.started, [(bundle, 42)])
        self.assertEqual(scope.reserved_version, 42)
        record = load_record(scope.record_path)
        self.assertEqual((record["scope_class"], record["calls"], record["closed"], record["authority"]), ("validation-llm", [], False, "spend-record-only"))
        for bad in (dict(scope_class="vibes"), dict(limit_microusd=0), dict(max_calls=0), dict(request_sha256="x"), dict(attempt=0)):
            with self.subTest(bad), self.assertRaises(MeterRefused):
                self.scope(**bad)
        self.assertEqual(len(self.ledger.reserved), 1, "refused scopes reserve nothing")

    def test_a_historical_or_consumed_bundle_never_opens(self):
        with self.assertRaises(MeterRefused):
            open_validation_scope(FakeLedger(historical=True), scope_class="worker-model", binding=BINDING, execution_id="x", attempt=1,
                                  limit_microusd=10, max_calls=1, request_sha256=digest("b"))
        with self.assertRaises(MeterRefused):
            open_validation_scope(FakeLedger(consumed=True), scope_class="worker-model", binding=BINDING, execution_id="x", attempt=1,
                                  limit_microusd=10, max_calls=1, request_sha256=digest("b"))

    def test_call_ceilings_must_sum_within_the_bundle_and_count_within_its_calls(self):
        scope = self.scope()
        first = reserve_call(scope, call_class="validation-llm", microusd=600, request_sha256=digest("q1"))
        second = reserve_call(scope, call_class="validation-llm", microusd=400, request_sha256=digest("q2"))
        with self.assertRaises(MeterRefused):
            reserve_call(scope, call_class="validation-llm", microusd=1, request_sha256=digest("q3"))
        self.assertEqual(scope.ceiling_sum(), 1_000)
        # A never-started reservation keeps its ceiling counted: no optimising away the attempt.
        observe_call(scope, second.call_id, reported_microusd=None, outcome="not_started")
        with self.assertRaises(MeterRefused):
            reserve_call(scope, call_class="validation-llm", microusd=1, request_sha256=digest("q4"))
        start_call(scope, first.call_id)
        observe_call(scope, first.call_id, reported_microusd=250, outcome="returned", telemetry={"tokens": 12})
        summary = close_validation_scope(scope)
        self.assertEqual((summary["reported_microusd"], summary["unknown_calls"], summary["outcome"], summary["calls"]), (250, 0, "returned", 2))
        self.assertEqual(self.ledger.observed, [{"id": scope.bundle["id"], "reported_microusd": 250, "outcome": "returned"}])
        self.assertEqual(close_validation_scope(scope), summary, "closing twice observes once")
        with self.assertRaises(MeterRefused):
            reserve_call(scope, call_class="validation-llm", microusd=1, request_sha256=digest("q5"))

    def test_a_scope_cannot_spend_another_classes_balance(self):
        scope = self.scope(scope_class="validation-embedding")
        for other in SPEND_CLASSES:
            if other == "validation-embedding":
                continue
            with self.subTest(other), self.assertRaises(MeterRefused):
                reserve_call(scope, call_class=other, microusd=1, request_sha256=digest("x"))
        self.assertEqual(scope.calls, {})

    def test_starts_are_one_use_and_a_lost_response_stays_unknown(self):
        scope = self.scope()
        call = reserve_call(scope, call_class="validation-llm", microusd=100, request_sha256=digest("q"))
        start_call(scope, call.call_id)
        with self.assertRaises(MeterRefused):
            start_call(scope, call.call_id)
        with self.assertRaises(MeterRefused):
            observe_call(scope, call.call_id, reported_microusd=None, outcome="not_started")
        summary = close_validation_scope(scope)
        self.assertEqual((summary["reported_microusd"], summary["unknown_calls"], summary["outcome"]), (None, 1, "failed"))
        self.assertEqual(self.ledger.observed[-1]["reported_microusd"], None)
        with self.assertRaises(MeterRefused):
            observe_call(scope, call.call_id, reported_microusd=5, outcome="returned")

    def test_an_unknown_receipt_makes_the_bundle_unknown_even_beside_known_ones(self):
        scope = self.scope()
        a = reserve_call(scope, call_class="validation-llm", microusd=100, request_sha256=digest("a"))
        b = reserve_call(scope, call_class="validation-llm", microusd=100, request_sha256=digest("b"))
        start_call(scope, a.call_id); observe_call(scope, a.call_id, reported_microusd=7, outcome="returned")
        start_call(scope, b.call_id); observe_call(scope, b.call_id, reported_microusd=None, outcome="partial")
        summary = close_validation_scope(scope)
        self.assertEqual((summary["reported_microusd"], summary["unknown_calls"], summary["outcome"]), (None, 1, "failed"))

    def test_the_record_follows_every_step(self):
        scope = self.scope()
        call = reserve_call(scope, call_class="validation-llm", microusd=100, request_sha256=digest("q"))
        states = [load_record(scope.record_path)["calls"][0]["state"]]
        start_call(scope, call.call_id); states.append(load_record(scope.record_path)["calls"][0]["state"])
        observe_call(scope, call.call_id, reported_microusd=3, outcome="returned"); states.append(load_record(scope.record_path)["calls"][0]["state"])
        close_validation_scope(scope)
        record = load_record(scope.record_path)
        self.assertEqual(states, ["reserved", "started", "observed"])
        self.assertEqual((record["closed"], record["summary"]["reported_microusd"]), (True, 3))
        with self.assertRaises(MeterRefused):
            (self.tmp / "other.json").write_text(json.dumps({"schema": "x"}), encoding="utf-8")
            load_record(self.tmp / "other.json")


class LocalLedgerTests(BudgetTests):
    """The Front Door host's real ExecutionBudget over a real intent store: the bundle is one
    reservation in the existing journal, started once and observed once, and it counts against
    the approved allowance like any worker call."""

    def test_a_bundle_is_one_reservation_in_the_existing_ledger(self):
        self.approve(dollars=3_000_000, calls=3)
        ledger = LocalLedger(self.budget, "citations", self.owner, lambda: observe_publication_source(self.github))
        binding = {**BINDING, "programme_sha256": self.programme.sha256}
        scope = open_validation_scope(ledger, scope_class="validation-llm", binding=binding, execution_id="e2e-run", attempt=1,
                                      limit_microusd=1_000_000, max_calls=4, request_sha256=digest("bundle"))
        state = self.budget.snapshot("citations", principal=self.owner)
        row = state["reservations"][scope.bundle["id"]]
        self.assertEqual((row["role"], row["microusd"], row["execution_id"], row["started"] is not None, row["observation"]),
                         ("validation-llm", 1_000_000, "e2e-run", True, None))
        self.assertEqual((state["reserved_microusd"], state["calls"], state["status"]), (1_000_000, 1, "unresolved-attempt"))
        call = reserve_call(scope, call_class="validation-llm", microusd=400_000, request_sha256=digest("q"))
        start_call(scope, call.call_id)
        observe_call(scope, call.call_id, reported_microusd=123_456, outcome="returned")
        summary = close_validation_scope(scope)
        state = self.budget.snapshot("citations", principal=self.owner)
        self.assertEqual(state["reservations"][scope.bundle["id"]]["observation"]["reported_microusd"], 123_456)
        self.assertEqual((summary["reported_microusd"], state["status"]), (123_456, "available"))
        # The same attempt identity cannot open a second bundle: the ledger refuses the repeat.
        with self.assertRaises(IntentRefused):
            open_validation_scope(ledger, scope_class="validation-llm", binding=binding, execution_id="e2e-run", attempt=1,
                                  limit_microusd=10, max_calls=1, request_sha256=digest("bundle-2"))

    def test_an_unknown_bundle_receipt_freezes_further_spend_on_the_ledger(self):
        self.approve(dollars=3_000_000, calls=3)
        ledger = LocalLedger(self.budget, "citations", self.owner, lambda: observe_publication_source(self.github))
        binding = {**BINDING, "programme_sha256": self.programme.sha256}
        scope = open_validation_scope(ledger, scope_class="diagnostic-probe", binding=binding, execution_id="probe", attempt=1,
                                      limit_microusd=100_000, max_calls=2, request_sha256=digest("bundle"))
        call = reserve_call(scope, call_class="diagnostic-probe", microusd=50_000, request_sha256=digest("q"))
        start_call(scope, call.call_id)
        close_validation_scope(scope)  # the call is still in flight: unknown
        state = self.budget.snapshot("citations", principal=self.owner)
        self.assertEqual((state["status"], state["uncertain"]), ("unresolved-attempt", True))
        with self.assertRaises(IntentRefused):
            open_validation_scope(ledger, scope_class="diagnostic-probe", binding=binding, execution_id="probe", attempt=2,
                                  limit_microusd=10, max_calls=1, request_sha256=digest("bundle-2"))


class ExchangeLedgerTests(unittest.TestCase):
    def test_the_hosted_exchange_adapter_speaks_the_three_phases_and_refuses_history(self):
        class Client:
            def __init__(self):
                self.calls = []

            def exchange(self, phase, call, payload):
                self.calls.append((phase, call["id"], payload))
                return {"reserve": {"status": "reserved", "project_version": 9},
                        "start": {"status": "start-once"}, "observe": {"status": "observed"}}[phase]

        client = Client()
        ledger = ExchangeLedger(client)
        bundle = {**BINDING, "id": "b1", "role": "worker-model", "microusd": 5, "request_sha256": digest("r"), "execution_id": "e", "attempt": 1}
        version = ledger.reserve(bundle)
        ledger.start(bundle, version)
        ledger.observe(bundle, reported_microusd=None, outcome="failed")
        self.assertEqual([c[0] for c in client.calls], ["reserve", "start", "observe"])
        self.assertEqual(client.calls[1][2], {"reservation_version": 9})
        self.assertEqual(client.calls[2][2], {"reported_microusd": None, "outcome": "failed"})

        class Historical(Client):
            def exchange(self, phase, call, payload):
                return {"status": "history", "project_version": 3} if phase == "reserve" else {"status": "consumed"}

        with self.assertRaises(MeterRefused):
            ExchangeLedger(Historical()).reserve(bundle)
        with self.assertRaises(MeterRefused):
            ExchangeLedger(Historical()).start(bundle, 3)


if __name__ == "__main__":
    unittest.main()
