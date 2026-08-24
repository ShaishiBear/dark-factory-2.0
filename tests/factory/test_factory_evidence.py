import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "scripts" / "factory_evidence.py"
spec = importlib.util.spec_from_file_location("factory_evidence", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


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

    def contract(self, **overrides):
        value = {
            "version": "2.0",
            "issue": {"number": 42, "title": "Exact evidence"},
            "summary": "Prove the evidence chain independently.",
            "behaviors": [{"id": "AC-1", "given": "a PR", "when": "validated",
                           "then": "proof is replayed", "seam": "merge gate"}],
            "invariants": ["fail closed"], "out_of_scope": [], "risks": [],
            "ambiguities": [],
        }
        value.update(overrides)
        return value

    def proof(self, head="abc"):
        return {
            "version": "1.0", "test_commit": "def", "cwd": ".", "argv": ["python", "-V"],
            "files": {"tests/test_x.py": "0" * 64}, "red_exit": 1,
            "red_output_sha256": "1" * 64, "expected_failure": "expected behavior",
            "green_commit": head, "green_exit": 0,
        }

    def assert_rejects_harness(self, text):
        with self.assertRaises(SystemExit):
            m.parse_harness(text, self.floors())

    def test_good_full_harness(self):
        self.assertEqual(m.parse_harness(self.log(), self.floors())["mutations_caught"], 4)

    def test_missing_full_marker_rejected(self):
        self.assert_rejects_harness(self.log().replace("GATE_OK mode=full\n", ""))

    def test_zero_e2e_rejected(self):
        self.assert_rejects_harness(self.log(e2e=0))

    def test_holdout_regression_rejected(self):
        self.assert_rejects_harness(self.log(holdout=8))

    def test_escaped_mutation_rejected(self):
        self.assert_rejects_harness(self.log(caught=3))

    def test_not_injected_mutation_rejected(self):
        self.assert_rejects_harness(self.log(not_injected=1))

    def test_trust_root_detection(self):
        self.assertEqual(m.trust_root_touched(["app/x.py", "harness/ci.py"]), ["harness/ci.py"])

    def test_contract_is_revalidated(self):
        contract = self.contract()
        expected = m.digest(contract)
        self.assertEqual(m.verify_contract(contract, expected, 42)["criteria"], 1)

    def test_contract_with_ambiguity_is_rejected(self):
        contract = self.contract(ambiguities=["what does done mean?"])
        with self.assertRaises(SystemExit):
            m.verify_contract(contract, m.digest(contract), 42)

    def test_final_green_must_equal_current_head(self):
        with self.assertRaises(SystemExit):
            m.validate_proof_fields(self.proof(head="old"), "new")

    def test_red_replay_must_be_red(self):
        with self.assertRaises(SystemExit):
            m.validate_red_result(0, "expected behavior", "expected behavior")

    def test_red_replay_must_fail_for_declared_reason(self):
        with self.assertRaises(SystemExit):
            m.validate_red_result(1, "some unrelated traceback", "expected behavior")

    def test_red_replay_accepts_declared_failure(self):
        m.validate_red_result(1, "AssertionError: EXPECTED BEHAVIOR missing", "expected behavior")


if __name__ == "__main__":
    unittest.main()
