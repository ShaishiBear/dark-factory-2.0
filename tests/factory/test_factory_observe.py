import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "harness" / "observe.py"
spec = importlib.util.spec_from_file_location("factory_observe", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ObservationTests(unittest.TestCase):
    def log(self, **overrides):
        value = {
            "static": 5, "unit": 549, "e2e": 7, "holdout": 9,
            "mutations_total": 9, "mutations_caught": 9,
            "independent": 3, "security": 3, "not_injected": 0,
            "factory_total": 16, "factory_caught": 16, "factory_not_injected": 0,
            "immunity_entries": 2, "immunity_assertions": 5,
        }
        value.update(overrides)
        return (
            "APP_STARTED port=15123\n"
            f"STATIC_OK checks={value['static']}\n"
            f"UNIT_PASSED tests={value['unit']}\n"
            f"E2E_PASSED steps={value['e2e']}\n"
            f"HOLDOUT_PASSED scenarios=3 assertions={value['holdout']}\n"
            f"MUTATIONS_TOTAL={value['mutations_total']}\n"
            f"MUTATIONS_CAUGHT={value['mutations_caught']}\n"
            f"MUTATIONS_INDEPENDENT_CAUGHT={value['independent']}\n"
            f"MUTATIONS_SECURITY_CAUGHT={value['security']}\n"
            f"MUTATIONS_NOT_INJECTED={value['not_injected']}\n"
            f"IMMUNITY_OK entries={value['immunity_entries']} assertions={value['immunity_assertions']} sha256={'a' * 64}\n"
            f"FACTORY_MUTATIONS_TOTAL={value['factory_total']}\n"
            f"FACTORY_MUTATIONS_CAUGHT={value['factory_caught']}\n"
            f"FACTORY_MUTATIONS_NOT_INJECTED={value['factory_not_injected']}\n"
            "FACTORY_MUTATIONS_OK\nMUTATIONS_OK\nGATE_OK mode=full\n"
        )

    def test_complete_observation_parses(self):
        result = m.parse_transcript(self.log())
        self.assertEqual(result["e2e_steps"], 7)
        self.assertEqual(result["factory_mutations_total"], 16)
        self.assertEqual(result["immunity_entries"], 2)

    def test_missing_full_gate_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_transcript(self.log().replace("GATE_OK mode=full\n", ""))

    def test_missing_app_started_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_transcript(self.log().replace("APP_STARTED port=15123\n", ""))

    def test_zero_e2e_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_transcript(self.log(e2e=0))

    def test_application_mutation_escape_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_transcript(self.log(mutations_caught=8))

    def test_application_not_injected_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_transcript(self.log(not_injected=1))

    def test_factory_mutation_escape_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_transcript(self.log(factory_caught=15))

    def test_factory_not_injected_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_transcript(self.log(factory_not_injected=1))

    def test_missing_security_channel_marker_rejected(self):
        text = self.log().replace("MUTATIONS_SECURITY_CAUGHT=3\n", "")
        with self.assertRaises(SystemExit):
            m.parse_transcript(text)

    def test_missing_immunity_marker_rejected(self):
        text = self.log().replace(
            f"IMMUNITY_OK entries=2 assertions=5 sha256={'a' * 64}\n", ""
        )
        with self.assertRaises(SystemExit):
            m.parse_transcript(text)

    def test_malformed_immunity_hash_rejected(self):
        text = self.log().replace("sha256=" + "a" * 64, "sha256=not-a-hash")
        with self.assertRaises(SystemExit):
            m.parse_transcript(text)


if __name__ == "__main__":
    unittest.main()
