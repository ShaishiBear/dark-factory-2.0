"""Search allocation and its limits (LINE_LEVEL_CLAIMS 5)."""
from __future__ import annotations

import unittest

from factory_kernel.search_policy import (
    SearchRefused, check_candidate_mix, evaluate_search_policy, propose_search_allocation, select_next_parent,
    structural_signature,
)


class AllocationTests(unittest.TestCase):
    def test_control_conservative_challenger_and_fixed_no_memory_share(self):
        proposal = propose_search_allocation("claim", {}, [], {"max_candidates": 4, "microusd": 2_000_000, "no_memory_share_percent": 34})
        self.assertEqual((proposal.total_candidates, proposal.control_included, proposal.conservative_slots,
                          proposal.challenger_slots, proposal.no_memory_slots), (4, True, 1, 1, 1))
        self.assertIsNone(proposal.limitation)
        capped = propose_search_allocation("claim", {}, [], {"max_candidates": 1, "microusd": 1, "no_memory_share_percent": 0})
        self.assertEqual((capped.total_candidates, capped.challenger_slots), (1, 0))
        self.assertIn("control only", capped.limitation)
        no_budget = propose_search_allocation("claim", {}, [], {"max_candidates": 4, "microusd": 0, "no_memory_share_percent": 50})
        self.assertIn("control only", no_budget.limitation)
        for bad in ({"max_candidates": 9, "microusd": 1, "no_memory_share_percent": 0},
                    {"max_candidates": 2, "microusd": 1, "no_memory_share_percent": 101},
                    {"max_candidates": True, "microusd": 1, "no_memory_share_percent": 0}):
            with self.subTest(bad=bad), self.assertRaises(SearchRefused):
                propose_search_allocation("claim", {}, [], bad)


class MixTests(unittest.TestCase):
    def test_duplicate_renamings_are_named_and_labels_are_not_believed(self):
        policy = propose_search_allocation("claim", {}, [], {"max_candidates": 4, "microusd": 10, "no_memory_share_percent": 34})
        a = b"-    return items.get(key)\n+    return lookup_table[key]\n"
        b = b"-    return items.get(key)\n+    return table[key]\n"  # a renaming of a
        c = b"-    return items.get(key)\n+    for item in items:\n+        if item == key:\n+            return item\n"
        self.assertEqual(structural_signature(a), structural_signature(b))
        self.assertNotEqual(structural_signature(a), structural_signature(c))
        rows = [{"id": "control", "is_control": True}, {"id": "a", "mechanism_family": "index"},
                {"id": "b", "mechanism_family": "novel-cache"}, {"id": "c", "mechanism_family": "scan", "no_memory": True}]
        report = check_candidate_mix(policy, rows, {"a": a, "b": b, "c": c})
        self.assertEqual(report.duplicate_ids, ("b",))
        self.assertEqual(report.families["novel-cache"], 1, "the label is reported, not trusted")
        self.assertFalse(report.meets_policy)
        self.assertIn("duplicate_renamings", report.reasons)
        clean = check_candidate_mix(policy, rows[:2] + rows[3:], {"a": a, "c": c})
        self.assertTrue(clean.meets_policy, clean.reasons)
        missing = check_candidate_mix(policy, [{"id": "a"}], {"a": a})
        self.assertIn("control_missing", missing.reasons)
        self.assertIn("no_memory_challenger_missing", missing.reasons)


class ParentAndEvaluationTests(unittest.TestCase):
    def test_parents_come_from_public_observations_only(self):
        policy = propose_search_allocation("claim", {}, [], {"max_candidates": 3, "microusd": 10, "no_memory_share_percent": 0})
        rows = [{"candidate_id": "x", "partition": "confirmation", "complete": True, "hard_pass": True, "metric": 1},
                {"candidate_id": "y", "partition": "public", "complete": True, "hard_pass": True, "metric": 5},
                {"candidate_id": "z", "partition": "public", "complete": True, "hard_pass": False, "metric": 0}]
        choice = select_next_parent(policy, rows, [])
        self.assertEqual(choice.parent_id, "y", "confirmation results and hard failures are never parents")
        self.assertIsNone(select_next_parent(policy, [], []).parent_id)
        with self.assertRaises(SearchRefused):
            select_next_parent(policy, [{"candidate_id": "y", "partition": "public", "complete": True, "hard_pass": True, "metric": 1.5}], [])

    def test_policy_evaluation_is_paired_family_bounded_and_never_equivalence(self):
        policy_rows = [{"task_family": f, "task_id": i, "quality": q} for f, i, q in (("a", 1, 3), ("a", 2, 2), ("b", 1, 5), ("c", 1, 1))]
        base_rows = [{"task_family": f, "task_id": i, "quality": q} for f, i, q in (("a", 1, 1), ("a", 2, 2), ("b", 1, 4), ("c", 1, 2))]
        result = evaluate_search_policy(policy_rows, base_rows, minimum_families=3)
        self.assertEqual((result.policy_wins, result.baseline_wins, result.ties, result.families, result.verdict), (2, 1, 1, 3, "policy_ahead"))
        underpowered = evaluate_search_policy(policy_rows, base_rows, minimum_families=4)
        self.assertEqual(underpowered.verdict, "insufficient")
        even = evaluate_search_policy(policy_rows[:2], base_rows[:2], minimum_families=1)
        self.assertEqual(even.verdict, "policy_ahead")
        same = evaluate_search_policy(base_rows, base_rows, minimum_families=1)
        self.assertEqual(same.verdict, "no_difference_detected")
        self.assertIn("not equivalence", " ".join(same.limitations))
        with self.assertRaises(SearchRefused):
            evaluate_search_policy([{"task_family": "a", "task_id": 1, "quality": "high"}], [], minimum_families=1)


if __name__ == "__main__":
    unittest.main()
