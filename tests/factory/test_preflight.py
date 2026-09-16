"""Comparison evidence must support a preference, never manufacture a universal winner."""
from copy import deepcopy
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.preflight import compare, compile_candidate, compile_policy, compile_pool, plan_facts


def policy():
    return {"question_id": "delivery-strategy", "question": "Which bounded strategy deserves qualification?",
            "signals": ["multiple-strategies"],
            "criteria": [{"id": "surface", "kind": "planned-files", "question": "Prefer fewer planned files."}],
            "priorities": [], "tie_break": "retain-tradeoff", "max_candidates": 4,
            "budget": {"max_calls": 2, "max_usd": 2.0, "wall_seconds": 676}, "direct_reason": None}


def candidate(key="baseline", count=1):
    return {"id": key, "family": key, "mechanism": "Use the " + key + " causal strategy.",
            "is_baseline": key == "baseline", "planned_files": [f"app/file{i}.py" for i in range(count)],
            "new_dependencies": [], "assumptions": [{"statement": "Current scale is representative.",
            "revisit_when": "Observed scale invalidates the approach."}], "risks": ["Scale is not measured here."]}


def registration(frozen=None):
    return {"policy": compile_policy(frozen or policy()),
            "constraints": [{"id": "privacy", "text": "Keep private data private."}]}


def challenge(reg, pool):
    return {"registration_sha256": sha256_value(reg), "candidates_sha256": sha256_value(pool),
            "family_groups": [[row["id"]] for row in pool], "assessments": [
                {"candidate_id": row["id"],
                 "constraints": [{"id": constraint["id"], "status": "appears-met", "basis": "Reasoned fit; unqualified."}
                                 for constraint in reg["constraints"]],
                 "judgments": [{"id": criterion["id"], "status": "mixed", "basis": "Reasoning, not a benchmark."}
                               for criterion in reg["policy"]["criteria"] if criterion["kind"] == "judgment"],
                 "uncertainties": []} for row in pool]}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.reg = registration()
        self.pool = compile_pool([candidate(), candidate("alternative", 2)], [], policy=self.reg["policy"])
        self.audit = challenge(self.reg, self.pool)

    def decision(self):
        return compare(registration=self.reg, pool=self.pool, challenge=self.audit, tracked_files=["app/file0.py"])

    def rebind(self):
        self.audit["registration_sha256"] = sha256_value(self.reg)
        self.audit["candidates_sha256"] = sha256_value(self.pool)

    def test_plan_dominance_explains_loser_but_never_claims_runtime_or_global_optimality(self):
        result = self.decision()
        self.assertEqual(result["selected_candidate"], "baseline")
        self.assertEqual(result["status"], "provisional-recommendation")
        loser = result["retained_alternatives"][1]
        self.assertEqual(loser["dominated_by"], ["baseline"])
        self.assertEqual(loser["plan_observations"]["proposed_new_paths"], ["app/file1.py"])
        self.assertEqual(result["qualification_status"], "UNPROVEN")
        self.assertIs(result["proof_reuse_allowed"], False)
        self.assertEqual(result["global_optimality"], "not-established")

    def test_permutation_and_candidate_renaming_cannot_hardcode_the_winner(self):
        self.pool[0]["id"], self.pool[1]["id"] = "zulu", "alpha"
        self.audit = challenge(self.reg, self.pool)
        self.pool.reverse()
        self.rebind()
        self.assertEqual(self.decision()["selected_candidate"], "zulu")

    def test_confirmed_plan_tradeoff_is_retained_without_a_registered_priority(self):
        self.reg["policy"]["criteria"].append({"id": "deps", "kind": "new-dependencies", "question": "Fewer dependencies."})
        self.pool[0]["new_dependencies"] = ["cache-package"]
        self.rebind()
        result = self.decision()
        self.assertEqual(result["status"], "needs-priority")
        self.assertIsNone(result["selected_candidate"])
        self.assertEqual(len(result["frontier"]), 2)
        self.reg["policy"]["priorities"] = ["deps"]
        self.rebind()
        result = self.decision()
        self.assertEqual(result["selected_candidate"], "alternative")
        self.assertEqual(result["retained_alternatives"][0]["not_selected_because"]["criterion_id"], "deps")

    def test_priority_change_after_registration_is_refused(self):
        self.reg["policy"]["priorities"] = ["surface"]
        with self.assertRaises(IntentRefused):
            self.decision()

    def test_changed_candidate_after_challenge_is_refused(self):
        self.pool[0]["planned_files"] = ["app/different.py"]
        with self.assertRaises(IntentRefused):
            self.decision()

    def test_decision_changing_uncertainty_on_loser_prevents_false_dominance(self):
        self.audit["assessments"][1]["uncertainties"] = [{"question": "Would the larger plan be faster?",
            "would_change_decision_if": "The baseline misses the latency requirement.",
            "resolution": "Measure representative workloads in an approved disposable probe."}]
        result = self.decision()
        self.assertEqual(result["status"], "needs-evidence")
        self.assertIsNone(result["selected_candidate"])
        self.assertTrue(all(not row["dominated_by"] for row in result["retained_alternatives"]))

    def test_constraint_conflict_is_not_traded_for_a_better_plan_count(self):
        self.audit["assessments"][0]["constraints"][0]["status"] = "conflict"
        result = self.decision()
        self.assertEqual(result["selected_candidate"], "alternative")
        self.assertEqual(result["retained_alternatives"][0]["status"], "screened-out")
        self.assertEqual(result["retained_alternatives"][0]["conflicts"], ["privacy"])

    def test_unknown_constraint_does_not_establish_a_viable_winner(self):
        self.audit["assessments"][0]["constraints"][0]["status"] = "unknown"
        self.assertEqual(self.decision()["status"], "needs-evidence")

    def test_no_viable_candidate_has_no_handoff_preference(self):
        for row in self.audit["assessments"]:
            row["constraints"][0]["status"] = "conflict"
        self.assertEqual(self.decision()["status"], "no-viable-candidate")

    def test_judgment_can_inform_a_provisional_preference_but_never_pareto_dominance(self):
        self.reg["policy"]["criteria"] = [{"id": "operability", "kind": "judgment", "question": "Simpler to operate?"}]
        self.reg["policy"]["priorities"] = ["operability"]
        self.audit = challenge(self.reg, self.pool)
        self.audit["assessments"][1]["judgments"][0]["status"] = "favourable"
        result = self.decision()
        self.assertEqual(result["selected_candidate"], "alternative")
        self.assertTrue(all(not row["dominated_by"] for row in result["retained_alternatives"]))
        self.audit["assessments"][0]["judgments"][0]["status"] = "unknown"
        self.assertEqual(self.decision()["status"], "needs-evidence")

    def test_baseline_tiebreak_cannot_erase_a_real_tradeoff(self):
        self.reg["policy"]["tie_break"] = "prefer-baseline-if-equal"
        self.reg["policy"]["criteria"].append({"id": "deps", "kind": "new-dependencies", "question": "Fewer dependencies."})
        self.pool[0]["new_dependencies"] = ["cache-package"]
        self.rebind()
        self.assertIsNone(self.decision()["selected_candidate"])
        self.pool[0]["new_dependencies"] = []
        self.pool[1]["planned_files"] = self.pool[0]["planned_files"][:]
        self.rebind()
        self.assertEqual(self.decision()["selected_candidate"], "baseline")

    def test_user_candidate_receives_identical_comparison_semantics(self):
        self.pool[1]["origin"] = "user"
        self.pool[0]["planned_files"].append("app/extra.py")
        self.pool[1]["planned_files"].pop()
        self.rebind()
        self.assertEqual(self.decision()["selected_candidate"], "alternative")

    def test_omitted_constraint_candidate_or_judgment_is_refused(self):
        for mutate in (lambda x: x["assessments"].pop(), lambda x: x["assessments"][0]["constraints"].clear(),
                       lambda x: x["family_groups"].pop()):
            original = deepcopy(self.audit)
            mutate(self.audit)
            with self.assertRaises(IntentRefused):
                self.decision()
            self.audit = original

    def test_cosmetic_families_do_not_create_search_breadth(self):
        self.audit["family_groups"] = [["baseline", "alternative"]]
        self.assertEqual(self.decision()["status"], "needs-diversity")

    def test_renamed_identical_candidate_is_not_a_second_strategy(self):
        duplicate = candidate()
        duplicate.update(id="renamed", family="renamed", is_baseline=False)
        with self.assertRaises(IntentRefused):
            compile_pool([candidate(), duplicate], [], policy=policy())

    def test_pool_requires_baseline_unique_ids_and_bound(self):
        for generated in ([candidate("other"), candidate("other2")], [candidate(), candidate()],
                          [candidate()], [candidate(), *[candidate(f"extra{i}") for i in range(4)]]):
            with self.assertRaises(IntentRefused):
                compile_pool(generated, [], policy=policy())

    def test_paths_and_policy_cannot_smuggle_execution_or_unbounded_search(self):
        for path in ("../secret", "/secret", "C:/secret", "app//x", ".git/config", "app\\x"):
            raw = candidate()
            raw["planned_files"] = [path]
            with self.assertRaises(IntentRefused):
                compile_candidate(raw, origin="system")
        for field, value in (("max_candidates", 7), ("signals", ["invented"]), ("priorities", ["invented"]),
                             ("budget", {"max_calls": 3, "max_usd": 2, "wall_seconds": 676})):
            raw = policy()
            raw[field] = value
            with self.assertRaises(IntentRefused):
                compile_policy(raw)

    def test_plan_counts_are_explicitly_declared_properties(self):
        facts = plan_facts(compile_candidate(candidate("other", 2), origin="system"), ["app/file0.py"])
        self.assertEqual(facts["planned-files"], 2)
        self.assertEqual(facts["existing_paths"], ["app/file0.py"])
        self.assertEqual(facts["proposed_new_paths"], ["app/file1.py"])


if __name__ == "__main__":
    unittest.main()
