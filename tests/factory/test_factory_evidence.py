import importlib.util, unittest
from pathlib import Path

P = Path(__file__).parents[2] / "scripts" / "factory_evidence.py"
spec = importlib.util.spec_from_file_location("factory_evidence", P)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class EvidenceTests(unittest.TestCase):
    def floors(self):
        return {"static_checks": 5, "unit_tests": 549, "holdout_assertions": 9, "mutations_total": 4}

    def log(self, **overrides):
        v = {"static": 5, "unit": 549, "e2e": 5, "holdout": 9,
             "total": 4, "caught": 4, "not_injected": 0}
        v.update(overrides)
        return (
            f"STATIC_OK checks={v['static']}\nUNIT_PASSED tests={v['unit']}\n"
            f"E2E_PASSED steps={v['e2e']}\nHOLDOUT_PASSED scenarios=3 assertions={v['holdout']}\n"
            f"MUTATIONS_TOTAL={v['total']}\nMUTATIONS_CAUGHT={v['caught']}\n"
            f"MUTATIONS_NOT_INJECTED={v['not_injected']}\nGATE_OK mode=full\n"
        )

    def assert_rejects(self, text):
        with self.assertRaises(SystemExit):
            m.parse_harness(text, self.floors())

    def test_good_full_harness(self):
        self.assertEqual(m.parse_harness(self.log(), self.floors())["mutations_caught"], 4)

    def test_missing_full_marker_rejected(self):
        self.assert_rejects(self.log().replace("GATE_OK mode=full\n", ""))

    def test_zero_e2e_rejected(self):
        self.assert_rejects(self.log(e2e=0))

    def test_holdout_regression_rejected(self):
        self.assert_rejects(self.log(holdout=8))

    def test_escaped_mutation_rejected(self):
        self.assert_rejects(self.log(caught=3))

    def test_not_injected_mutation_rejected(self):
        self.assert_rejects(self.log(not_injected=1))

    def test_trust_root_detection(self):
        self.assertEqual(m.trust_root_touched(["app/x.py", "harness/ci.py"]), ["harness/ci.py"])


if __name__ == "__main__":
    unittest.main()
