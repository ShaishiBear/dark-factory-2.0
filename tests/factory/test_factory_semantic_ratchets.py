import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "scripts" / "factory_evidence.py"
spec = importlib.util.spec_from_file_location("factory_evidence_ratchets", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SemanticRatchetTests(unittest.TestCase):
    def floors(self, **overrides):
        value = {
            "static_checks": 5,
            "unit_tests": 549,
            "holdout_assertions": 9,
            "mutations_total": 9,
            "mutations_independent_caught": 3,
            "mutations_security_caught": 3,
        }
        value.update(overrides)
        return value

    def log(self, **overrides):
        value = {
            "static": 5,
            "unit": 549,
            "e2e": 5,
            "holdout": 9,
            "total": 9,
            "caught": 9,
            "independent": 3,
            "security": 3,
            "not_injected": 0,
        }
        value.update(overrides)
        return (
            f"STATIC_OK checks={value['static']}\n"
            f"UNIT_PASSED tests={value['unit']}\n"
            f"E2E_PASSED steps={value['e2e']}\n"
            f"HOLDOUT_PASSED scenarios=3 assertions={value['holdout']}\n"
            f"MUTATIONS_TOTAL={value['total']}\n"
            f"MUTATIONS_CAUGHT={value['caught']}\n"
            f"MUTATIONS_INDEPENDENT_CAUGHT={value['independent']}\n"
            f"MUTATIONS_SECURITY_CAUGHT={value['security']}\n"
            f"MUTATIONS_NOT_INJECTED={value['not_injected']}\n"
            "GATE_OK mode=full\n"
        )

    def test_exact_structural_floor_passes(self):
        result = m.parse_harness(self.log(), self.floors())
        self.assertEqual(result["mutations_total"], 9)
        self.assertEqual(result["mutations_security_caught"], 3)

    def test_old_four_mutation_baseline_is_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_harness(self.log(total=4, caught=4), self.floors())

    def test_security_channel_below_floor_is_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_harness(self.log(security=2), self.floors())

    def test_independent_channel_below_floor_is_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_harness(self.log(independent=2), self.floors())

    def test_missing_security_marker_is_rejected_when_floor_exists(self):
        text = self.log().replace("MUTATIONS_SECURITY_CAUGHT=3\n", "")
        with self.assertRaises(SystemExit):
            m.parse_harness(text, self.floors())

    def test_missing_independent_marker_is_rejected_when_floor_exists(self):
        text = self.log().replace("MUTATIONS_INDEPENDENT_CAUGHT=3\n", "")
        with self.assertRaises(SystemExit):
            m.parse_harness(text, self.floors())

    def test_legacy_floor_fixture_does_not_require_new_markers(self):
        legacy = {
            "static_checks": 5,
            "unit_tests": 549,
            "holdout_assertions": 9,
            "mutations_total": 4,
        }
        text = self.log(total=4, caught=4)
        text = text.replace("MUTATIONS_INDEPENDENT_CAUGHT=3\n", "")
        text = text.replace("MUTATIONS_SECURITY_CAUGHT=3\n", "")
        self.assertEqual(m.parse_harness(text, legacy)["mutations_total"], 4)


if __name__ == "__main__":
    unittest.main()
