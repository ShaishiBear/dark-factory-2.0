"""Durable work, outbox, registration and provider-call rows, against a real SQLite file.

These are the storage half of Revision 4's control-plane counterexamples (C02, C03, C04,
C07, C08, C09, C10, C14). Nothing here is mocked: every test opens a real `LeaseStore` on a
real temporary database, applies the real additive migration, and asserts on what the file
holds afterwards. A refusal that changed a row would be visible.

The properties that matter, stated once:

  * One project owns one nonterminal attempt. Two coordinators cannot both start paying.
  * The budget request is durable BEFORE the canonical ledger is touched, under a stable
    attempt id, so a crash replays the same reservation instead of buying a second one.
  * A reservation and its lease bundle commit together, on one connection, in one
    transaction -- and the lease refusal is the lease store's own refusal.
  * Terminal work has no outgoing edge. A retry is a new attempt number.
  * An outbox row redelivered after a crash returns the identical receipt; different bytes
    under one idempotency key are corruption, never a second journal event.
  * A second authentic run cannot consume an envelope another run already consumed.
  * An unknown bill stays unknown. Nothing here turns it into zero.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.lease_store import LeaseRefused, LeaseStore  # noqa: E402
from factory_kernel.operational_state import (  # noqa: E402
    MIGRATION_VERSION, OperationalRefused, OperationalState, TRANSITIONS,
    canonical_request_sha256, migration_sha256, migration_sql, seconds_of,
)

D = lambda ch: ch * 64  # noqa: E731 - a readable stand-in digest
PROJECT = "ShaishiBear/dark-factory-2.0"
NOW = 1_758_000_000_000


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = LeaseStore(self.dir / "operational.sqlite")
        self.addCleanup(self.store.close)
        self.state = OperationalState(self.store)

    def work(self, key=D("1"), project=PROJECT, priority=3) -> str:
        return self.state.put_work(work_key=key, project=project, proposal_sha256=D("2"),
                                   programme_sha256=D("3"), created_sequence=7, priority=priority)

    def opened(self, key=D("1"), project=PROJECT, now_ms=NOW):
        self.work(key, project)
        return self.state.open_attempt(work_key=key, project=project,
                                       request={"claim": "c-1", "stage": "implement"}, now_ms=now_ms)

    def reserved(self, resources=("repository", "provider")):
        attempt = self.opened()
        attempt = self.state.record_budget_receipt(attempt_id=attempt.attempt_id,
                                                   receipt_sha256=D("4"),
                                                   expected_version=attempt.version, now_ms=NOW)
        return self.state.reserve(attempt_id=attempt.attempt_id, expected_version=attempt.version,
                                  owner="coordinator", role="coordinator", resources=resources,
                                  subject_sha256=D("5"), envelope_sha256=D("6"),
                                  ttl_seconds=300, now_ms=NOW)


class MigrationTests(Base):
    def test_the_migration_records_its_version_and_its_own_checksum(self):
        row = self.state.migration_row()
        self.assertEqual(row["version"], MIGRATION_VERSION)
        self.assertEqual(row["sql_sha256"], migration_sha256())

    def test_applying_the_same_version_twice_changes_nothing(self):
        self.assertFalse(self.store.apply_migration(MIGRATION_VERSION, migration_sql(), migration_sha256()))

    def test_a_different_file_under_the_same_version_is_refused(self):
        with self.assertRaises(LeaseRefused):
            self.store.apply_migration(MIGRATION_VERSION, "CREATE TABLE x(y);", D("f"))

    def test_the_existing_lease_schema_is_still_readable_and_still_works(self):
        bundle = self.store.acquire_many(owner="o", role="r", resources=["repository"],
                                         request_sha256=D("a"), subject_sha256=D("b"),
                                         ttl_seconds=60, now=seconds_of(NOW))
        self.assertEqual(self.store.lease(bundle.lease_id)["active"], True)
        tables = {row[0] for row in self.store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertLessEqual({"leases", "grants", "resources", "df_work", "df_attempts",
                              "df_outbox", "df_provider_calls"}, tables)

    def test_milliseconds_meet_seconds_in_exactly_one_place(self):
        self.assertEqual(seconds_of(1_758_000_000_999), 1_758_000_000)
        self.assertEqual(seconds_of(0), 0)
        with self.assertRaises(OperationalRefused):
            seconds_of(-1)


class WorkTests(Base):
    def test_a_repeated_wakeup_inserts_no_duplicate_work(self):
        self.assertEqual(self.work(), self.work())
        count = self.store.connection.execute("SELECT COUNT(*) FROM df_work").fetchone()[0]
        self.assertEqual(count, 1)

    def test_the_same_key_naming_different_work_is_a_refusal(self):
        self.work()
        with self.assertRaises(OperationalRefused):
            self.state.put_work(work_key=D("1"), project=PROJECT, proposal_sha256=D("9"),
                                programme_sha256=D("3"), created_sequence=7, priority=3)

    def test_unknown_work_cannot_open_an_attempt(self):
        with self.assertRaises(OperationalRefused):
            self.state.open_attempt(work_key=D("7"), project=PROJECT, request={}, now_ms=NOW)


class ReserveTests(Base):
    def test_c02_one_project_owns_one_nonterminal_attempt(self):
        first = self.opened()
        self.work(D("8"))
        with self.assertRaises(OperationalRefused) as caught:
            self.state.open_attempt(work_key=D("8"), project=PROJECT, request={}, now_ms=NOW)
        self.assertIn(first.attempt_id, str(caught.exception))
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM df_attempts").fetchone()[0], 1,
            "the loser wrote nothing")

    def test_a_different_project_is_not_blocked_by_this_one(self):
        self.opened()
        other = "ShaishiBear/other"
        self.work(D("8"), project=other)
        self.assertEqual(self.state.open_attempt(work_key=D("8"), project=other, request={},
                                                 now_ms=NOW).state, "budget_pending")

    def test_c03_the_budget_request_is_durable_before_the_ledger_is_touched(self):
        attempt = self.opened()
        self.assertEqual(attempt.state, "budget_pending")
        self.assertIsNone(attempt.budget_receipt_sha256)
        pending = self.state.pending_outbox(project=PROJECT)
        self.assertEqual([row["kind"] for row in pending], ["budget_reserve"])
        # Reopening the same file is what a restart sees.
        reopened = OperationalState(LeaseStore(self.dir / "operational.sqlite"))
        self.addCleanup(reopened.store.close)
        self.assertEqual(reopened.attempt(attempt.attempt_id).state, "budget_pending")
        self.assertEqual(len(reopened.pending_outbox(project=PROJECT)), 1,
                         "the same reserve request replays; it does not become a second one")

    def test_a_second_budget_receipt_with_different_bytes_is_refused(self):
        attempt = self.opened()
        attempt = self.state.record_budget_receipt(attempt_id=attempt.attempt_id,
                                                   receipt_sha256=D("4"),
                                                   expected_version=attempt.version, now_ms=NOW)
        with self.assertRaises(OperationalRefused):
            self.state.record_budget_receipt(attempt_id=attempt.attempt_id, receipt_sha256=D("5"),
                                             expected_version=attempt.version, now_ms=NOW)

    def test_a_valid_lease_is_not_permission_to_spend(self):
        attempt = self.opened()
        with self.assertRaises(OperationalRefused) as caught:
            self.state.reserve(attempt_id=attempt.attempt_id, expected_version=attempt.version,
                               owner="c", role="c", resources=["repository"], subject_sha256=D("5"),
                               envelope_sha256=D("6"), ttl_seconds=60, now_ms=NOW)
        self.assertIn("budget ambiguity", str(caught.exception))
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM leases").fetchone()[0], 0, "no lease was taken")

    def test_the_reservation_and_its_lease_commit_together(self):
        attempt, bundle = self.reserved()
        self.assertEqual(attempt.state, "reserved")
        self.assertEqual(attempt.lease_id, bundle.lease_id)
        self.assertEqual(sorted(bundle.generations), ["provider", "repository"])
        kinds = [row["kind"] for row in self.state.pending_outbox(project=PROJECT)]
        self.assertEqual(sorted(kinds), ["budget_reserve", "dispatch"])

    def test_a_lease_refusal_is_the_lease_stores_refusal_and_leaves_no_reservation(self):
        held = self.store.acquire_many(owner="other", role="worker", resources=["repository"],
                                       request_sha256=D("a"), subject_sha256=D("b"),
                                       ttl_seconds=600, now=seconds_of(NOW))
        self.assertTrue(held.lease_id)
        with self.assertRaises(LeaseRefused) as caught:
            self.reserved(resources=("repository",))
        self.assertIn("held by a live lease", str(caught.exception))
        states = [row[0] for row in self.store.connection.execute(
            "SELECT state FROM df_attempts").fetchall()]
        self.assertEqual(states, ["budget_pending"], "the attempt did not become reserved")

    def test_an_expected_version_that_has_moved_refuses_and_changes_nothing(self):
        attempt = self.opened()
        self.state.record_budget_receipt(attempt_id=attempt.attempt_id, receipt_sha256=D("4"),
                                         expected_version=attempt.version, now_ms=NOW)
        with self.assertRaises(OperationalRefused) as caught:
            self.state.transition(attempt_id=attempt.attempt_id, to_state="stale",
                                  expected_version=attempt.version, now_ms=NOW)
        self.assertIn("version", str(caught.exception))
        self.assertEqual(self.state.attempt(attempt.attempt_id).state, "budget_pending")


class TransitionTests(Base):
    def test_c04_a_head_change_before_dispatch_makes_the_attempt_stale_and_keeps_the_reservation(self):
        attempt, _ = self.reserved()
        stale = self.state.transition(attempt_id=attempt.attempt_id, to_state="stale",
                                      expected_version=attempt.version, now_ms=NOW + 1000)
        self.assertEqual(stale.state, "stale")
        self.assertEqual(stale.budget_receipt_sha256, D("4"),
                         "the ledger has no refund; the reservation is retained")

    def test_terminal_work_never_returns_to_ready(self):
        attempt, _ = self.reserved()
        for to_state, expect in (("dispatching", "dispatching"), ("dispatched", "dispatched"),
                                 ("running", "running"), ("failed", "failed")):
            attempt = self.state.transition(attempt_id=attempt.attempt_id, to_state=to_state,
                                            expected_version=attempt.version, now_ms=NOW)
            self.assertEqual(attempt.state, expect)
        for forbidden in ("ready", "running", "reserved", "succeeded", "stale"):
            with self.subTest(forbidden):
                with self.assertRaises(OperationalRefused):
                    self.state.transition(attempt_id=attempt.attempt_id, to_state=forbidden,
                                          expected_version=attempt.version, now_ms=NOW)

    def test_a_retry_is_a_new_attempt_number_under_the_same_work_key(self):
        attempt, _ = self.reserved()
        self.state.transition(attempt_id=attempt.attempt_id, to_state="failed",
                              expected_version=attempt.version, now_ms=NOW)
        again = self.state.open_attempt(work_key=D("1"), project=PROJECT,
                                        request={"claim": "c-1", "stage": "implement"}, now_ms=NOW)
        self.assertEqual(again.attempt_number, 2)
        self.assertEqual([a.attempt_number for a in self.state.attempts_for(D("1"))], [1, 2])

    def test_only_pre_start_work_becomes_stale(self):
        attempt, _ = self.reserved()
        for to_state in ("dispatching", "dispatched", "running"):
            attempt = self.state.transition(attempt_id=attempt.attempt_id, to_state=to_state,
                                            expected_version=attempt.version, now_ms=NOW)
        with self.assertRaises(OperationalRefused):
            self.state.transition(attempt_id=attempt.attempt_id, to_state="stale",
                                  expected_version=attempt.version, now_ms=NOW)

    def test_the_state_machine_has_no_edge_out_of_a_terminal_state(self):
        for state in ("succeeded", "failed", "cancelled", "stale"):
            self.assertEqual(TRANSITIONS[state], frozenset(), state)


class OutboxTests(Base):
    def test_c08_the_same_row_redelivered_returns_the_identical_receipt(self):
        attempt = self.opened()
        row = self.state.pending_outbox(project=PROJECT)[0]
        first = self.state.record_delivery(outbox_id=row["outbox_id"], receipt_sha256=D("c"),
                                           expected_version=row["version"])
        again = self.state.record_delivery(outbox_id=row["outbox_id"], receipt_sha256=D("c"),
                                           expected_version=first["version"])
        self.assertEqual(again["receipt_sha256"], D("c"))
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM df_outbox WHERE attempt_id = ?",
            (attempt.attempt_id,)).fetchone()[0], 1, "no second journal entry")

    def test_different_bytes_under_one_idempotency_key_are_corruption(self):
        row = self.state.pending_outbox(project=PROJECT) or self.opened() and self.state.pending_outbox(project=PROJECT)
        row = row[0]
        delivered = self.state.record_delivery(outbox_id=row["outbox_id"], receipt_sha256=D("c"),
                                               expected_version=row["version"])
        with self.assertRaises(OperationalRefused) as caught:
            self.state.record_delivery(outbox_id=row["outbox_id"], receipt_sha256=D("d"),
                                       expected_version=delivered["version"])
        self.assertIn("corruption", str(caught.exception))

    def test_the_idempotency_key_is_op_and_the_outbox_id(self):
        self.opened()
        row = self.state.pending_outbox(project=PROJECT)[0]
        self.assertEqual(self.state.idempotency_key(row["outbox_id"]), f"op:{row['outbox_id']}")

    def test_a_pending_row_is_not_an_approval(self):
        self.opened()
        row = self.state.pending_outbox(project=PROJECT)[0]
        self.assertEqual(row["state"], "pending")
        self.assertIsNone(row["receipt_sha256"],
                          "nothing counts as approved intent until its real receipt exists")

    def test_a_delivered_row_is_final(self):
        self.opened()
        row = self.state.pending_outbox(project=PROJECT)[0]
        delivered = self.state.record_delivery(outbox_id=row["outbox_id"], receipt_sha256=D("c"),
                                               expected_version=row["version"])
        with self.assertRaises(OperationalRefused):
            self.state.mark_outbox(outbox_id=row["outbox_id"], state="uncertain",
                                   expected_version=delivered["version"])

    def test_an_unknown_delivery_stays_in_the_queue_as_uncertain(self):
        self.opened()
        row = self.state.pending_outbox(project=PROJECT)[0]
        marked = self.state.mark_outbox(outbox_id=row["outbox_id"], state="uncertain",
                                        expected_version=row["version"],
                                        next_observation_at_ms=NOW + 30_000)
        self.assertEqual(marked["state"], "uncertain")
        self.assertIn(row["outbox_id"], [r["outbox_id"] for r in self.state.pending_outbox()])


class RegistrationTests(Base):
    def registered(self, run_id="900", token=D("e")):
        attempt, _ = self.reserved()
        return attempt, self.state.register_executor(
            attempt_id=attempt.attempt_id, repository_id="42", run_id=run_id, run_attempt=1,
            oidc_token_sha256=token, envelope_sha256=D("6"), credential_sha256=D("7"),
            expires_at_ms=NOW + 60_000, now_ms=NOW)

    def test_c07_a_second_authentic_run_cannot_consume_the_same_envelope(self):
        attempt, first = self.registered()
        with self.assertRaises(OperationalRefused) as caught:
            self.state.register_executor(
                attempt_id=attempt.attempt_id, repository_id="42", run_id="901", run_attempt=1,
                oidc_token_sha256=D("f"), envelope_sha256=D("6"), credential_sha256=D("8"),
                expires_at_ms=NOW + 60_000, now_ms=NOW)
        self.assertIn("already consumed", str(caught.exception))
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM df_executor_registrations").fetchone()[0], 1)
        self.assertEqual(first["run_id"], "900")

    def test_the_same_run_re_presenting_its_registration_gets_its_own_row_back(self):
        attempt, first = self.registered()
        again = self.state.register_executor(
            attempt_id=attempt.attempt_id, repository_id="42", run_id="900", run_attempt=1,
            oidc_token_sha256=D("e"), envelope_sha256=D("6"), credential_sha256=D("7"),
            expires_at_ms=NOW + 60_000, now_ms=NOW)
        self.assertEqual(again["registration_id"], first["registration_id"])

    def test_c15_a_consumed_token_cannot_register_another_job(self):
        attempt, _ = self.registered()
        other = self.opened(key=D("8"), project="ShaishiBear/other")
        other = self.state.record_budget_receipt(attempt_id=other.attempt_id, receipt_sha256=D("4"),
                                                 expected_version=other.version, now_ms=NOW)
        other, _ = self.state.reserve(attempt_id=other.attempt_id, expected_version=other.version,
                                      owner="c2", role="coordinator", resources=["other-repo"],
                                      subject_sha256=D("5"), envelope_sha256=D("9"),
                                      ttl_seconds=300, now_ms=NOW)
        with self.assertRaises(OperationalRefused) as caught:
            self.state.register_executor(
                attempt_id=other.attempt_id, repository_id="42", run_id="902", run_attempt=1,
                oidc_token_sha256=D("e"), envelope_sha256=D("9"), credential_sha256=D("a"),
                expires_at_ms=NOW + 60_000, now_ms=NOW)
        self.assertIn("already registered", str(caught.exception))

    def test_an_envelope_that_is_not_this_attempts_envelope_is_refused(self):
        attempt, _ = self.reserved()
        with self.assertRaises(OperationalRefused):
            self.state.register_executor(
                attempt_id=attempt.attempt_id, repository_id="42", run_id="900", run_attempt=1,
                oidc_token_sha256=D("e"), envelope_sha256=D("b"), credential_sha256=D("7"),
                expires_at_ms=NOW + 60_000, now_ms=NOW)

    def test_c10_a_heartbeat_at_equality_is_refused_not_renewed(self):
        _, registration = self.registered()
        expiry = registration["expires_at_ms"]
        with self.assertRaises(OperationalRefused) as caught:
            self.state.heartbeat_executor(registration_id=registration["registration_id"],
                                          now_ms=expiry, ttl_ms=60_000)
        self.assertIn("expired", str(caught.exception))
        self.assertEqual(self.state.registration(registration["registration_id"])["expires_at_ms"],
                         expiry, "a stale heartbeat cannot extend ownership")

    def test_a_live_heartbeat_extends_and_a_revoked_one_does_not(self):
        _, registration = self.registered()
        renewed = self.state.heartbeat_executor(registration_id=registration["registration_id"],
                                                now_ms=NOW + 10_000, ttl_ms=60_000)
        self.assertEqual(renewed["expires_at_ms"], NOW + 70_000)
        self.state.revoke_executor(registration_id=registration["registration_id"])
        with self.assertRaises(OperationalRefused):
            self.state.heartbeat_executor(registration_id=registration["registration_id"],
                                          now_ms=NOW + 20_000, ttl_ms=60_000)

    def test_an_already_expired_registration_grants_nothing(self):
        attempt, _ = self.reserved()
        with self.assertRaises(OperationalRefused):
            self.state.register_executor(
                attempt_id=attempt.attempt_id, repository_id="42", run_id="900", run_attempt=1,
                oidc_token_sha256=D("e"), envelope_sha256=D("6"), credential_sha256=D("7"),
                expires_at_ms=NOW, now_ms=NOW)


class WakeupTests(Base):
    def test_a_canonical_event_wakes_a_project_at_most_once(self):
        self.assertTrue(self.state.record_wakeup(project=PROJECT, source_event_id="evt-7",
                                                 payload_sha256=D("c")))
        self.assertFalse(self.state.record_wakeup(project=PROJECT, source_event_id="evt-7",
                                                  payload_sha256=D("c")))
        self.assertEqual(self.state.unprocessed_wakeups(project=PROJECT), ("evt-7",))

    def test_the_same_event_id_with_other_bytes_is_refused(self):
        self.state.record_wakeup(project=PROJECT, source_event_id="evt-7", payload_sha256=D("c"))
        with self.assertRaises(OperationalRefused):
            self.state.record_wakeup(project=PROJECT, source_event_id="evt-7", payload_sha256=D("d"))

    def test_a_processed_wakeup_leaves_the_unprocessed_list(self):
        self.state.record_wakeup(project=PROJECT, source_event_id="evt-7", payload_sha256=D("c"))
        self.state.mark_wakeup_processed(project=PROJECT, source_event_id="evt-7", now_ms=NOW)
        self.assertEqual(self.state.unprocessed_wakeups(project=PROJECT), ())


class ProviderCallTests(Base):
    """The call index links a call to a REAL grant: the foreign key is the point.

    `df_provider_calls.grant_id` references `grants(grant_id)`, so a call cannot record itself
    as started against a grant that was never issued. These tests therefore take an actual
    grant out of the lease store rather than inventing an identifier.
    """

    def call(self):
        attempt, bundle = self.reserved()
        self.bundle = bundle
        return attempt, self.state.reserve_call(
            call_id="call-1", attempt_id=attempt.attempt_id, invocation_id="inv-1",
            request_sha256=D("c"), allowance_receipt_sha256=D("4"), reserved_microusd=1_000_000)

    def grant(self, request_id="op-1"):
        return self.store.consume_grant(self.bundle, project=PROJECT, semantic_operation="provider_call",
                                        request_id=request_id, request_sha256=D("c"),
                                        now=seconds_of(NOW))["grant_id"]

    def test_a_started_call_must_name_a_grant_that_exists(self):
        self.call()
        with self.assertRaises(sqlite3.IntegrityError):
            self.state.record_started(call_id="call-1", grant_id="never-issued")
        self.assertEqual(self.state.call("call-1")["state"], "reserved")

    def test_the_durable_record_precedes_the_send(self):
        _, row = self.call()
        self.assertEqual(row["state"], "reserved")
        self.assertIsNone(row["grant_id"])
        grant_id = self.grant()
        started = self.state.record_started(call_id="call-1", grant_id=grant_id)
        self.assertEqual((started["state"], started["grant_id"]), ("started", grant_id))

    def test_c09_the_same_call_id_with_a_different_request_is_a_conflict(self):
        attempt, _ = self.call()
        with self.assertRaises(OperationalRefused):
            self.state.reserve_call(call_id="call-1", attempt_id=attempt.attempt_id,
                                    invocation_id="inv-1", request_sha256=D("d"),
                                    allowance_receipt_sha256=D("4"), reserved_microusd=1)
        self.assertEqual(self.state.call("call-1")["request_sha256"], D("c"))

    def test_an_identical_reservation_replays_without_a_second_row(self):
        attempt, first = self.call()
        again = self.state.reserve_call(call_id="call-1", attempt_id=attempt.attempt_id,
                                        invocation_id="inv-1", request_sha256=D("c"),
                                        allowance_receipt_sha256=D("4"), reserved_microusd=1_000_000)
        self.assertEqual(again, first)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM df_provider_calls").fetchone()[0], 1)

    def test_an_unknown_bill_stays_unknown(self):
        self.call()
        self.state.record_started(call_id="call-1", grant_id=self.grant())
        uncertain = self.state.mark_call_uncertain(call_id="call-1",
                                                   unknown_reason="timeout after possible send")
        self.assertEqual(uncertain["state"], "uncertain")
        self.assertIsNone(uncertain["billed_microusd"])
        observed = self.state.observe_call(call_id="call-1", result_sha256=D("e"))
        self.assertIsNone(observed["billed_microusd"], "no observation is not zero")
        self.assertEqual(observed["reserved_microusd"], 1_000_000,
                         "the reservation is retained; there is no refund")

    def test_a_started_call_cannot_be_declared_not_started(self):
        self.call()
        self.state.record_started(call_id="call-1", grant_id=self.grant())
        with self.assertRaises(OperationalRefused) as caught:
            self.state.mark_call_not_started(call_id="call-1")
        self.assertIn("cannot make", str(caught.exception))

    def test_a_cancel_before_the_send_records_not_started(self):
        self.call()
        self.assertEqual(self.state.mark_call_not_started(call_id="call-1")["state"], "not_started")

    def test_unresolved_calls_are_listed_until_they_are_observed(self):
        attempt, _ = self.call()
        self.assertEqual([c["call_id"] for c in self.state.unresolved_calls()], ["call-1"])
        self.state.record_started(call_id="call-1", grant_id=self.grant())
        self.state.observe_call(call_id="call-1", result_sha256=D("e"), billed_microusd=12)
        self.assertEqual(self.state.unresolved_calls(attempt_id=attempt.attempt_id), ())

    def test_a_negative_or_boolean_amount_is_refused(self):
        attempt, _ = self.reserved()
        for amount in (-1, True):
            with self.subTest(amount=amount):
                with self.assertRaises(OperationalRefused):
                    self.state.reserve_call(call_id="c", attempt_id=attempt.attempt_id,
                                            invocation_id="i", request_sha256=D("c"),
                                            allowance_receipt_sha256=D("4"),
                                            reserved_microusd=amount)


class ClockTests(Base):
    def test_c14_a_clock_that_moves_backwards_blocks_time_dependent_authority(self):
        self.reserved()
        self.work(D("8"), project="ShaishiBear/other")
        attempt = self.state.open_attempt(work_key=D("8"), project="ShaishiBear/other",
                                          request={}, now_ms=NOW)
        attempt = self.state.record_budget_receipt(attempt_id=attempt.attempt_id,
                                                   receipt_sha256=D("4"),
                                                   expected_version=attempt.version, now_ms=NOW)
        with self.assertRaises(LeaseRefused) as caught:
            self.state.reserve(attempt_id=attempt.attempt_id, expected_version=attempt.version,
                               owner="c2", role="coordinator", resources=["other-repo"],
                               subject_sha256=D("5"), envelope_sha256=D("9"), ttl_seconds=60,
                               now_ms=NOW - 10_000)
        self.assertIn("clock rollback", str(caught.exception))
        self.assertEqual(self.state.attempt(attempt.attempt_id).state, "budget_pending")


class BoundsTests(Base):
    def test_c16_malformed_input_is_refused_at_the_boundary(self):
        for bad in ({"work_key": "not-a-digest"}, {"project": "bad project name"},
                    {"priority": 9}, {"created_sequence": -1}):
            with self.subTest(**bad):
                args = {"work_key": D("1"), "project": PROJECT, "proposal_sha256": D("2"),
                        "programme_sha256": D("3"), "created_sequence": 7, "priority": 3}
                args.update(bad)
                with self.assertRaises(OperationalRefused):
                    self.state.put_work(**args)

    def test_the_request_digest_is_canonical_and_order_independent(self):
        self.assertEqual(canonical_request_sha256({"a": 1, "b": 2}),
                         canonical_request_sha256({"b": 2, "a": 1}))
        self.assertNotEqual(canonical_request_sha256({"a": 1}), canonical_request_sha256({"a": 2}))

    def test_an_outbox_limit_outside_its_bounds_is_refused(self):
        for limit in (0, 1001, True):
            with self.subTest(limit=limit):
                with self.assertRaises(OperationalRefused):
                    self.state.pending_outbox(limit=limit)

    def test_the_schema_itself_refuses_an_unknown_state(self):
        attempt = self.opened()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute(
                "UPDATE df_attempts SET state = 'invented' WHERE attempt_id = ?",
                (attempt.attempt_id,))


if __name__ == "__main__":
    unittest.main()
