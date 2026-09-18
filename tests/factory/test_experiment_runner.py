"""Contained experiment execution (SPECIFICATION 8, C08 execute_registered, WP08): a real lease
store, a real child interpreter with a scrubbed environment, receipts verified against the spec,
timeouts and failures recorded as attempts and never as measurements, leases released always."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel import experiment_runner
from factory_kernel.canonical import sha256_value
from factory_kernel.experiment_runner import (CONTAINMENT, ExperimentFailed, ExperimentRefused, ExperimentTimeout, cancel,
                                              execute_registered, reserve, verify_receipt)
from factory_kernel.experiments import run_experiment
from factory_kernel.exploration import Exploration
from factory_kernel.lease_store import LeaseRefused, LeaseStore
from tests.factory import test_exploration as exploration_fixture
from tests.factory.test_frontdoor_intent import OWNER

SPEC = {"kind": "lookup-workload-v1", "strategies": ["linear", "hash"], "keys": list(range(100)), "queries": [99] * 20}
CONTEXT = {"commit": "a" * 40, "files": {}, "policies": {}, "coverage": "selected-committed-source-only", "proof_status": "not-established"}
CONTEXT["identity"] = sha256_value({k: v for k, v in CONTEXT.items() if k != "identity"})


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.leases = LeaseStore(Path(tmp.name) / "leases.sqlite")
        self.addCleanup(self.leases.close)

    def test_a_contained_run_returns_the_same_receipt_as_the_in_process_runner_and_hides_credentials(self) -> None:
        expected = run_experiment(SPEC, context=CONTEXT)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-should-not-cross", "GH_TOKEN": "ghp-should-not-cross", "DF_PLAIN": "kept"}):
            result = execute_registered(SPEC, context=CONTEXT, leases=self.leases, session_id="lookup", reservation_id="r1", now=100)
        attempt = result.pop("attempt")
        self.assertEqual(result, expected)  # the boundary changes nothing about the measurement
        self.assertEqual((attempt["status"], attempt["family"], attempt["containment"]), ("complete", "lookup-workload-v1", CONTAINMENT))
        keys = attempt["child_environment_keys"]
        self.assertNotIn("OPENROUTER_API_KEY", keys)
        self.assertNotIn("GH_TOKEN", keys)
        self.assertNotIn("DF_PLAIN", keys)  # nothing is inherited, not even a harmless variable
        self.assertIn("PYTHONPATH", keys)
        self.assertTrue(set(keys) <= set(experiment_runner.CHILD_ENVIRONMENT_KEYS) | {"PYTHONPATH", "PYTHONIOENCODING"}, keys)
        self.assertEqual(attempt["lease_release"], {"status": "eligible", "reason_codes": []})
        self.assertGreaterEqual(attempt["elapsed_ms"], 0)
        self.assertFalse(self.leases.lease(attempt["lease_id"])["active"])  # released, not live

    def test_a_timeout_is_an_attempt_that_names_its_limit_and_releases_the_lease(self) -> None:
        with self.assertRaises(Exception) as caught:
            execute_registered(SPEC, context=CONTEXT, leases=self.leases, session_id="lookup", reservation_id="r1", now=100, wall_seconds=0.01)
        self.assertIsInstance(caught.exception, ExperimentTimeout, "a wall-clock expiry is a timeout, not a generic failure")
        attempt = getattr(caught.exception, "attempt", {})
        self.assertEqual((attempt.get("status"), attempt.get("wall_seconds")), ("timeout", 0.01))
        self.assertIn("did not finish within the stated environment and limit", attempt.get("detail", ""))
        self.assertEqual((attempt.get("lease_release") or {}).get("status"), "eligible", "the lease is released after a timeout")
        # The slot is free again: the next reservation on the same session succeeds.
        bundle = reserve(self.leases, session_id="lookup", reservation_id="r2", spec=SPEC, now=101)
        self.assertEqual(cancel(self.leases, bundle, now=102), {"status": "eligible", "reason_codes": []})

    def test_a_child_that_fails_to_start_or_exits_is_a_failed_attempt_never_a_measurement(self) -> None:
        with self.assertRaises(ExperimentFailed) as caught:
            execute_registered(SPEC, context=CONTEXT, leases=self.leases, session_id="lookup", reservation_id="r1", now=100,
                               python=str(Path(tempfile.gettempdir()) / "no-such-interpreter-df"))
        self.assertEqual(caught.exception.attempt["status"], "failed-to-start")
        completed = subprocess.CompletedProcess(args=[], returncode=3, stdout=b"", stderr=b"boom")
        with patch.object(experiment_runner, "_spawn", return_value=completed), self.assertRaises(Exception) as caught:
            execute_registered(SPEC, context=CONTEXT, leases=self.leases, session_id="lookup", reservation_id="r1", now=100)
        self.assertIsInstance(caught.exception, ExperimentFailed)
        attempt = getattr(caught.exception, "attempt", {})
        self.assertEqual((attempt.get("status"), attempt.get("stderr_tail")), ("failed", "boom"))
        self.assertEqual((attempt.get("lease_release") or {}).get("status"), "eligible", "the lease is released after a failed child")
        reported = subprocess.CompletedProcess(args=[], returncode=0, stdout=b'{"status": "failed", "failure": "IntentRefused"}', stderr=b"")
        with patch.object(experiment_runner, "_spawn", return_value=reported), self.assertRaises(Exception) as caught:
            execute_registered(SPEC, context=CONTEXT, leases=self.leases, session_id="lookup", reservation_id="r1", now=100)
        self.assertIsInstance(caught.exception, ExperimentFailed, "a child that reported failure is a failed attempt, never a refused or accepted receipt")
        self.assertEqual(getattr(caught.exception, "attempt", {}).get("detail"), "IntentRefused")

    def test_a_malformed_or_forged_receipt_is_refused(self) -> None:
        good = run_experiment(SPEC, context=CONTEXT)
        for bad, reason in ((b"not json", "no JSON object"),
                            (b'{"status": "complete", "receipt": {"runner": "other", "kind": "measured"}}', "requested family"),
                            (b'{"status": "complete", "receipt": ' + _json({**good, "results": {"linear": good["results"]["linear"]}}) + b"}", "exactly the requested strategies"),
                            (b'{"status": "complete", "receipt": ' + _json({**good, "results": {**good["results"], "hash": {**good["results"]["hash"], "latency_ms": 1}}}) + b"}", "outside the family"),
                            (b'{"status": "complete", "receipt": ' + _json({**good, "input_sha256": "0" * 64}) + b"}", "another spec"),
                            (b'{"status": "complete", "receipt": ' + _json({**good, "context_identity": "f" * 64}) + b"}", "context-free family"),
                            (b'{"status": "complete", "receipt": ' + _json({**good, "scope": "everything"}) + b"}", "scope or qualification")):
            with self.subTest(reason):
                completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=bad, stderr=b"")
                with patch.object(experiment_runner, "_spawn", return_value=completed), self.assertRaisesRegex(ExperimentRefused, reason) as caught:
                    execute_registered(SPEC, context=CONTEXT, leases=self.leases, session_id="lookup", reservation_id="r1", now=100)
                attempt = getattr(caught.exception, "attempt", {})
                self.assertEqual(attempt.get("status"), "refused")
                self.assertEqual((attempt.get("lease_release") or {}).get("status"), "eligible", "the lease is released after a refused receipt")
        self.assertEqual(verify_receipt(SPEC, CONTEXT, good), good)

    def test_a_context_bound_family_must_bind_the_frozen_context_and_a_data_only_family_must_not_claim_one(self) -> None:
        fixture = exploration_fixture.ExplorationTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.boundary_context()
        spec, context = fixture.boundary_request()["probe"], deepcopy(fixture.context)
        good = run_experiment(spec, context=context)
        self.assertEqual(good["context_identity"], context["identity"])
        self.assertEqual(verify_receipt(spec, context, good), good)
        for forged, reason in (({k: v for k, v in good.items() if k != "context_identity"}, "does not bind the frozen"),
                               ({**good, "context_identity": "f" * 64}, "does not bind the frozen")):
            with self.subTest(reason), self.assertRaisesRegex(ExperimentRefused, reason):
                verify_receipt(spec, context, forged)
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b'{"status": "complete", "receipt": ' + _json(forged) + b"}", stderr=b"")
            with patch.object(experiment_runner, "_spawn", return_value=completed), self.assertRaises(Exception) as caught:
                execute_registered(spec, context=context, leases=self.leases, session_id="layers", reservation_id="r1", now=100)
            self.assertIsInstance(caught.exception, ExperimentRefused)
        # The contained run of the context-bound family binds the context exactly as the in-process run does.
        contained = execute_registered(spec, context=context, leases=self.leases, session_id="layers", reservation_id="r2", now=101)
        contained.pop("attempt")
        self.assertEqual(contained, good)
        lookup = run_experiment(SPEC, context=CONTEXT)
        self.assertNotIn("context_identity", lookup)  # data-only: no context claim, and verify_receipt requires none
        self.assertTrue(experiment_runner.context_bound(spec["kind"]))
        self.assertFalse(experiment_runner.context_bound(SPEC["kind"]))

    def test_one_experiment_per_session_at_a_time_and_the_runner_slot_is_shared(self) -> None:
        held = reserve(self.leases, session_id="lookup", reservation_id="r1", spec=SPEC, now=100)
        with self.assertRaisesRegex(LeaseRefused, "live lease"):
            reserve(self.leases, session_id="lookup", reservation_id="r2", spec=SPEC, now=101)
        with self.assertRaisesRegex(LeaseRefused, "live lease"):
            reserve(self.leases, session_id="other", reservation_id="r3", spec=SPEC, now=101)  # the shared runner slot
        with self.assertRaisesRegex(LeaseRefused, "live lease"):  # a run cannot even start while the slot is held; nothing is spent
            execute_registered(SPEC, context=CONTEXT, leases=self.leases, session_id="other", reservation_id="r3", now=101)
        cancel(self.leases, held, now=102)
        again = reserve(self.leases, session_id="other", reservation_id="r3", spec=SPEC, now=103)
        self.assertEqual(again.owner, "exploration:other")
        with self.assertRaises(Exception):
            reserve(self.leases, session_id="x", reservation_id="r", spec={"kind": "no-such-family"}, now=104)

    def test_the_child_entry_is_the_only_runnable_command(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        completed = subprocess.run([sys.executable, "-m", "factory_kernel.experiment_runner", "--spec", "x"], capture_output=True, env=env, timeout=60)
        self.assertEqual(completed.returncode, 2)
        self.assertIn(b"only the --child entry", completed.stderr)
        # The launch line runs the interpreter without the user site (-s) and without writing bytecode (-B).
        source = Path(experiment_runner.__file__).read_text(encoding="utf-8")
        self.assertIn('[python, "-s", "-B", "-m", "factory_kernel.experiment_runner", "--child"]', source)
        self.assertEqual(experiment_runner.main([]), 2)


def _json(value) -> bytes:
    import json
    return json.dumps(value, sort_keys=True).encode("utf-8")


class ExplorationIntegrationTests(unittest.TestCase):
    """The exploration service runs every experiment through the contained runner."""

    def setUp(self) -> None:
        self.fixture = exploration_fixture.ExplorationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_the_service_records_the_attempt_and_the_receipt_is_the_in_process_receipt(self) -> None:
        self.fixture.add()
        request = self.fixture.experiment_request()
        state = self.fixture.engine.experiment("citations", self.fixture.command(request), principal=OWNER)
        observation = state["sessions"]["lookup"]["observations"][-1]
        self.assertEqual(observation["status"], "complete")
        self.assertEqual(observation["receipt"], run_experiment(request["probe"], context=self.fixture.context))
        attempt = observation.get("attempt") or {}
        self.assertEqual((attempt.get("status"), attempt.get("containment")), ("complete", CONTAINMENT), "the service must run the contained runner and record its attempt")
        self.assertEqual((attempt.get("lease_release") or {}).get("status"), "eligible")
        self.assertTrue((self.fixture.store.directory / "experiments.sqlite").exists())
        leases = self.fixture.engine.leases()
        self.assertFalse(leases.lease(attempt.get("lease_id"))["active"])

    def test_a_timed_out_experiment_is_a_failed_observation_with_its_attempt(self) -> None:
        self.fixture.add()
        with patch.object(experiment_runner, "WALL_SECONDS", 0.01), patch("factory_kernel.exploration.execute_registered",
                                                                          side_effect=lambda spec, **kw: execute_registered(spec, wall_seconds=0.01, **kw)):
            state = self.fixture.engine.experiment("citations", self.fixture.command(self.fixture.experiment_request()), principal=OWNER)
        observation = state["sessions"]["lookup"]["observations"][-1]
        self.assertEqual((observation["status"], observation["failure"], observation["measurements"]), ("failed", "ExperimentTimeout", []))
        self.assertEqual(observation["attempt"]["status"], "timeout")
        self.assertEqual(self.fixture.inspect()["comparison"]["preferred"], "scan")  # nothing measured, nothing preferred


if __name__ == "__main__":
    unittest.main()
