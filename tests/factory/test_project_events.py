"""The canonical journal's one append primitive (C02 / WP01).

Acceptance pinned here: old fixtures reload and are reproduced byte-identically by the
migrated services; a same-key replay after unrelated appends returns the original event and
appends nothing; a conflicting actor or body refuses; two independent OS processes racing one
version yield exactly one append (thread tests are insufficient for the OS-lock contract);
pagination is bounded with an explicit `cursor_ahead`; capacity exhaustion is a refusal with
no partial append; an unknown operation or role refuses; a corrupted or unknown-version event
refuses reads without deleting history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal, intake_operations
from factory_kernel.project_events import (
    MAX_EVENTS, PAGE_LIMIT, EventAppend, OperationSpec, ProjectEvents, exact_replay, verify_chain,
)
from tests.factory import project_events_fixture as fixture

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "factory" / "fixtures" / "project-events"
REPO = "owner/product"
OWNER = Principal("maintainer", "owner")
SERVICE = Principal("transition-service", "service")
NOTE = "service-note"


def registry():
    return {NOTE: OperationSpec(NOTE, frozenset({"owner", "service"}), exact_replay), **intake_operations()}


class PrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = IntentStore(self.directory, repository=REPO, owner=OWNER.identity)
        self.events = ProjectEvents(self.store, registry())

    def append(self, key, payload, *, version=None, principal=OWNER, operation=NOTE):
        if version is None:
            version = len(self.events.read("p", OWNER))
        return self.events.append(project="p", principal=principal, expected_version=version,
                                  idempotency_key=key, operation=operation, payload=payload,
                                  validate_transition=lambda events: payload)

    def test_append_then_same_key_replay_after_unrelated_appends_returns_the_original(self):
        first = self.append("k1", {"x": 1})
        self.assertIsInstance(first, EventAppend)
        self.assertEqual((first.project_version, first.replayed, first.index), (1, False, 0))
        for n in range(2, 6):
            self.append(f"k{n}", {"x": n})
        replay = self.append("k1", {"x": 1}, version=1)
        self.assertEqual((replay.project_version, replay.replayed, replay.index), (5, True, 0))
        self.assertEqual(replay.event_sha256, first.event_sha256)
        self.assertEqual(len(self.events.read("p", OWNER)), 5, "nothing appended on replay")

    def test_different_body_or_actor_under_one_key_is_a_conflict_and_appends_nothing(self):
        self.append("k1", {"x": 1})
        for principal, payload in ((OWNER, {"x": 2}), (SERVICE, {"x": 1})):
            with self.subTest(principal=principal.role, payload=payload):
                with self.assertRaisesRegex(IntentRefused, "idempotency_conflict"):
                    self.append("k1", payload, version=1, principal=principal)
        self.assertEqual(len(self.events.read("p", OWNER)), 1)

    def test_stale_version_refuses_before_the_validator_runs(self):
        self.append("k1", {"x": 1})
        called = []
        with self.assertRaisesRegex(IntentRefused, "stale project version"):
            self.events.append(project="p", principal=OWNER, expected_version=0, idempotency_key="k2",
                               operation=NOTE, payload={}, validate_transition=lambda events: called.append(1))
        self.assertEqual(called, [])

    def test_validator_sees_the_verified_history_and_its_refusal_appends_nothing(self):
        self.append("k1", {"x": 1})
        seen = []

        def validate(events):
            seen.append([event["project_version"] for event in events])
            raise IntentRefused("semantic refusal")
        with self.assertRaisesRegex(IntentRefused, "semantic refusal"):
            self.events.append(project="p", principal=OWNER, expected_version=1, idempotency_key="k2",
                               operation=NOTE, payload={}, validate_transition=validate)
        self.assertEqual(seen, [[1]])
        self.assertEqual(len(self.events.read("p", OWNER)), 1)

    def test_unknown_operation_wrong_role_and_bad_inputs_refuse_before_the_lock(self):
        with self.assertRaisesRegex(IntentRefused, "unknown project operation"):
            self.append("k", {}, operation="not-registered")
        with self.assertRaisesRegex(IntentRefused, "may not append"):
            self.append("k", {}, operation="approve-spec", principal=Principal("intake-agent", "proposal"))
        with self.assertRaises(IntentRefused):
            self.append("k", {}, principal=Principal("stranger", "guest"))
        with self.assertRaises(IntentRefused):
            self.append("k", {}, principal=Principal("impostor", "owner"))
        with self.assertRaises(IntentRefused):
            self.append("", {})
        with self.assertRaises(IntentRefused):
            self.events.append(project="p", principal=OWNER, expected_version=True, idempotency_key="k",
                               operation=NOTE, payload={}, validate_transition=lambda e: {})
        with self.assertRaises(ValueError):
            ProjectEvents(self.store, {"a": OperationSpec("b", frozenset({"owner"}), exact_replay)})
        self.assertFalse((self.directory / "p.json").exists())

    def test_payload_is_detached_and_strictly_parsed(self):
        payload = {"list": [1, 2]}
        self.append("k1", payload)
        payload["list"].append(3)
        self.assertEqual(self.events.read("p", OWNER)[0]["command"]["payload"], {"list": [1, 2]})
        with self.assertRaises((IntentRefused, ValueError)):
            self.append("k2", {"nan": float("nan")})

    def test_page_is_bounded_and_a_cursor_ahead_never_restarts_the_chain(self):
        for n in range(7):
            self.append(f"k{n}", {"x": n})
        page = self.events.page("p", OWNER, after_version=0, limit=3)
        self.assertEqual(([e["project_version"] for e in page.events], page.next_after, page.status), ([1, 2, 3], 3, "ok"))
        page = self.events.page("p", OWNER, after_version=3, limit=PAGE_LIMIT)
        self.assertEqual(([e["project_version"] for e in page.events], page.next_after), ([4, 5, 6, 7], None))
        ahead = self.events.page("p", OWNER, after_version=9, limit=5)
        self.assertEqual((ahead.status, ahead.events, ahead.next_after, ahead.project_version), ("cursor_ahead", (), None, 7))
        self.assertEqual(len(self.events.read("p", OWNER)), 7)
        for after, limit in ((-1, 5), (0, 0), (0, PAGE_LIMIT + 1), ("0", 5), (True, 5), (0, 2.0)):
            with self.subTest(after=after, limit=limit), self.assertRaises(IntentRefused):
                self.events.page("p", OWNER, after_version=after, limit=limit)
        with self.assertRaises(IntentRefused):
            self.events.page("p", Principal("stranger", "guest"), after_version=0, limit=1)

    def test_capacity_exhaustion_is_a_refusal_with_no_partial_append(self):
        self.append("k1", {"x": 1})
        with patch("factory_kernel.project_events.MAX_EVENTS", 1):
            with self.assertRaisesRegex(IntentRefused, "capacity"):
                self.append("k2", {"x": 2})
        self.assertEqual(len(self.events.read("p", OWNER)), 1)
        current = (self.directory / "p.json").stat().st_size
        with patch("factory_kernel.frontdoor_intent.MAX_HISTORY_BYTES", current + 10):
            with self.assertRaisesRegex(IntentRefused, "capacity"):
                self.append("k3", {"x": 3, "padding": "p" * 64})
        self.assertEqual(len(self.events.read("p", OWNER)), 1)
        self.assertEqual((self.directory / "p.json").stat().st_size, current, "no partial write")
        self.assertEqual(MAX_EVENTS, 10000, "current capacity bound kept")

    def test_unknown_event_or_version_refuses_reads_without_deleting_history(self):
        self.append("k1", {"x": 1})
        path = self.directory / "p.json"
        good = path.read_bytes()
        events = json.loads(good)
        events[0]["schema_version"] = "9.0"
        path.write_bytes(canonical_bytes(events))
        with self.assertRaisesRegex(IntentRefused, "cannot be verified"):
            self.events.read("p", OWNER)
        with self.assertRaises(IntentRefused):
            self.append("k2", {"x": 2}, version=1)
        self.assertEqual(path.read_bytes(), canonical_bytes(events), "refusal deleted nothing")
        with self.assertRaises(IntentRefused):
            verify_chain(events, project="p", repository=REPO)
        verify_chain(json.loads(good), project="p", repository=REPO)
        path.write_bytes(good)
        self.assertEqual(len(self.events.read("p", OWNER)), 1)

    def test_two_os_processes_racing_one_version_yield_exactly_one_append(self):
        self.append("k0", {"x": 0})
        script = r'''
import sys, time, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
from factory_kernel.project_events import OperationSpec, ProjectEvents, exact_replay
store = IntentStore(Path(sys.argv[2]), repository="owner/product", owner="maintainer")
events = ProjectEvents(store, {"service-note": OperationSpec("service-note", frozenset({"owner"}), exact_replay)})
gate = Path(sys.argv[3])
while not gate.exists():
    time.sleep(0.005)
try:
    result = events.append(project="p", principal=Principal("maintainer", "owner"), expected_version=1,
                           idempotency_key=sys.argv[4], operation="service-note", payload={"who": sys.argv[4]},
                           validate_transition=lambda e: (time.sleep(0.2), {"who": sys.argv[4]})[1])
    print(json.dumps({"appended": True, "version": result.project_version}))
except (IntentRefused, OSError) as exc:
    # The store's OS lock is nonblocking: the loser sees the store's own refusal or the
    # platform's lock error (PermissionError on Windows, BlockingIOError on POSIX).
    print(json.dumps({"appended": False, "reason": type(exc).__name__ + ": " + str(exc)}))
'''
        gate = self.directory / "go"
        procs = [subprocess.Popen([sys.executable, "-c", script, str(ROOT), str(self.directory), str(gate), who],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for who in ("A", "B")]
        time.sleep(1.0)
        gate.write_text("go")
        outputs = []
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            outputs.append(json.loads(out.strip().splitlines()[-1]))
        appended = [row for row in outputs if row["appended"]]
        refused = [row for row in outputs if not row["appended"]]
        self.assertEqual(len(appended), 1, outputs)
        self.assertEqual(len(refused), 1, outputs)
        self.assertRegex(refused[0]["reason"], "busy|stale project version|PermissionError|BlockingIOError|locked")
        history = self.events.read("p", OWNER)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1]["command"]["payload"]["who"], appended[0] and history[1]["command"]["idempotency_key"])


class FixtureIdentityTests(unittest.TestCase):
    """The four migrated services reproduce the pinned baseline's bytes for the same flows."""

    def test_recorded_baseline_flows_are_reproduced_byte_for_byte(self):
        with tempfile.TemporaryDirectory(prefix="project-events-replay-") as tmp, \
                patch.dict(os.environ, {"FACTORY_WORKDIR": os.environ.get("FACTORY_WORKDIR", str(ROOT / ".factory-work"))}):
            recorded = fixture.record_flows(Path(tmp))
        for name in fixture.FLOWS:
            with self.subTest(flow=name):
                expected = (FIXTURES / f"{name}.json").read_bytes()
                self.assertEqual(recorded[name], expected, f"{name}: bytes differ from the baseline fixture")
                events = json.loads(expected)
                verify_chain(events, project="citations", repository=events[0]["repository"])
                self.assertGreater(len(events), 2)

    def test_fixtures_carry_every_operation_the_journal_registers(self):
        operations = set()
        for name in fixture.FLOWS:
            for event in json.loads((FIXTURES / f"{name}.json").read_bytes()):
                operations.add(event["command"]["operation"])
        self.assertEqual(operations, {"record-intent", "add-exploration", "propose-spec", "approve-spec",
                                      "preflight-event", "execution-budget-event", "replacement-intent-event"})
        self.assertEqual(sha256_value(sorted(operations)), sha256_value(sorted(operations)))


if __name__ == "__main__":
    unittest.main()
