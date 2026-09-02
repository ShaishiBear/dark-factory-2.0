import hashlib
import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "harness" / "merge_verify.py"
spec = importlib.util.spec_from_file_location("factory_merge_verify", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE = "a" * 40
HEAD = "b" * 40
TREE = "c" * 40
MERGE = "d" * 40
EVIDENCE = "e" * 64
MANIFEST = "1" * 64
PROVENANCE = "2" * 64


def _digest(claim_id: str, role: str) -> str:
    return hashlib.sha256(f"{claim_id}:{role}".encode()).hexdigest()


class MergeVerifyTests(unittest.TestCase):
    def spine(self, **overrides):
        policy = m.load_policy(Path(__file__).parents[2] / ".factory/evidence-spine.json")
        value = {
            "version": "1.0",
            "base_sha": BASE,
            "head_sha": HEAD,
            "policy_sha256": policy.sha256(),
            "manifest_sha256": MANIFEST,
            "builder_provenance_sha256": PROVENANCE,
            "claims": [
                {
                    "claim_id": requirement.claim_id,
                    "completion_level": 100,
                    "artifact_sha256": _digest(requirement.claim_id, "artifact"),
                    "deterministic_sha256": (
                        _digest(requirement.claim_id, "deterministic")
                        if requirement.deterministic_required
                        else None
                    ),
                    "independent_sha256": (
                        _digest(requirement.claim_id, "independent")
                        if requirement.independent_required
                        else None
                    ),
                    "exact_head_sha": HEAD if requirement.exact_head_required else None,
                }
                for requirement in policy.requirements
            ],
            "completion_level": 100,
        }
        value.update(overrides)
        return value

    def bundle(self, **overrides):
        value = {
            "version": "5.0",
            "base_sha": BASE,
            "head_sha": HEAD,
            "spine": self.spine(),
            "run_manifest_sha256": MANIFEST,
            "builder_provenance_sha256": PROVENANCE,
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
            "merge_sha": MERGE,
            "merge_parents": [BASE],
            "merge_tree": TREE,
            "merge_is_ancestor": True,
        }
        values.update(overrides)
        return m.post_result(bundle, authorization, **values)

    def test_pre_authorizes_exact_evidenced_base_head_and_tree(self):
        result = self.authorize()
        self.assertEqual(result["base_sha"], BASE)
        self.assertEqual(result["head_sha"], HEAD)
        self.assertEqual(result["head_tree_sha"], TREE)

    def test_pre_rejects_missing_evidence_spine(self):
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=None))

    def test_pre_rejects_spine_below_global_100_percent(self):
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=self.spine(completion_level=80)))

    def test_pre_rejects_incomplete_evidence_spine_claim(self):
        spine = self.spine()
        spine["claims"][0]["completion_level"] = 80
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=spine))

    def test_pre_rejects_claim_missing_independent_certification(self):
        """Merge authority re-derives independence from policy, not from the closure's summary."""
        spine = self.spine()
        row = next(r for r in spine["claims"] if r["claim_id"] == "design")
        row["independent_sha256"] = None
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=spine))

    def test_pre_rejects_claim_missing_deterministic_certification(self):
        spine = self.spine()
        next(r for r in spine["claims"] if r["claim_id"] == "impact")["deterministic_sha256"] = None
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=spine))

    def test_pre_rejects_deterministic_certification_reused_as_independent(self):
        spine = self.spine()
        row = next(r for r in spine["claims"] if r["claim_id"] == "architecture-governor")
        row["independent_sha256"] = row["deterministic_sha256"]
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=spine))

    def test_pre_rejects_claim_that_independently_certifies_itself(self):
        spine = self.spine()
        row = next(r for r in spine["claims"] if r["claim_id"] == "design")
        row["independent_sha256"] = row["artifact_sha256"]
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=spine))

    def test_pre_rejects_exact_head_claim_bound_to_another_head(self):
        spine = self.spine()
        next(r for r in spine["claims"] if r["claim_id"] == "green-proof")["exact_head_sha"] = "f" * 40
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=spine))

    def test_pre_rejects_wrong_evidence_spine_policy(self):
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=self.spine(policy_sha256="f" * 64)))

    def test_pre_rejects_stale_evidence_spine_head(self):
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(spine=self.spine(head_sha="f" * 40)))

    def test_pre_rejects_manifest_hash_not_matching_spine(self):
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(run_manifest_sha256="f" * 64))

    def test_pre_rejects_main_moving_after_evidence(self):
        with self.assertRaises(SystemExit):
            self.authorize(current_base="f" * 40)

    def test_pre_rejects_pr_base_oid_moving_after_evidence(self):
        with self.assertRaises(SystemExit):
            self.authorize(meta=self.meta(baseRefOid="f" * 40))

    def test_pre_rejects_pr_head_moving_after_evidence(self):
        with self.assertRaises(SystemExit):
            self.authorize(local_head="f" * 40)

    def test_pre_rejects_wrong_target_branch(self):
        with self.assertRaises(SystemExit):
            self.authorize(meta=self.meta(baseRefName="release"))

    def test_pre_rejects_malformed_head_tree_oid(self):
        with self.assertRaises(SystemExit):
            self.authorize(head_tree="not-an-oid")

    def test_pre_rejects_wrong_evidence_bundle_version(self):
        with self.assertRaises(SystemExit):
            self.authorize(bundle=self.bundle(version="4.0"))

    def test_pre_requires_base_to_be_head_ancestor(self):
        with self.assertRaises(SystemExit):
            self.authorize(base_is_ancestor=False)

    def test_post_verifies_squash_parent_and_tree(self):
        result = self.post()
        self.assertEqual(result["verdict"], "verified")
        self.assertEqual(result["merge_sha"], MERGE)

    def test_post_still_passes_when_later_commit_is_on_main(self):
        self.assertEqual(self.post(merge_is_ancestor=True)["verdict"], "verified")

    def test_post_rejects_merge_missing_from_main_history(self):
        with self.assertRaises(SystemExit):
            self.post(merge_is_ancestor=False)

    def test_post_rejects_tampered_authorization(self):
        auth = self.authorize()
        auth["head_sha"] = "f" * 40
        with self.assertRaises(SystemExit):
            self.post(authorization=auth)

    def test_post_rejects_tampered_authorized_tree(self):
        auth = self.authorize()
        auth["head_tree_sha"] = "f" * 40
        with self.assertRaises(SystemExit):
            self.post(authorization=auth)

    def test_post_rejects_wrong_squash_parent(self):
        with self.assertRaises(SystemExit):
            self.post(merge_parents=["f" * 40])

    def test_post_rejects_merge_commit_with_two_parents(self):
        with self.assertRaises(SystemExit):
            self.post(merge_parents=[BASE, "f" * 40])

    def test_post_rejects_tree_not_identical_to_authorized_head(self):
        with self.assertRaises(SystemExit):
            self.post(merge_tree="f" * 40)

    def test_post_rejects_github_reporting_different_merge_sha(self):
        with self.assertRaises(SystemExit):
            self.post(meta=self.meta(mergeCommit={"oid": "f" * 40}))

    def test_post_rejects_unmerged_pr(self):
        with self.assertRaises(SystemExit):
            self.post(meta=self.meta(mergedAt=None))


if __name__ == "__main__":
    unittest.main()
