"""Atomic lease and grant store (C05): all-or-nothing acquisition, identities that never reset,
heartbeats that cannot recreate ownership, releases that cannot clear an in-flight operation,
grants with final terminal states and at-most-once execution, a clock that may not run
backwards, and a real two-process race for one resource."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from factory_kernel.canonical import sha256_bytes
from factory_kernel.lease_store import GuardResult, LeaseRefused, LeaseStore, validate_generation

ROOT = Path(__file__).resolve().parents[2]
REQ, SUBJ = sha256_bytes(b"request"), sha256_bytes(b"subject")


def presented(**overrides) -> dict:
    fields = {"epoch": "E", "generation": 4, "owner": "run1", "expires_at": 10, "active": True, "consumed": False, "uncertain": False}
    fields.update(overrides)
    return fields


CURRENT = {"current_epoch": "E", "current_generation": 4, "current_owner": "run1"}


class GuardTests(unittest.TestCase):
    def test_priority_is_schema_then_identity_then_live_state_then_uncertainty_then_replay(self):
        self.assertEqual(validate_generation(presented(), CURRENT, now=9), GuardResult("eligible", ()))
        self.assertEqual(validate_generation(presented(active=False, uncertain=True, consumed=True), {**CURRENT, "current_epoch": "E2"}, now=99).reason_codes,
                         ("lease_identity_mismatch",), "identity outranks every live-state and replay reason")
        self.assertEqual(validate_generation(presented(active=False, uncertain=True, consumed=True), CURRENT, now=99).reason_codes, ("lease_inactive",))
        self.assertEqual(validate_generation(presented(uncertain=True, consumed=True), CURRENT, now=10).reason_codes, ("lease_expired",))
        self.assertEqual(validate_generation(presented(uncertain=True, consumed=True), CURRENT, now=9).reason_codes, ("effect_uncertain",))
        self.assertEqual(validate_generation(presented(consumed=True), CURRENT, now=9).reason_codes, ("grant_consumed",))
        for bad in (presented(generation="4"), presented(active="yes"), presented(owner=""), presented(expires_at=-1)):
            self.assertEqual(validate_generation(bad, CURRENT, now=9).reason_codes, ("schema_invalid",), bad)
        self.assertEqual(validate_generation(presented(), CURRENT, now="9").reason_codes, ("schema_invalid",))

    def test_per_resource_generations_are_compared_as_a_bundle(self):
        lease = presented(generations={"issue:7": 2, "pr:5": 9}); lease.pop("generation")
        current = {"current_epoch": "E", "current_owner": "run1", "current_generations": {"issue:7": 2, "pr:5": 9}}
        self.assertEqual(validate_generation(lease, current, now=1).status, "eligible")
        moved = {**current, "current_generations": {"issue:7": 3, "pr:5": 9}}
        self.assertEqual(validate_generation(lease, moved, now=1).reason_codes, ("lease_identity_mismatch",))
        self.assertEqual(validate_generation(lease, CURRENT, now=1).reason_codes, ("schema_invalid",), "a bundle cannot be compared with one shared integer")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = LeaseStore(self.tmp / "leases.sqlite")
        self.addCleanup(self.store.close)

    def acquire(self, owner="run1", resources=("issue:7", "pr:5"), now=100, ttl=60, req=REQ):
        return self.store.acquire_many(owner=owner, role="builder", resources=resources, request_sha256=req, subject_sha256=SUBJ,
                                       ttl_seconds=ttl, now=now)

    def test_acquisition_is_all_or_nothing_and_generations_never_reset(self):
        first = self.acquire()
        self.assertEqual((first.generations, first.epoch, first.expires_at), ({"issue:7": 1, "pr:5": 1}, self.store.epoch, 160))
        with self.assertRaises(LeaseRefused) as ctx:
            self.acquire(owner="run2", resources=("pr:5", "issue:9"), now=101)
        self.assertIn("pr:5@run1", str(ctx.exception))
        self.assertIsNone(self.store.lease("nonexistent"))
        rows = self.store.connection.execute("SELECT resource, generation FROM resources ORDER BY resource").fetchall()
        self.assertEqual(rows, [("issue:7", 1), ("pr:5", 1)], "the refused bundle incremented nothing, not even issue:9")
        self.assertEqual(self.store.release(first, now=102), GuardResult("eligible", ()))
        second = self.acquire(owner="run2", resources=("pr:5",), now=103)
        self.assertEqual(second.generations, {"pr:5": 2}, "released leases are tombstones; the generation moves on")
        self.assertEqual(self.store.lease(first.lease_id)["active"], False)

    def test_a_heartbeat_extends_only_a_live_matching_bundle_and_never_recreates_ownership(self):
        bundle = self.acquire(now=100, ttl=10)
        self.assertEqual(self.store.heartbeat(bundle, ttl_seconds=10, now=105).status, "eligible")
        self.assertEqual(self.store.lease(bundle.lease_id)["expires_at"], 115)
        self.assertEqual(self.store.heartbeat(bundle, ttl_seconds=10, now=115).reason_codes, ("lease_expired",), "expiry is now >= expires_at")
        self.assertEqual(self.store.lease(bundle.lease_id)["expires_at"], 115, "a stale heartbeat changed nothing")
        self.assertEqual(self.store.reap(now=116), (bundle.lease_id,))
        self.assertEqual(self.store.heartbeat(bundle, ttl_seconds=10, now=117).reason_codes, ("lease_inactive",))
        other = self.acquire(owner="run2", now=118, ttl=10)
        self.assertEqual(self.store.heartbeat(bundle, ttl_seconds=10, now=119).reason_codes, ("lease_identity_mismatch",),
                         "once the generations moved on, the old bundle is not even the same identity; identity outranks state")
        forged = type(bundle)(bundle.lease_id, bundle.epoch, "run2", bundle.role, other.generations, bundle.request_sha256, bundle.subject_sha256, 200)
        self.assertEqual(self.store.heartbeat(forged, ttl_seconds=10, now=119).reason_codes, ("lease_identity_mismatch",))

    def test_a_restored_epoch_invalidates_every_existing_bundle(self):
        bundle = self.acquire()
        self.store.rotate_epoch()
        self.assertEqual(self.store.heartbeat(bundle, ttl_seconds=10, now=101).reason_codes, ("lease_identity_mismatch",))
        with self.assertRaises(LeaseRefused):
            self.store.consume_grant(bundle, project="p", semantic_operation="merge", request_id="r1", request_sha256=REQ, now=101)

    def test_grants_execute_at_most_once_replay_identically_and_never_leave_a_terminal_state(self):
        bundle = self.acquire()
        grant = self.store.consume_grant(bundle, project="p", semantic_operation="merge", request_id="r1", request_sha256=REQ, now=101)
        self.assertEqual((grant["state"], grant["execute"], grant["replayed"]), ("started", True, False))
        # A second operation on the lease while the first is pending is refused; the release is too.
        with self.assertRaises(LeaseRefused):
            self.store.consume_grant(bundle, project="p", semantic_operation="label", request_id="r2", request_sha256=REQ, now=102)
        self.assertEqual(self.store.release(bundle, now=102).reason_codes, ("effect_uncertain",))
        again = self.store.consume_grant(bundle, project="p", semantic_operation="merge", request_id="r1", request_sha256=REQ, now=103)
        self.assertEqual((again["execute"], again["replayed"], again["state"]), (False, True, "started"), "a pending operation is never reissued")
        with self.assertRaises(LeaseRefused) as ctx:
            self.store.consume_grant(bundle, project="p", semantic_operation="merge", request_id="r1", request_sha256=sha256_bytes(b"other"), now=103)
        self.assertIn("conflict", str(ctx.exception))
        self.store.observe_operation(grant["grant_id"], outcome="success", result={"merged": "abc"}, now=104)
        replay = self.store.consume_grant(bundle, project="p", semantic_operation="merge", request_id="r1", request_sha256=REQ, now=105)
        self.assertEqual((replay["execute"], replay["replayed"], replay["result"]), (False, True, {"merged": "abc"}))
        with self.assertRaises(LeaseRefused):
            self.store.observe_operation(grant["grant_id"], outcome="failure", result={}, now=106)
        self.assertEqual(self.store.grant(grant["grant_id"])["state"], "observed_success")
        self.assertEqual(self.store.release(bundle, now=107).status, "eligible", "an observed operation is not in flight")

    def test_a_lost_response_is_uncertain_and_blocks_the_resource_until_reconciled(self):
        bundle = self.acquire()
        grant = self.store.consume_grant(bundle, project="p", semantic_operation="merge", request_id="r1", request_sha256=REQ, now=101)
        self.store.observe_operation(grant["grant_id"], outcome="uncertain", result=None, now=102)
        again = self.store.consume_grant(bundle, project="p", semantic_operation="merge", request_id="r1", request_sha256=REQ, now=103)
        self.assertEqual((again["execute"], again.get("refused")), (False, "prior operation is pending or uncertain"))
        self.assertEqual(self.store.release(bundle, now=104).reason_codes, ("effect_uncertain",))
        self.store.reap(now=200)
        with self.assertRaises(LeaseRefused) as ctx:
            self.acquire(owner="run2", resources=("pr:5",), now=201)
        self.assertIn("unresolved operation", str(ctx.exception))
        # Reconciliation by independent observation of the remote system resolves it; then work resumes.
        self.store.observe_operation(grant["grant_id"], outcome="failure", result={"observed": "not merged"}, now=202)
        self.assertEqual(self.acquire(owner="run2", resources=("pr:5",), now=203).generations, {"pr:5": 2})

    def test_expiry_removes_permission_and_a_clock_rollback_blocks_authority(self):
        bundle = self.acquire(now=100, ttl=5)
        with self.assertRaises(LeaseRefused):
            self.store.consume_grant(bundle, project="p", semantic_operation="merge", request_id="r1", request_sha256=REQ, now=105)
        with self.assertRaises(LeaseRefused) as ctx:
            self.store.heartbeat(bundle, ttl_seconds=5, now=99)
        self.assertIn("rollback", str(ctx.exception))
        self.assertEqual(self.store.lease(bundle.lease_id)["expires_at"], 105)

    def test_two_processes_racing_for_one_resource_yield_exactly_one_owner(self):
        path = self.tmp / "race.sqlite"
        LeaseStore(path).close()
        script = textwrap.dedent(f"""
            import json, pathlib, sys, time
            sys.path.insert(0, {str(ROOT)!r})
            from factory_kernel.lease_store import LeaseRefused, LeaseStore
            store = LeaseStore({str(path)!r})
            owner = sys.argv[1]
            # A file barrier: each process announces it is ready, then both spin until both are, so
            # interpreter start-up skew cannot serialise them. The pre/post acquire clocks are
            # reported so the test can check the two attempts really overlapped in time.
            barrier = pathlib.Path(sys.argv[2])
            (barrier / owner).write_text("ready")
            deadline = time.time() + 30
            while len(list(barrier.iterdir())) < 2:
                if time.time() > deadline:
                    raise SystemExit("barrier timeout")
            pre = time.perf_counter_ns(); wall_pre = time.time()
            try:
                bundle = store.acquire_many(owner=owner, role="builder", resources=["pr:5"], request_sha256={REQ!r},
                                            subject_sha256={SUBJ!r}, ttl_seconds=30, now=100)
                out = {{"owner": owner, "won": True, "generation": bundle.generations["pr:5"]}}
            except LeaseRefused as exc:
                out = {{"owner": owner, "won": False, "reason": str(exc)}}
            out["pre"] = wall_pre; out["post"] = time.time()
            print(json.dumps(out))
        """)
        barrier = self.tmp / "barrier"; barrier.mkdir()
        procs = [subprocess.Popen([sys.executable, "-c", script, owner, str(barrier)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for owner in ("runA", "runB")]
        outputs = [p.communicate(timeout=60) for p in procs]
        results = [json.loads(o[0].strip().splitlines()[-1]) for o in outputs]
        # Both attempts were in flight at once: each started before the other finished.
        a, b = results
        self.assertLess(a["pre"], b["post"], (results, outputs)); self.assertLess(b["pre"], a["post"], (results, outputs))
        winners = [r for r in results if r["won"]]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(winners[0]["generation"], 1)
        loser = next(r for r in results if not r["won"])
        self.assertIn("pr:5@", loser["reason"])
        store = LeaseStore(path)
        self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM leases WHERE active = 1").fetchone()[0], 1)
        store.close()


if __name__ == "__main__":
    unittest.main()
