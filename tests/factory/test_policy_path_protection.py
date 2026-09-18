"""The factory may not edit the policy that says what the factory may do.

Two kernel policy files decide the factory's authority over itself. `.factory/
maintenance-policy.json` names the maintenance lanes that may run without a maintainer --
today an empty list -- and `.factory/lesson-policy.json` will say what evidence admits a
learned method into the packet a worker reads. Until 2026-09-18 the deterministic security
guard did not protect either one, and `factory_kernel/maintenance.py` classified both as
`product`: an autonomous pull request could have added itself a lane or lowered its own
admission threshold, and no check would have objected.

No bypass was observed. No lane is active, and the maintenance classifier's inferred `policy`
effect already raised the tier of a change that declared one, so the gap was reachable rather
than exercised. That is a reason to close it, not a reason to call it closed.

The rule is the filename shape, `.factory/<name>-policy.json`, so a policy file added by a
later work package is protected on the day it is added. These tests pin: both sides agree,
the guard refuses an autonomous PR that touches one, the maintainer lane still may, the
maintenance classifier puts such a change on the maintainer lane with an approval required,
and no autonomous lane is opened by any of it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from factory_kernel import maintenance  # noqa: E402

_spec = importlib.util.spec_from_file_location("policy_guard", ROOT / "scripts" / "factory_security.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

POLICY_PATHS = (".factory/maintenance-policy.json", ".factory/lesson-policy.json")
EXAMPLE = ROOT / "tests" / "factory" / "fixtures" / "policy" / "lesson-policy.example.json"


class ProtectedSetTests(unittest.TestCase):
    def test_both_kernel_policy_files_are_protected_paths(self):
        for path in POLICY_PATHS:
            with self.subTest(path):
                self.assertTrue(guard.protected_path(path))
                self.assertEqual(maintenance.path_tier(path), "trust-root-authority")

    def test_the_rule_is_the_policy_suffix_and_not_every_kernel_json(self):
        self.assertTrue(guard.protected_path(".factory/worker-image-policy.json"),
                        "a policy added later is protected by its name, not by an edit here")
        self.assertFalse(guard.protected_path(".factory/experiment-results.json"))
        self.assertFalse(guard.protected_path("docs/lesson-policy.json"),
                         "the rule is about the kernel's directory, not the word policy")
        self.assertFalse(guard.protected_path(".factory/nested/dir-policy.json"))

    def test_the_guard_and_the_classifier_never_disagree_about_a_policy_path(self):
        for path in (".factory/maintenance-policy.json", ".factory/lesson-policy.json",
                     ".factory/broker-policy.json", ".factory/experiment-results.json",
                     ".factory/kernel.json", "docs/lesson-policy.json"):
            with self.subTest(path):
                self.assertEqual(maintenance.path_tier(path) != "product",
                                 guard.protected_path(path))


class LaneTests(unittest.TestCase):
    """The factory lane is refused; the maintainer lane is not waived, only allowed."""

    def setUp(self):
        self.policy = maintenance.load_policy(ROOT / ".factory" / "maintenance-policy.json")

    def test_a_policy_change_is_a_trust_change_on_the_maintainer_lane(self):
        verdict = maintenance.classify_change([".factory/lesson-policy.json"], policy=self.policy)
        self.assertEqual(verdict["tier"], "trust-root-authority")
        self.assertEqual(verdict["lane"], "maintainer-lane")
        self.assertTrue(verdict["acp_required"])
        self.assertEqual(verdict["activation"], "refused",
                         "protecting a policy file does not open a lane that may edit it")

    def test_a_declared_product_tier_cannot_lower_a_policy_change(self):
        verdict = maintenance.classify_change([".factory/maintenance-policy.json"],
                                              policy=self.policy, declared_tier="product")
        self.assertEqual(verdict["tier"], "trust-root-authority")
        self.assertTrue(verdict["downgrade_attempted"])

    def test_the_maintenance_policy_still_activates_no_autonomous_lane(self):
        raw = json.loads((ROOT / ".factory" / "maintenance-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(raw.get("autonomous_lanes"), [],
                         "this change strengthens protection; it does not enable maintenance")


class ExampleLessonPolicyTests(unittest.TestCase):
    """The example is a fixture. The real file's absence is the current admission answer."""

    def test_the_real_lesson_policy_is_deliberately_absent(self):
        self.assertFalse((ROOT / ".factory" / "lesson-policy.json").exists(),
                         "an installed lesson policy would be an admission threshold nobody registered")

    def test_the_example_declares_itself_uninstalled_and_names_real_thresholds_nowhere_else(self):
        record = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], "dark-factory/lesson-policy")
        self.assertFalse(record["installed"])
        self.assertEqual(record["admission"]["maximum_confirmation_exposures"], 1)
        self.assertTrue(record["admission"]["requires_independent_study"])
        self.assertTrue(record["retrieval"]["blind_roles_receive_nothing"])

    def test_the_example_lives_under_fixtures_so_it_is_not_a_kernel_policy(self):
        relative = EXAMPLE.relative_to(ROOT).as_posix()
        self.assertTrue(relative.startswith("tests/factory/fixtures/"))
        self.assertFalse(guard.protected_path(relative) and not relative.startswith("tests/factory/"),
                         "the example is protected as a detector fixture, not as a kernel policy")
        self.assertEqual(maintenance.path_tier(relative), "trust-root-authority",
                         "it is under tests/factory, which is trust root for a different reason")


if __name__ == "__main__":
    unittest.main()
