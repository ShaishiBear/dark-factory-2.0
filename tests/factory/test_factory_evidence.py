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
            "behaviors": [
                {"id": "AC-1", "given": "a PR", "when": "validated",
                 "then": "first proof is replayed", "seam": "merge gate"},
                {"id": "AC-2", "given": "a PR", "when": "validated",
                 "then": "second proof is replayed", "seam": "merge gate"},
            ],
            "invariants": ["fail closed"], "out_of_scope": [], "risks": [],
            "ambiguities": [],
        }
        value.update(overrides)
        return value

    def checkpoint(self, ac):
        return {
            "acceptance_id": ac, "seams": ["merge gate"], "cwd": ".",
            "argv": ["python", "-V"], "files": [f"tests/test_{ac.lower()}.py"],
            "expected_failure": f"{ac} expected behavior", "red_exit": 1,
            "red_output_sha256": "1" * 64,
        }

    def proof(self, head="abc"):
        contract = self.contract()
        p = {
            "version": "2.0", "test_commit": "def",
            "contract_sha256": m.digest(contract), "design_sha256": "2" * 64,
            "files": {"tests/test_ac-1.py": "3" * 64, "tests/test_ac-2.py": "4" * 64},
            "checkpoints": [self.checkpoint("AC-1"), self.checkpoint("AC-2")],
            "green_commit": head,
            "green_results": [
                {"acceptance_id": "AC-1", "exit": 0, "output_sha256": "5" * 64},
                {"acceptance_id": "AC-2", "exit": 0, "output_sha256": "6" * 64},
            ],
        }
        p["test_plan_sha256"] = m.digest(m.plan_from_proof(p))
        return p

    def policy(self):
        return {
            "version": "1.0",
            "principles": [
                {"id": "P-BACKEND", "scope": ["app/backend"], "rule": "Keep backend ownership local."},
                {"id": "P-FRONTEND", "scope": ["app/frontend"], "rule": "Keep frontend ownership local."},
            ],
            "migrations": [
                {"id": "M-RAG", "paths": ["app/backend/rag"], "active": True,
                 "direction": "Move RAG dependencies behind one seam."},
                {"id": "M-OLD", "paths": ["app/backend/rag"], "active": False,
                 "direction": "Historical migration."},
            ],
            "debt": [
                {"id": "D-RAG", "paths": ["app/backend/rag"], "mode": "no-growth",
                 "note": "Do not grow this hotspot."},
            ],
        }

    def architecture_proof(self, *, head="abc", files=None, diff="d" * 64):
        p = self.proof(head)
        policy = self.policy()
        files = files or ["app/backend/rag/service.py"]
        arch = {
            "version": "1.0", "policy_sha256": m.digest(policy),
            "contract_sha256": p["contract_sha256"], "context_sha256": "7" * 64,
            "design_sha256": p["design_sha256"], "governor_sha256": "8" * 64,
            "head_sha": head, "diff_sha256": diff, "verdict": "conform",
            "convergence": "neutral", "principles": ["P-BACKEND"],
            "migrations": ["M-RAG"], "debts": ["D-RAG"],
            "rationale": ["matches governed seams"], "findings": [],
            "changed_files": files,
        }
        p["architecture_builder"] = arch
        p["architecture_builder_sha256"] = m.digest(arch)
        return p

    def architecture_holdout(self, **overrides):
        value = {"version": "1.0", "verdict": "pass", "convergence": "neutral",
                 "principles": ["P-BACKEND"], "migrations": ["M-RAG"],
                 "debts": ["D-RAG"], "findings": [], "reasoning": "Independent pass."}
        value.update(overrides)
        return value

    def assert_rejects_harness(self, text):
        with self.assertRaises(SystemExit):
            m.parse_harness(text, self.floors())

    def verify_arch(self, proof=None, **kwargs):
        proof = proof or self.architecture_proof()
        return m.verify_architecture(
            proof, "abc", "base", proof["contract_sha256"], self.policy(),
            files=["app/backend/rag/service.py"], diff_sha256="d" * 64, **kwargs
        )

    def verify_holdout(self, value=None):
        return m.verify_architecture_holdout(
            value or self.architecture_holdout(), ["app/backend/rag/service.py"], self.policy())

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
        touched = m.trust_root_touched(["app/x.py", "harness/ci.py", ".factory/architecture.json"])
        self.assertEqual(touched, ["harness/ci.py", ".factory/architecture.json"])

    def test_contract_is_revalidated(self):
        contract = self.contract()
        expected = m.digest(contract)
        self.assertEqual(m.verify_contract(contract, expected, 42)["criteria"], 2)

    def test_contract_with_ambiguity_is_rejected(self):
        contract = self.contract(ambiguities=["what does done mean?"])
        with self.assertRaises(SystemExit):
            m.verify_contract(contract, m.digest(contract), 42)

    def test_good_v2_proof_matrix(self):
        c = self.contract()
        m.validate_proof_fields(self.proof(), "abc", c, m.digest(c))

    def test_proof_must_cover_every_contract_ac(self):
        c = self.contract()
        p = self.proof()
        p["checkpoints"] = p["checkpoints"][:1]
        p["green_results"] = p["green_results"][:1]
        p["test_plan_sha256"] = m.digest(m.plan_from_proof(p))
        with self.assertRaises(SystemExit):
            m.validate_proof_fields(p, "abc", c, m.digest(c))

    def test_duplicate_ac_is_rejected(self):
        c = self.contract()
        p = self.proof()
        p["checkpoints"][1]["acceptance_id"] = "AC-1"
        p["test_plan_sha256"] = m.digest(m.plan_from_proof(p))
        with self.assertRaises(SystemExit):
            m.validate_proof_fields(p, "abc", c, m.digest(c))

    def test_forged_test_plan_hash_rejected(self):
        c = self.contract()
        p = self.proof()
        p["test_plan_sha256"] = "f" * 64
        with self.assertRaises(SystemExit):
            m.validate_proof_fields(p, "abc", c, m.digest(c))

    def test_final_green_must_equal_current_head(self):
        c = self.contract()
        with self.assertRaises(SystemExit):
            m.validate_proof_fields(self.proof(head="old"), "new", c, m.digest(c))

    def test_green_matrix_must_cover_every_ac(self):
        c = self.contract()
        p = self.proof()
        p["green_results"] = p["green_results"][:1]
        with self.assertRaises(SystemExit):
            m.validate_proof_fields(p, "abc", c, m.digest(c))

    def test_checkpoint_must_have_red_evidence(self):
        c = self.contract()
        p = self.proof()
        p["checkpoints"][0]["red_exit"] = 0
        p["test_plan_sha256"] = m.digest(m.plan_from_proof(p))
        with self.assertRaises(SystemExit):
            m.validate_proof_fields(p, "abc", c, m.digest(c))

    def test_red_replay_must_be_red(self):
        with self.assertRaises(SystemExit):
            m.validate_red_result(0, "AC-1 expected behavior", "AC-1 expected behavior")

    def test_red_replay_must_fail_for_declared_reason(self):
        with self.assertRaises(SystemExit):
            m.validate_red_result(1, "some unrelated traceback", "AC-1 expected behavior")

    def test_red_replay_accepts_declared_failure(self):
        m.validate_red_result(1, "AssertionError: AC-1 EXPECTED BEHAVIOR", "AC-1 expected behavior")

    def test_architecture_exact_current_provenance_passes(self):
        result = self.verify_arch()
        self.assertEqual(result["migrations"], ["M-RAG"])

    def test_architecture_forged_conformance_hash_rejected(self):
        p = self.architecture_proof()
        p["architecture_builder_sha256"] = "f" * 64
        with self.assertRaises(SystemExit):
            self.verify_arch(p)

    def test_architecture_stale_policy_rejected(self):
        p = self.architecture_proof()
        p["architecture_builder"]["policy_sha256"] = "f" * 64
        p["architecture_builder_sha256"] = m.digest(p["architecture_builder"])
        with self.assertRaises(SystemExit):
            self.verify_arch(p)

    def test_architecture_omitted_migration_rejected(self):
        p = self.architecture_proof()
        p["architecture_builder"]["migrations"] = []
        p["architecture_builder_sha256"] = m.digest(p["architecture_builder"])
        with self.assertRaises(SystemExit):
            self.verify_arch(p)

    def test_architecture_omitted_debt_rejected(self):
        p = self.architecture_proof()
        p["architecture_builder"]["debts"] = []
        p["architecture_builder_sha256"] = m.digest(p["architecture_builder"])
        with self.assertRaises(SystemExit):
            self.verify_arch(p)

    def test_architecture_stale_diff_hash_rejected(self):
        p = self.architecture_proof(diff="e" * 64)
        with self.assertRaises(SystemExit):
            self.verify_arch(p)

    def test_architecture_stale_head_rejected(self):
        p = self.architecture_proof(head="old")
        with self.assertRaises(SystemExit):
            self.verify_arch(p)

    def test_architecture_regression_disguised_as_conform_rejected(self):
        p = self.architecture_proof()
        p["architecture_builder"]["convergence"] = "regresses"
        p["architecture_builder_sha256"] = m.digest(p["architecture_builder"])
        with self.assertRaises(SystemExit):
            self.verify_arch(p)

    def test_architecture_holdout_exact_policy_passes(self):
        self.assertEqual(self.verify_holdout()["verdict"], "pass")

    def test_architecture_holdout_nonpass_rejected(self):
        with self.assertRaises(SystemExit):
            self.verify_holdout(self.architecture_holdout(verdict="request_changes"))

    def test_architecture_holdout_omitted_migration_rejected(self):
        with self.assertRaises(SystemExit):
            self.verify_holdout(self.architecture_holdout(migrations=[]))

    def test_architecture_holdout_blocking_finding_rejected(self):
        finding = {"severity": "high", "description": "Wrong dependency direction", "file": "app/backend/rag/service.py"}
        with self.assertRaises(SystemExit):
            self.verify_holdout(self.architecture_holdout(findings=[finding]))


if __name__ == "__main__":
    unittest.main()
