"""The trusted project profile: exact first-profile values, strict loading, exact destination.

WP01 acceptance: the configured profile keeps every value the legacy constants had; a wrong
repository, numeric identity, visibility or default branch refuses; a disposable second
profile loads offline without touching DynaChat's governance files; and the legacy constants
are wrappers over the profile, not a second copy of it.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel import publication_policy as policy
from factory_kernel.project_profile import (
    DEFAULT_PROFILE_PATH, FIELDS, ProfileRefused, ProjectProfile, current_profile, load_profile,
    verify_destination,
)

ROOT = Path(__file__).resolve().parents[2]
OBSERVATION = {"id": 1341036238, "full_name": "ShaishiBear/dark-factory-2.0", "owner": {"login": "ShaishiBear"},
               "private": False, "default_branch": "main"}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(DEFAULT_PROFILE_PATH)
        self.raw = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))

    def test_first_profile_keeps_the_legacy_constants_exactly(self):
        self.assertEqual(self.profile.repository, "ShaishiBear/dark-factory-2.0")
        self.assertEqual(self.profile.owner, "ShaishiBear")
        self.assertEqual(self.profile.project, "citation-inspection")
        self.assertEqual(self.profile.publication_origin, "https://108.131.113.253")
        self.assertEqual(self.profile.app_login, "shaishibear-dark-factory[bot]")
        self.assertEqual(self.profile.publication_workflow, "dark-factory-programme-publish.yml")
        self.assertEqual(self.profile.publication_workflow_path, ".github/workflows/dark-factory-programme-publish.yml")
        self.assertEqual(self.profile.publication_artifact, "programme-publication-validated")
        self.assertEqual(self.profile.programme_branch_prefix, "factory/programme-")
        self.assertEqual(self.profile.default_branch, "main")
        self.assertEqual(self.profile.repository_id, 1341036238)
        self.assertEqual(self.profile.visibility, "public")
        self.assertIs(current_profile(), current_profile(), "loaded once")
        self.assertEqual(self.profile.to_dict(), self.raw, "the file is the profile, byte for byte in meaning")

    def test_legacy_constants_are_wrappers_over_the_profile(self):
        source = (ROOT / "factory_kernel" / "publication_policy.py").read_text(encoding="utf-8")
        self.assertIn("current_profile()", source)
        for name in ("REPOSITORY", "OWNER", "PROJECT", "ORIGIN", "APP_LOGIN", "WORKFLOW", "WORKFLOW_PATH",
                     "ARTIFACT", "BRANCH_PREFIX"):
            self.assertNotRegex(source, rf'(?m)^{name} = "', f"{name} must not be a second literal copy")
        self.assertEqual((policy.REPOSITORY, policy.OWNER, policy.PROJECT, policy.ORIGIN, policy.APP_LOGIN),
                         (self.profile.repository, self.profile.owner, self.profile.project,
                          self.profile.publication_origin, self.profile.app_login))
        self.assertEqual((policy.WORKFLOW, policy.WORKFLOW_PATH, policy.ARTIFACT, policy.BRANCH_PREFIX),
                         (self.profile.publication_workflow, self.profile.publication_workflow_path,
                          self.profile.publication_artifact, self.profile.programme_branch_prefix))

    @unittest.skipUnless((ROOT / "MISSION.md").exists(), "repo-shaped copy without the product tree (mutation runner)")
    def test_mission_binding_and_product_roots_name_real_inherited_files(self):
        for rel in (*self.profile.mission_binding.values(), *self.profile.validation_adapters):
            self.assertTrue((ROOT / rel).is_file(), rel)
        for root in self.profile.product_roots:
            self.assertTrue((ROOT / root).is_dir(), root)

    def test_destination_is_verified_by_numeric_identity_visibility_and_branch(self):
        self.assertEqual(verify_destination(self.profile, OBSERVATION)["verified"], True)
        self.assertEqual(verify_destination(self.profile, {**OBSERVATION, "owner": "ShaishiBear"})["repository_id"], 1341036238)
        for change in ({"id": 1341036239}, {"id": "1341036238"}, {"id": True}, {"full_name": "ShaishiBear/dark-factory-2.1"},
                       {"owner": {"login": "someone"}}, {"private": True}, {"private": "false"}, {"default_branch": "master"}):
            with self.subTest(change=change), self.assertRaises(ProfileRefused):
                verify_destination(self.profile, {**OBSERVATION, **change})
        with self.assertRaises(ProfileRefused):
            verify_destination(self.profile, "not a mapping")

    def test_strict_loading_refuses_unknown_keys_wrong_versions_and_bad_shapes(self):
        cases = (
            {"extra": 1}, {"schema_version": "2.0"}, {"schema": "dark-factory/profile"}, {"repository_id": True},
            {"repository_id": "1341036238"}, {"repository_id": 0}, {"visibility": "internal"},
            {"publication_origin": "http://example"}, {"publication_origin": "https://example/"},
            {"owner": "other"}, {"project": "Bad Project"}, {"app_login": "not-a-bot"},
            {"product_roots": []}, {"product_roots": ["../x"]}, {"product_roots": ["/etc"]},
            {"mission_binding": {"mission": "MISSION.md"}}, {"default_branch": "bad branch"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ProfileRefused):
                load_profile({**self.raw, **overrides})
        missing = dict(self.raw)
        del missing["project"]
        with self.assertRaises(ProfileRefused):
            load_profile(missing)
        self.assertEqual(set(self.raw), FIELDS)

    def test_a_disposable_second_profile_loads_offline_without_touching_governance(self):
        second = {**self.raw, "repository": "example/other-product", "repository_id": 42, "owner": "example",
                  "project": "other-product", "visibility": "private",
                  "publication_origin": "https://factory.example", "app_login": "example-factory[bot]",
                  "product_roots": ["src/"], "mission_binding": {"mission": "docs/MISSION-other.md",
                                                                  "product_requirements": "docs/other.prd.md"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project-profile.json"
            path.write_text(json.dumps(second), encoding="utf-8")
            profile = load_profile(path)
            self.assertIsInstance(profile, ProjectProfile)
            self.assertEqual((profile.repository, profile.repository_id, profile.project), ("example/other-product", 42, "other-product"))
            observation = {"id": 42, "full_name": "example/other-product", "owner": {"login": "example"},
                           "private": True, "default_branch": "main"}
            self.assertTrue(verify_destination(profile, observation)["verified"])
            with self.assertRaises(ProfileRefused):
                verify_destination(profile, OBSERVATION)
            with self.assertRaises(ProfileRefused):
                verify_destination(self.profile, observation)
            # Selecting it is trusted configuration, not a request: the current profile is unchanged.
            self.assertEqual(current_profile().repository, "ShaishiBear/dark-factory-2.0")
            self.assertEqual(current_profile(path).repository, "example/other-product")
        self.assertEqual(json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8")), self.raw)
        if (ROOT / "MISSION.md").exists():  # absent only in the repo-shaped mutation copy
            self.assertTrue((ROOT / "MISSION.md").is_file())

    def test_authority_closure_including_the_profile_fits_its_reader_bound_with_headroom(self):
        """The host compares every closure file with protected main under one selection bound
        (exploration_repository.SELECTION_BOUND). The profile module and file are in that
        closure now; this pins the measured size so growth is seen here, not as a host refusal."""
        from factory_kernel.execution_authority import POLICY_FILES, PROGRAMS
        from factory_kernel.exploration_repository import SELECTION_BOUND

        self.assertIn("project_profile.py", PROGRAMS)
        self.assertEqual(POLICY_FILES, (".factory/project-profile.json",))
        size = sum((ROOT / "factory_kernel" / name).stat().st_size for name in PROGRAMS)
        size += sum((ROOT / path).stat().st_size for path in POLICY_FILES)
        self.assertLessEqual(size, SELECTION_BOUND - 50000, f"closure is {size} bytes; leave 50 KB of headroom")
        self.assertLessEqual(max((ROOT / "factory_kernel" / name).stat().st_size for name in PROGRAMS), 100000,
                             "per-file read limit of the closure reader")

    def test_duplicate_keys_symlinks_and_oversized_files_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text('{"schema": "x", "schema": "y"}', encoding="utf-8")
            with self.assertRaises(ProfileRefused):
                load_profile(path)
            path.write_text("{" + " " * 20001 + "}", encoding="utf-8")
            with self.assertRaises(ProfileRefused):
                load_profile(path)
            with self.assertRaises(ProfileRefused):
                load_profile(Path(tmp) / "absent.json")


if __name__ == "__main__":
    unittest.main()
