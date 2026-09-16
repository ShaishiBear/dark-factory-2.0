"""A measured counterexample changes strategy; causal history never grants proof authority."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import sha256_value
from factory_kernel.decision_history import explain_history
from factory_kernel.exploration import Exploration
from factory_kernel.exploration_probe import run_probe, validate_probe
from factory_kernel.exploration_repository import inspect_repository
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.programme import ProgrammeRefused
from tests.factory import test_frontdoor_programme as fixture
from tests.factory.test_frontdoor_intent import OWNER, WORKER


def policy():
    return {"criteria": [{"id": "lookup", "question": "Bound work for the declared lookup workload.",
                           "kind": "measurement", "unit": "comparisons", "ceiling": 1000}],
            "priorities": ["lookup"], "budget": {"calls": 4, "usd": 4, "probe_units": 1000000},
            "max_candidates": 8, "max_rounds": 4}


def claim(key, dependencies=None):
    return {"id": key, "statement": "This strategy is suitable for the current workload.",
            "kind": "assumption", "depends_on": dependencies or [], "acceptance": ["AC1"],
            "revisit_when": "The measured workload exceeds the registered work bound."}


def candidate(key, strategy, low, high, claims=None):
    return {"id": key, "family": strategy, "mechanism": "Use " + strategy + " lookup.",
            "baseline": strategy == "linear", "claim_ids": claims or [key + "-assumption"],
            "trajectory": {"implementation": "Implement the named lookup strategy.",
                "validation": "Compare lookup results and measure work for the same workload.",
                "failure_repair": "Wrong workload assumptions require strategy reconsideration.",
                "migration_reversal": "Keep the current lookup interface so implementation can be replaced."},
            "predictions": {"lookup": {"low": low, "high": high, "basis": "Uncalibrated initial estimate."}},
            "probe_strategy": strategy, "origin": "system"}


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ProgrammeReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.context = {"commit": "a" * 40, "files": {}, "policies": {}, "coverage": "selected-committed-source-only",
                        "proof_status": "not-established"}
        self.rehash()
        self.stop = Mock()
        self.engine = Exploration(self.store, lambda: deepcopy(self.context), check_stop=self.stop,
                                  app_login="factory[bot]")
        self.counter = 0
        self.open()

    def rehash(self):
        self.context["identity"] = sha256_value({k: v for k, v in self.context.items() if k != "identity"})

    def command(self, request, session="lookup", key=None):
        self.counter += 1
        return {"idempotency_key": key or f"step-{self.counter}", "session_id": session,
                "expected_project_version": self.store.snapshot("citations", principal=OWNER)["project_version"],
                "request": deepcopy(request)}

    def open(self, session="lookup", parent=None, frozen=None):
        return self.engine.open("citations", self.command({"question": "Which lookup architecture?",
            "parent_session": parent, "policy": frozen or policy()}, session), principal=OWNER)

    def add(self, session="lookup", suffix=""):
        keys = ["scan" + suffix, "index" + suffix]
        return self.engine.add_candidates("citations", self.command({
            "claims": [claim(key + "-assumption") for key in keys], "candidates": [
                candidate(keys[0], "linear", 10, 20), candidate(keys[1], "hash", 40, 60)]}, session), principal=OWNER)

    def experiment_request(self):
        return {"probe": {"kind": "lookup-workload-v1", "strategies": ["linear", "hash"],
                          "keys": list(range(100)), "queries": [99] * 20},
                "targets": [{"candidate_id": key, "criterion_id": "lookup", "metric": "comparisons",
                             "falsifies_claim": key + "-assumption"}
                            for key in ("scan", "index")],
                "question": "Does repeated lookup exceed the work bound?",
                "would_change_decision_if": "If linear lookup exceeds 1000 comparisons, prefer the feasible alternative.",
                "claim_ids": ["scan-assumption", "index-assumption"]}

    def inspect(self, session="lookup"):
        return self.engine.inspect("citations", session, principal=OWNER)

    def recommend(self, session="lookup"):
        return self.engine.recommend("citations", self.command({"stop_reason": "sufficient-support",
            "rationale": "Registered criteria distinguish the viable strategies for this workload.",
            "remaining_uncertainty": ["Production workload and latency are not established."],
            "next_useful_experiment": "Use representative production workload in a separately authorised probe."}, session),
            principal=OWNER)

    def test_experiment_changes_choice_then_compiles_programme_with_same_approved_scope(self):
        self.add()
        before = self.inspect()["comparison"]
        self.assertEqual(before["preferred"], "scan")
        self.assertEqual(before["candidates"]["scan"]["values"]["lookup"]["kind"], "predicted")
        result = self.engine.experiment("citations", self.command(self.experiment_request()), principal=OWNER)
        self.assertEqual(result["sessions"]["lookup"]["observations"][0]["status"], "complete")
        after = self.inspect()["comparison"]
        self.assertEqual(after["preferred"], "index")
        self.assertEqual(after["candidates"]["scan"]["status"], "rejected-assumption")
        self.assertEqual(result["claims"]["scan-assumption"]["status"], "invalidated")
        self.assertEqual(result["claims"]["index-assumption"]["observations"][-1]["outcome"], "supported-in-probe")
        self.assertEqual(after["candidates"]["scan"]["values"]["lookup"]["low"], 2000)
        self.assertEqual(after["candidates"]["index"]["values"]["lookup"]["kind"], "measured")
        self.recommend()
        result = self.engine.handoff("citations", self.command({"proposal": self.fixture.request["proposal"]}), principal=OWNER)
        handoff = result["sessions"]["lookup"]["handoffs"][-1]
        self.assertEqual(handoff["qualification_status"], "UNPROVEN")
        self.assertIs(handoff["proof_reuse_allowed"], False)
        self.assertEqual(handoff["input"]["spec"], self.fixture.spec)
        self.assertEqual(handoff["candidate_id"], "index")
        history = explain_history(self.store, "citations", principal=OWNER)
        self.assertEqual(history["exploration"], result)
        self.assertEqual(len(result["sessions"]["lookup"]["candidates"]), 2)

    def test_restart_replay_does_not_repeat_probe_or_reset_spend(self):
        self.add()
        command = self.command(self.experiment_request())
        first = self.engine.experiment("citations", command, principal=OWNER)
        self.engine = Exploration(self.store, lambda: deepcopy(self.context), check_stop=self.stop, app_login="factory[bot]")
        with patch("factory_kernel.exploration.run_probe", side_effect=AssertionError("must not run again")):
            second = self.engine.experiment("citations", command, principal=OWNER)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(IntentRefused, "already reserved"):
            self.engine.experiment("citations", self.command(self.experiment_request()), principal=OWNER)

    def test_reservation_is_durable_before_execution(self):
        self.add()
        real = run_probe
        def inspect(spec, **kwargs):
            state = self.inspect()
            self.assertEqual(len(state["session"]["reservations"]), 1)
            self.assertGreater(state["budget"]["probe_units"], 0)
            self.assertEqual(next(iter(state["session"]["reservations"].values()))["status"], "pending")
            return real(spec, **kwargs)
        with patch("factory_kernel.exploration.run_probe", side_effect=inspect):
            self.engine.experiment("citations", self.command(self.experiment_request()), principal=OWNER)

    def test_failed_experiment_never_becomes_measurement(self):
        self.add()
        with patch("factory_kernel.exploration.run_probe", side_effect=RuntimeError("environment failed")):
            result = self.engine.experiment("citations", self.command(self.experiment_request()), principal=OWNER)
        observation = result["sessions"]["lookup"]["observations"][-1]
        self.assertEqual(observation["status"], "failed")
        self.assertEqual(observation["measurements"], [])
        self.assertEqual(self.inspect()["comparison"]["preferred"], "scan")

    def test_stopped_completion_stays_pending_and_replay_never_spends_again(self):
        self.add()
        command = self.command(self.experiment_request())
        real = run_probe
        def stopping(spec, **kwargs):
            receipt = real(spec, **kwargs)
            self.stop.side_effect = IntentRefused("stop")
            return receipt
        with patch("factory_kernel.exploration.run_probe", side_effect=stopping):
            with self.assertRaises(IntentRefused):
                self.engine.experiment("citations", command, principal=OWNER)
        self.stop.side_effect = None
        with patch("factory_kernel.exploration.run_probe", side_effect=AssertionError("replayed")):
            self.engine.experiment("citations", command, principal=OWNER)
        with self.assertRaisesRegex(IntentRefused, "uncertain reserved"):
            self.recommend()

    def test_changed_repository_cannot_complete_probe_or_export_old_recommendation(self):
        self.add()
        self.recommend()
        self.context["commit"] = "b" * 40
        self.rehash()
        with self.assertRaisesRegex(IntentRefused, "repository changed"):
            self.engine.handoff("citations", self.command({"proposal": self.fixture.request["proposal"]}), principal=OWNER)

    def test_handoff_replay_does_not_hide_repository_drift(self):
        self.add()
        self.recommend()
        command = self.command({"proposal": self.fixture.request["proposal"]})
        self.engine.handoff("citations", command, principal=OWNER)
        self.context["commit"] = "b" * 40
        self.rehash()
        with self.assertRaisesRegex(IntentRefused, "repository changed"):
            self.engine.handoff("citations", command, principal=OWNER)

    def test_handoff_replay_cannot_restore_invalidated_recommendation(self):
        self.add()
        self.recommend()
        command = self.command({"proposal": self.fixture.request["proposal"]})
        self.engine.handoff("citations", command, principal=OWNER)
        self.engine.observe_claim("citations", self.command({"claim_id": "scan-assumption", "status": "invalidated",
            "observation": "The selection premise is false.", "source": "Owner observation."}), principal=OWNER)
        with self.assertRaisesRegex(IntentRefused, "current recommendation"):
            self.engine.handoff("citations", command, principal=OWNER)

    def test_concurrent_project_change_keeps_result_pending_without_false_observation(self):
        self.add()
        real = run_probe
        def changed(spec, **kwargs):
            result = real(spec, **kwargs)
            self.open("concurrent")
            return result
        with patch("factory_kernel.exploration.run_probe", side_effect=changed):
            with self.assertRaisesRegex(IntentRefused, "stale project version"):
                self.engine.experiment("citations", self.command(self.experiment_request()), principal=OWNER)
        state = self.inspect()["session"]
        self.assertEqual(state["observations"], [])
        self.assertEqual(next(iter(state["reservations"].values()))["status"], "pending")

    def test_shared_budget_cannot_reset_via_another_question(self):
        self.add()
        self.engine.experiment("citations", self.command(self.experiment_request()), principal=OWNER)
        self.open("other", "lookup")
        self.assertEqual(self.inspect("other")["budget"], self.inspect()["budget"])
        raised = policy()
        raised["budget"]["probe_units"] += 1
        with self.assertRaisesRegex(IntentRefused, "shared|share"):
            self.open("reset", frozen=raised)

    def test_units_and_missing_preregistration_cannot_relabel_measurement(self):
        self.add()
        for mutate in (lambda x: x["targets"][0].update(metric="matches"),
                       lambda x: x.update(would_change_decision_if=""),
                       lambda x: x.update(claim_ids=["invented"])):
            request = self.experiment_request()
            mutate(request)
            with self.assertRaises((IntentRefused, ProgrammeRefused)):
                self.engine.experiment("citations", self.command(request), principal=OWNER)
        self.assertEqual(self.inspect()["budget"]["probe_units"], 0)

    def test_claim_invalidation_reopens_only_causal_dependents_and_keeps_alternatives(self):
        self.add()
        self.recommend()
        self.open("unrelated")
        self.add("unrelated", "-other")
        self.recommend("unrelated")
        result = self.engine.observe_claim("citations", self.command({"claim_id": "scan-assumption",
            "status": "invalidated", "observation": "The workload assumption failed.", "source": "Owner observation."}), principal=OWNER)
        self.assertEqual(result["sessions"]["lookup"]["status"], "reconsideration-required")
        self.assertEqual(result["sessions"]["unrelated"]["status"], "recommended")
        with self.assertRaisesRegex(IntentRefused, "current recommendation"):
            self.engine.handoff("citations", self.command({"proposal": self.fixture.request["proposal"]}), principal=OWNER)
        self.engine.reopen("citations", self.command({"reason": "Reconsider the failed assumption."}), principal=OWNER)
        self.assertEqual(self.inspect()["comparison"]["preferred"], "index")
        result = self.recommend()
        self.assertEqual(len(result["sessions"]["lookup"]["recommendations"]), 2)
        self.assertEqual(len(result["sessions"]["lookup"]["candidates"]), 2)

    def test_challenge_is_not_rejection_and_never_qualifies_claim(self):
        self.add()
        self.recommend()
        result = self.engine.observe_claim("citations", self.command({"claim_id": "scan-assumption",
            "status": "challenged", "observation": "An observation needs investigation.", "source": "Owner report."}), principal=OWNER)
        self.assertEqual(result["sessions"]["lookup"]["status"], "challenged")
        self.assertEqual(self.inspect()["comparison"]["candidates"]["scan"]["status"], "viable")
        self.assertEqual(result["claims"]["scan-assumption"]["observations"][-1]["qualification_status"], "UNPROVEN")

    def test_worker_cannot_record_exploration_and_intake_json_cannot_forge_probe(self):
        with self.assertRaisesRegex(IntentRefused, "owner scoped"):
            self.engine.inspect("citations", "lookup", principal=WORKER)
        with self.assertRaises(IntentRefused):
            self.engine.add_candidates("citations", self.command({}), principal=WORKER)
        command = {"idempotency_key": "forge", "expected_project_version": self.command({})["expected_project_version"],
                   "operation": "preflight-event", "payload": {"kind": "observed", "data": {"status": "complete"}}}
        with self.assertRaisesRegex(IntentRefused, "unknown intake"):
            self.store.execute("citations", command, principal=OWNER)

    def test_claim_cycles_unknown_acceptance_and_duplicate_candidates_refused(self):
        for row in (claim("bad", ["bad"]), {**claim("bad"), "acceptance": ["NEW"]}):
            with self.assertRaises(IntentRefused):
                self.engine.add_candidates("citations", self.command({"claims": [row],
                    "candidates": [candidate("scan", "linear", 1, 2, ["bad"])]}), principal=OWNER)

    def test_old_approval_cannot_survive_new_intent(self):
        self.add()
        version = self.store.snapshot("citations", principal=OWNER)["project_version"]
        self.store.execute("citations", {"idempotency_key": "new-intent", "expected_project_version": version,
            "operation": "record-intent", "payload": {"wording": "Different scope."}}, principal=OWNER)
        with self.assertRaisesRegex(IntentRefused, "own approval"):
            self.inspect()

    def test_reasoned_reassessment_cannot_overwrite_observed_measurement(self):
        self.add()
        self.engine.experiment("citations", self.command(self.experiment_request()), principal=OWNER)
        self.engine.assess("citations", self.command({"candidates": {
            "scan": {"lookup": {"low": 0, "high": 0, "basis": "Model disagrees with measurement."}},
            "index": {"lookup": {"low": 100000, "high": 100000, "basis": "Model prefers its original answer."}}},
            "basis": "Reasoning cannot erase the recorded experiment."}), principal=OWNER)
        result = self.inspect()["comparison"]
        self.assertEqual(result["preferred"], "index")
        self.assertEqual(result["candidates"]["scan"]["values"]["lookup"]["low"], 2000)

    def test_transitive_invalidation_follows_edges_not_names(self):
        self.engine.add_candidates("citations", self.command({"claims": [claim("root"), claim("middle", ["root"]),
            claim("leaf", ["middle"]), claim("separate")], "candidates": [
                candidate("scan", "linear", 1, 2, ["leaf"]), candidate("index", "hash", 3, 4, ["separate"])]}), principal=OWNER)
        self.recommend()
        result = self.engine.observe_claim("citations", self.command({"claim_id": "root", "status": "invalidated",
            "observation": "The dependency no longer holds.", "source": "Owner observation."}), principal=OWNER)
        self.assertEqual(result["sessions"]["lookup"]["status"], "reconsideration-required")
        self.assertEqual(self.inspect()["comparison"]["preferred"], "index")

    def test_uncertain_experiment_can_be_abandoned_without_releasing_reserved_budget(self):
        self.add()
        command = self.command(self.experiment_request())
        with patch("factory_kernel.exploration.run_probe", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.experiment("citations", command, principal=OWNER)
        before = self.inspect()
        reservation = next(iter(before["session"]["reservations"]))
        self.engine.abandon_pending("citations", self.command({"reservation_id": reservation,
            "reason": "Process was interrupted; discard unrecorded observations."}), principal=OWNER)
        self.assertEqual(self.inspect()["budget"], before["budget"])
        self.assertEqual(self.inspect()["session"]["reservations"][reservation]["status"], "abandoned")

    def test_finite_work_budget_refuses_before_experiment_is_called(self):
        low = policy()
        low["budget"]["probe_units"] = 1
        other = fixture.ProgrammeReviewTests()
        other.setUp()
        try:
            engine = Exploration(other.store, lambda: deepcopy(self.context), check_stop=self.stop, app_login="factory[bot]")
            command = {"idempotency_key": "open", "expected_project_version": 3, "session_id": "limited",
                "request": {"question": "Choose a lookup.", "parent_session": None, "policy": low}}
            engine.open("citations", command, principal=OWNER)
            command.update(idempotency_key="add", expected_project_version=4, request={"claims": [claim("scan-assumption"),
                claim("index-assumption")], "candidates": [candidate("scan", "linear", 1, 2), candidate("index", "hash", 3, 4)]})
            engine.add_candidates("citations", command, principal=OWNER)
            command.update(idempotency_key="probe", expected_project_version=5, request=self.experiment_request())
            with patch("factory_kernel.exploration.run_probe", side_effect=AssertionError("must not spend")):
                with self.assertRaisesRegex(IntentRefused, "cumulative experiment budget"):
                    engine.experiment("citations", command, principal=OWNER)
        finally:
            other.doCleanups()

    def test_qualitative_future_architecture_remains_judgment_not_fake_measurement(self):
        frozen = policy()
        frozen["criteria"].append({"id": "migration", "kind": "judgment", "unit": "qualitative",
                                   "question": "Ease of reversal for approved future work.", "ceiling": None})
        frozen["priorities"] = ["migration", "lookup"]
        self.open("architecture", frozen=frozen)
        candidates = [candidate("simple", "linear", 1, 2, ["simple-assumption"]),
                      candidate("reversible", "hash", 3, 4, ["reversible-assumption"])]
        for row, rating in zip(candidates, ("adverse", "favourable")):
            row["predictions"]["migration"] = {"assessment": rating, "basis": "Explicit architectural judgment."}
        self.engine.add_candidates("citations", self.command({"claims": [claim("simple-assumption"),
            claim("reversible-assumption")], "candidates": candidates}, "architecture"), principal=OWNER)
        result = self.inspect("architecture")["comparison"]
        self.assertEqual(result["preferred"], "reversible")
        self.assertFalse(result["sufficient_support"])
        self.assertEqual(result["candidates"]["reversible"]["values"]["migration"]["kind"], "judgment")
        with self.assertRaisesRegex(IntentRefused, "tradeoff"):
            self.recommend("architecture")


class FixedProbeTests(unittest.TestCase):
    def test_algorithms_execute_same_queries_and_report_real_work(self):
        spec = {"kind": "lookup-workload-v1", "strategies": ["linear", "binary", "hash"],
                "keys": list(range(100)), "queries": [99, 99, -1]}
        result = run_probe(spec, check_stop=lambda: None)
        self.assertEqual({row["matches"] for row in result["results"].values()}, {2})
        self.assertEqual(result["results"]["linear"]["comparisons"], 300)
        self.assertEqual(result["results"]["hash"]["comparisons"], 3)
        self.assertLess(result["results"]["binary"]["comparisons"], 30)
        self.assertEqual(result["input_sha256"], sha256_value(spec))

    def test_no_executable_input_or_unbounded_data(self):
        for change in ({"kind": "shell"}, {"keys": ["__import__('os')"]}, {"queries": [0] * 1001},
                       {"strategies": ["python"]}, {"argv": ["sh"]}):
            value = {"kind": "lookup-workload-v1", "strategies": ["linear"], "keys": [1], "queries": [1]}
            value.update(change)
            with self.assertRaises(IntentRefused):
                validate_probe(value)


class RepositoryTests(unittest.TestCase):
    def test_inspects_committed_imports_not_dirty_tree_and_binds_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.STDOUT)
            git("init")
            git("config", "user.email", "fixture@example.test")
            git("config", "user.name", "fixture")
            path = root / "app" / "lookup.py"
            path.parent.mkdir()
            path.write_text("import bisect\nfrom collections import Counter\ndef lookup(): pass\n")
            (root / ".factory").mkdir()
            (root / ".factory" / "architecture.json").write_text("{}")
            (root / "FACTORY_RULES.md").write_text("Independent qualification required.")
            git("add", ".")
            git("commit", "-m", "fixture")
            path.write_text("import dangerous_dirty_dependency\n")
            result = inspect_repository(root, ["app/lookup.py"])
            self.assertEqual(result["files"]["app/lookup.py"]["imports"], ["bisect", "collections"])
            self.assertEqual(result["files"]["app/lookup.py"]["definitions"], ["lookup"])
            self.assertEqual(result["commit"], git("rev-parse", "HEAD").decode().strip())
            for name in ("../secrets.py", "app/.env", "factory_kernel/runtime.py", "app/../private.py"):
                with self.assertRaises(IntentRefused):
                    inspect_repository(root, [name])


if __name__ == "__main__":
    unittest.main()
