import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "scripts" / "factory_merge_verify.py"
spec = importlib.util.spec_from_file_location("factory_merge_verify", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE = "a" * 40
HEAD = "b" * 40
TREE = "c" * 40
MERGE = "d" * 40
EVIDENCE = "e" * 64


class MergeVerifyTests(unittest.TestCase):
    def bundle(self, **overrides):
        value = {
            "version": "6.0",
            "base_ref": "main",
            "base_sha": BASE,
            "head_sha": HEAD,
            "head_tree_sha": TREE,
        }
        value.update(overrides)
        return value

    def meta(self, **overrides):
        value = {
            "baseRefName": "main",
            "baseRefOid": BASE,
            "headRefOid": HEAD,
            "mergedAt": "2026-08-24T16:00:00Z",
            "mergeCommit": {"oid": MERGE},
        }
        value.update(overrides)
        return value

    def authorize(self, bundle=None, meta=None, **overrides):
        values = {
            "evidence_sha256": EVIDENCE,
            "pr_meta": meta or self.meta(),
            "local_head": HEAD,
            "current_base": BASE,
            "head_tree": TREE,
            "base_is_ancestor": True,
        }
        values.update(overrides)
        return m.pre_authorization(bundle or self.bundle(), **values)

    def post(self, bundle=None, authorization=None, meta=None, **overrides):
        bundle = bundle or self.bundle()
        authorization = authorization or self.authorize(bundle=bundle)
        values = {
            "evidence_sha256": EVIDENCE,
            "pr_meta": meta or self.meta(),
            "current_base": MERGE,
            "merge_sha": MERGE,
            "merge_parents": [BASE],
            "merge_tree": TREE,
        }
        values.update(overrides)
        return m.post_result(bundle, authorization, **values)

    def test_pre_authorizes_exact_evidenced_base_head_and_tree(self):
        result = self.authorize()
        self.assertEqual(result["head_tree_sha"], TREE)

    def test_pre_rejects_main_moving_after_evidence(self):
        with self.assertRaises(SystemExit):
            self.authorize(current_base="f" * 40)

    def test_pre_rejects_pr_head_moving_after_evidence(self):
        with self.assertRaises(SystemExit):
            self.authorize(local_head="f" * 40)

    def test_pre_rejects_head_tree_mismatch(self):
        with self.assertRaises(SystemExit):
            self.authorize(head_tree="f" * 40)

    def test_pre_rejects_non_main_target(self):
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(base_ref="release"))

    def test_pre_requires_base_to_be_head_ancestor(self):
        with self.assertRaises(SystemExit):
            self.authorize(base_is_ancestor=False)

    def test_post_verifies_squash_parent_and_tree(self):
        result = self.post()
        self.assertEqual(result["verdict"], "verified")
        self.assertEqual(result["merge_sha"], MERGE)

    def test_post_rejects_tampered_authorization(self):
        auth = self.authorize()
        auth["head_sha"] = "f" * 40
        with self.assertRaises(SystemExit):
            self.post(authorization=auth)

    def test_post_rejects_wrong_squash_parent(self):
        with self.assertRaises(SystemExit):
            self.post(merge_parents=["f" * 40])

    def test_post_rejects_merge_commit_with_two_parents(self):
        with self.assertRaises(SystemExit):
            self.post(merge_parents=[BASE, "f" * 40])

    def test_post_rejects_tree_not_identical_to_evidenced_head(self):
        with self.assertRaises(SystemExit):
            self.post(merge_tree="f" * 40)

    def test_post_rejects_main_tip_moving_past_merge(self):
        with self.assertRaises(SystemExit):
            self.post(current_base="f" * 40)

    def test_post_rejects_github_reporting_different_merge_sha(self):
        with self.assertRaises(SystemExit):
            self.post(meta=self.meta(mergeCommit={"oid": "f" * 40}))

    def test_post_rejects_unmerged_pr(self):
        with self.assertRaises(SystemExit):
            self.post(meta=self.meta(mergedAt=None))


if __name__ == "__main__":
    unittest.main()
