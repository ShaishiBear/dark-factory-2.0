import hashlib
import unittest

from harness.post_merge import assert_exact_main, result_payload, verified_merge


MERGE = "a" * 40
TREE = "b" * 40


class PostMergeAuthorityTests(unittest.TestCase):
    def test_verified_merge_requires_exact_verified_v1_shape(self):
        self.assertEqual(
            verified_merge(
                {
                    "version": "1.0",
                    "verdict": "verified",
                    "merge_sha": MERGE,
                    "tree_sha": TREE,
                }
            ),
            (MERGE, TREE),
        )
        with self.assertRaisesRegex(ValueError, "verified merge evidence v1"):
            verified_merge(
                {
                    "version": "1.0",
                    "verdict": "failed",
                    "merge_sha": MERGE,
                    "tree_sha": TREE,
                }
            )

    def test_post_merge_requires_exact_main_tip(self):
        assert_exact_main(merge_sha=MERGE, main_sha=MERGE)
        with self.assertRaisesRegex(ValueError, "origin/main moved"):
            assert_exact_main(merge_sha=MERGE, main_sha="c" * 40)

    def test_result_is_bound_to_merge_tree_main_and_harness_bytes(self):
        transcript = "GATE_OK mode=full\nE2E_PASSED steps=7\n"
        observed = {"e2e_steps": 7, "unit_tests": 800}
        result = result_payload(
            merge_sha=MERGE,
            tree_sha=TREE,
            current_main_sha=MERGE,
            transcript=transcript,
            observed=observed,
        )
        self.assertEqual(result["version"], "1.0")
        self.assertEqual(result["verdict"], "verified")
        self.assertEqual(result["merge_sha"], MERGE)
        self.assertEqual(result["tree_sha"], TREE)
        self.assertEqual(result["origin_main_sha"], MERGE)
        self.assertEqual(
            result["harness_sha256"], hashlib.sha256(transcript.encode()).hexdigest()
        )
        self.assertEqual(result["observed"], observed)

    def test_zero_browser_steps_can_never_be_post_merge_ok(self):
        with self.assertRaisesRegex(ValueError, "no browser steps"):
            result_payload(
                merge_sha=MERGE,
                tree_sha=TREE,
                current_main_sha=MERGE,
                transcript="GATE_OK mode=full\n",
                observed={"e2e_steps": 0},
            )


if __name__ == "__main__":
    unittest.main()
