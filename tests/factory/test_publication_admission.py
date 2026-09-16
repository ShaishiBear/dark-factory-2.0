"""Only independently observed, exact-data App publications enter the narrow lane."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.programme import ACTIVE_PATH, compile_programme
from factory_kernel.publication_admission import admit_publication
from factory_kernel import publication_policy as policy
from tests.factory.test_frontdoor_intent import example_spec


def publication_facts():
    base, head, request = "a" * 40, "b" * 40, "c" * 32
    spec = example_spec()
    spec["repository"] = policy.REPOSITORY
    value = {"version": "1.0", "spec": spec, "app_login": policy.APP_LOGIN,
             "proposal": {"spec_sha256": sha256_value(spec), "items": [
                 {"id": "snippet", "acceptance": ["AC1"], "blocked_by": []}]}}
    app = {"login": policy.APP_LOGIN, "type": "Bot"}
    repository = {"full_name": policy.REPOSITORY, "id": 123}
    manifest = {"schema": "dark-factory/validated-publication", "schema_version": "1.0",
                "repository": policy.REPOSITORY, "project": policy.PROJECT,
                "request_id": request, "request_sha256": "d" * 64, "source_sha": base,
                "run_id": 234, "run_attempt": 1, "input": value, "input_sha256": sha256_value(value),
                "programme_sha256": compile_programme(value, repository=policy.REPOSITORY).sha256}
    return {"manifest": manifest, "base_sha": base, "head_sha": head, "base_input": None,
            "head_input": deepcopy(value), "changed_files": [ACTIVE_PATH], "file_mode": "100644",
            "pr": {"number": 12, "state": "open", "draft": False, "user": deepcopy(app), "commits": 1,
                   "created_at": "2026-09-16T10:03:00Z",
                   "base": {"ref": "main", "sha": base, "repo": deepcopy(repository)},
                   "head": {"ref": policy.BRANCH_PREFIX + request, "sha": head, "repo": deepcopy(repository)}},
            "commits": [{"sha": head, "parents": [{"sha": base}], "author": deepcopy(app), "committer": deepcopy(app)}],
            "run": {"id": 234, "run_attempt": 1, "event": "workflow_dispatch", "path": policy.WORKFLOW_PATH,
                    "head_branch": "main", "head_sha": base, "repository": deepcopy(repository),
                    "head_repository": deepcopy(repository), "status": "in_progress", "conclusion": None,
                    "actor": {"login": policy.OWNER, "type": "User"},
                    "triggering_actor": {"login": policy.OWNER, "type": "User"},
                    "created_at": "2026-09-16T10:00:00Z"},
            "jobs": [{"name": "validate-publication", "run_id": 234, "head_sha": base,
                      "status": "completed", "conclusion": "success", "completed_at": "2026-09-16T10:02:00Z"}],
            "artifact": {"id": 345, "name": policy.ARTIFACT, "expired": False, "size_in_bytes": 500,
                         "digest": "sha256:" + "e" * 64, "created_at": "2026-09-16T10:01:00Z",
                         "expires_at": "2026-09-17T10:01:00Z",
                         "workflow_run": {"id": 234, "head_branch": "main", "head_sha": base,
                                          "repository_id": 123, "head_repository_id": 123}},
            "artifact_sha256": "e" * 64, "now": datetime(2026, 9, 16, 10, 4, tzinfo=timezone.utc)}


class PublicationAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.facts = publication_facts()

    def change(self, path, value):
        facts = deepcopy(self.facts)
        target = facts
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        return facts

    def test_completed_validation_before_app_pr_admits_only_a_separate_data_lane(self):
        result = admit_publication(**self.facts)
        self.assertEqual(result["lane"], "programme-publication")
        self.assertFalse(result["unattended_merge_eligible"])
        self.assertEqual(result["head_sha"], self.facts["head_sha"])
        self.assertEqual(result["manifest_sha256"], sha256_value(self.facts["manifest"]))

    def test_exact_single_regular_data_file_and_initial_publication_only(self):
        changes = ((["changed_files"], [ACTIVE_PATH, "factory_kernel/runtime.py"]),
                   (["changed_files"], []), (["changed_files"], [ACTIVE_PATH, ACTIVE_PATH]),
                   (["file_mode"], "120000"), (["base_input"], self.facts["head_input"]),
                   (["head_input", "spec", "outcome"], "Unapproved outcome"),
                   (["base_sha"], "f" * 40), (["head_sha"], self.facts["base_sha"]))
        for path, value in changes:
            with self.subTest(path=path, value=value), self.assertRaises(IntentRefused):
                admit_publication(**self.change(path, value))

    def test_manifest_cannot_forge_payload_hash_or_scope(self):
        for field in ("request_sha256", "source_sha", "input_sha256", "programme_sha256"):
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                admit_publication(**self.change(["manifest", field], "wrong"))
        for field, value in (("project", "other"), ("repository", "other/repo"), ("run_attempt", 2),
                             ("run_id", True), ("schema_version", "2.0")):
            with self.subTest(field=field), self.assertRaises(IntentRefused):
                admit_publication(**self.change(["manifest", field], value))

    def test_platform_pr_identity_cannot_be_replaced_by_visible_markers(self):
        changes = ((["pr", "user", "login"], "another[bot]"), (["pr", "user", "type"], "User"),
                   (["pr", "head", "ref"], "factory/issue-12"), (["pr", "head", "sha"], "f" * 40),
                   (["pr", "head", "repo", "full_name"], "fork/repo"), (["pr", "base", "ref"], "candidate"),
                   (["pr", "head", "repo", "id"], 456), (["pr", "base", "repo", "id"], 456),
                   (["pr", "base", "sha"], "f" * 40), (["pr", "draft"], True), (["pr", "commits"], 2))
        for path, value in changes:
            with self.subTest(path=path), self.assertRaises(IntentRefused):
                admit_publication(**self.change(path, value))

    def test_complete_single_app_commit_with_one_source_parent_required(self):
        changes = ((["commits"], []), (["commits"], self.facts["commits"] * 2),
                   (["commits", 0, "sha"], "f" * 40), (["commits", 0, "parents"], []),
                   (["commits", 0, "parents"], [{"sha": "f" * 40}]),
                   (["commits", 0, "author", "type"], "User"), (["commits", 0, "committer"], None))
        for path, value in changes:
            with self.subTest(path=path), self.assertRaises(IntentRefused):
                admit_publication(**self.change(path, value))

    def test_candidate_workflow_foreign_actor_rerun_and_wrong_source_refuse(self):
        changes = ((["run", "run_attempt"], 2), (["run", "event"], "pull_request"),
                   (["run", "path"], ".github/workflows/other.yml"), (["run", "head_branch"], "candidate"),
                   (["run", "head_sha"], "f" * 40), (["run", "actor", "type"], "Bot"),
                   (["run", "triggering_actor", "login"], "other"),
                   (["run", "head_repository", "id"], 999), (["run", "repository", "id"], None))
        for path, value in changes:
            with self.subTest(path=path), self.assertRaises(IntentRefused):
                admit_publication(**self.change(path, value))

    def test_overall_workflow_need_not_finish_but_validation_job_must_succeed_first(self):
        self.assertEqual(admit_publication(**self.facts)["source_run_id"], 234)
        changes = ((["jobs"], []), (["jobs"], self.facts["jobs"] * 2),
                   (["jobs", 0, "status"], "in_progress"), (["jobs", 0, "conclusion"], "failure"),
                   (["jobs", 0, "head_sha"], "f" * 40),
                   (["jobs", 0, "completed_at"], "2026-09-16T10:03:30Z"))
        for path, value in changes:
            with self.subTest(path=path), self.assertRaises(IntentRefused):
                admit_publication(**self.change(path, value))
        self.facts["run"].update(status="completed", conclusion="failure")
        with self.assertRaises(IntentRefused):
            admit_publication(**self.facts)

    def test_expired_missing_oversized_wrong_digest_or_foreign_artifact_refuses(self):
        changes = ((["artifact", "expired"], True), (["artifact", "id"], None),
                   (["artifact", "name"], "candidate-evidence"), (["artifact", "size_in_bytes"], 1000001),
                   (["artifact", "digest"], "sha256:" + "f" * 64),
                   (["artifact", "expires_at"], "2026-09-16T10:03:00Z"),
                   (["artifact", "created_at"], "2026-09-16T09:59:00Z"),
                   (["artifact", "workflow_run", "id"], 999),
                   (["artifact", "workflow_run", "head_sha"], "f" * 40),
                   (["artifact", "workflow_run", "head_repository_id"], 999))
        for path, value in changes:
            with self.subTest(path=path), self.assertRaises(IntentRefused):
                admit_publication(**self.change(path, value))

    def test_missing_or_malformed_facts_never_become_an_admission(self):
        for key in self.facts:
            if key == "now":
                continue
            facts = deepcopy(self.facts)
            del facts[key]
            with self.subTest(key=key), self.assertRaises(IntentRefused):
                admit_publication(**facts)


if __name__ == "__main__":
    unittest.main()
