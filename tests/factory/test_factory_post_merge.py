import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.post_merge import assert_exact_main, result_payload, verified_merge
from harness import post_merge


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

    def test_main_is_fetched_again_after_the_long_proof_before_any_result_is_written(self):
        for change in ("unchanged", "moved", "unreadable"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                verification, output = root / "merge.json", root / "post.json"
                verification.write_text(json.dumps({"version": "1.0", "verdict": "verified",
                                                    "merge_sha": MERGE, "tree_sha": TREE}))
                remote, tracking, proof_ran, fetches = MERGE, MERGE, False, 0

                def run(argv, **kwargs):
                    nonlocal remote, tracking, proof_ran, fetches
                    if argv[:2] == ["git", "fetch"]:
                        fetches += 1
                        if proof_ran and change == "unreadable":
                            raise RuntimeError("main fetch unavailable")
                        tracking = remote
                    if "harness/ci.py" in argv:
                        proof_ran = True
                        if change == "moved":
                            remote = "c" * 40
                    return SimpleNamespace(returncode=0, stdout="", stderr="")

                def git_oid(revision, **kwargs):
                    if revision == "origin/main":
                        return tracking
                    return TREE if revision.endswith("^{tree}") else MERGE

                with patch.object(post_merge, "run", side_effect=run), \
                        patch.object(post_merge, "git_oid", side_effect=git_oid), \
                        patch.object(post_merge, "create_detached", return_value=SimpleNamespace(path=root)), \
                        patch.object(post_merge, "remove") as remove, \
                        patch.object(post_merge, "parse_transcript", return_value={"e2e_steps": 21}):
                    if change == "unchanged":
                        result = post_merge.execute(merge_verification=verification, output=output)
                        self.assertEqual(result["origin_main_sha"], MERGE)
                        self.assertTrue(output.exists())
                    else:
                        with self.assertRaises((RuntimeError, ValueError)):
                            post_merge.execute(merge_verification=verification, output=output)
                        self.assertFalse(output.exists(), "stale or unreadable main cannot produce verified proof")
                    self.assertEqual(fetches, 2)
                    remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
