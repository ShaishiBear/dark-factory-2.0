import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "scripts" / "factory_architecture.py"
spec = importlib.util.spec_from_file_location("factory_architecture", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ArchitectureTests(unittest.TestCase):
    def policy(self):
        return {
            "version": "1.0",
            "principles": [
                {"id": "ARCH-BACKEND", "scope": ["app/backend"], "rule": "Keep backend seams stable."},
                {"id": "ARCH-FRONTEND", "scope": ["app/frontend/src"], "rule": "Keep frontend seams stable."},
            ],
            "layers": [
                {"id": "backend-db", "paths": ["app/backend/db"], "allowed_imports": []},
                {"id": "frontend", "paths": ["app/frontend/src"], "allowed_imports": []},
            ],
            "graph": {
                "enforce_new_forbidden_edges": True,
                "enforce_new_cycles": True,
                "enforce_no_growth_debt": True,
            },
            "migrations": [
                {"id": "MIG-REPO", "active": True, "paths": ["app/backend/db/repository.py"],
                 "direction": "Move capability persistence to focused repositories."},
            ],
            "debt": [
                {"id": "DEBT-REPO", "paths": ["app/backend/db/repository.py"], "mode": "no-growth",
                 "note": "Do not add unrelated persistence behavior."},
            ],
        }

    def contract(self):
        return {
            "version": "2.0",
            "issue": {"number": 42, "title": "Architectural change"},
            "summary": "Change behavior while preserving long-horizon architecture.",
            "behaviors": [{"id": "AC-1", "given": "a request", "when": "processed",
                           "then": "the result is correct", "seam": "repository seam"}],
            "invariants": ["preserve behavior"], "out_of_scope": [], "risks": [], "ambiguities": [],
        }

    def context(self, files=None):
        c = self.contract()
        return {
            "version": "1.0", "contract_sha256": m.validate_contract(c),
            "files": files or ["app/backend/db/repository.py"],
            "symbols": [], "callers": [], "tests": [], "invariants": [], "adrs": [], "history": [],
        }

    def design(self, context=None):
        c = self.contract(); context = context or self.context()
        return {
            "version": "1.0", "contract_sha256": m.validate_contract(c),
            "context_sha256": m.digest(context), "modules": ["focused repository"],
            "seams": ["repository seam"], "public_interfaces": [],
            "invariants": ["preserve behavior"], "data_flows": ["route -> repository"],
            "ac_mapping": {"AC-1": ["repository seam"]},
            "planned_files": ["app/backend/db/repository.py"],
            "allowed_new_files": [],
        }

    def raw(self, **overrides):
        value = {
            "version": "1.0", "decision": "proceed", "convergence": "improves",
            "principles": ["ARCH-BACKEND"], "migrations": ["MIG-REPO"], "debts": ["DEBT-REPO"],
            "rationale": ["The design reduces concentration at the touched persistence hotspot."],
            "required_changes": [],
        }
        value.update(overrides)
        return value

    def compile(self, raw=None, context=None):
        context = context or self.context()
        return m.compile_value(self.policy(), raw or self.raw(), self.contract(), context, self.design(context))

    def conformance_raw(self, **overrides):
        value = {
            "version": "1.0", "verdict": "conform", "convergence": "improves",
            "principles": ["ARCH-BACKEND"], "migrations": ["MIG-REPO"], "debts": ["DEBT-REPO"],
            "rationale": ["The finished diff keeps persistence behind the governed repository seam."],
            "findings": [],
        }
        value.update(overrides)
        return value

    def conformance(self, raw=None, governor=None, files=None):
        context = self.context()
        return m.compile_conformance_value(
            self.policy(), raw or self.conformance_raw(), self.contract(), context, self.design(context),
            governor or self.compile(),
            head_sha="abcdef1234567890", changed_files=files or ["app/backend/db/repository.py"],
            diff_sha256="0" * 64,
        )

    def test_good_governor_is_bound_to_policy_and_design(self):
        result = self.compile()
        self.assertEqual(result["decision"], "proceed")
        self.assertEqual(result["migrations"], ["MIG-REPO"])
        self.assertEqual(result["design_sha256"], m.digest(self.design(self.context())))

    def test_governor_includes_planned_target_files(self):
        result = self.compile()
        self.assertIn("app/backend/db/repository.py", result["source_files"])

    def test_missing_touched_migration_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.compile(self.raw(migrations=[]))

    def test_regressing_design_cannot_proceed(self):
        with self.assertRaises(SystemExit):
            self.compile(self.raw(convergence="regresses"))

    def test_prefactor_requires_concrete_change(self):
        with self.assertRaises(SystemExit):
            self.compile(self.raw(decision="prefactor", convergence="neutral", required_changes=[]))

    def test_scope_cannot_ignore_architecture_veto(self):
        governor = self.compile(self.raw(decision="prefactor", convergence="neutral",
                                         required_changes=["Extract focused repository first."]))
        with self.assertRaises(SystemExit):
            m.enforce_scope_value(governor, "implement")
        m.enforce_scope_value(governor, "decompose")

    def test_scope_may_decompose_more_conservatively(self):
        m.enforce_scope_value(self.compile(), "decompose")

    def test_unrelated_frontend_policy_is_not_required(self):
        result = self.compile()
        self.assertEqual(result["principles"], ["ARCH-BACKEND"])
        self.assertNotIn("ARCH-FRONTEND", result["principles"])

    def test_finished_implementation_is_bound_to_governor_and_diff(self):
        result = self.conformance()
        self.assertEqual(result["verdict"], "conform")
        self.assertEqual(result["governor_sha256"], m.digest(self.compile()))
        self.assertEqual(result["diff_sha256"], "0" * 64)
        self.assertEqual(result["head_sha"], "abcdef1234567890")

    def test_conformance_recomputes_touched_migrations(self):
        with self.assertRaises(SystemExit):
            self.conformance(self.conformance_raw(migrations=[]))

    def test_regressing_implementation_cannot_conform(self):
        with self.assertRaises(SystemExit):
            self.conformance(self.conformance_raw(convergence="regresses"))

    def test_deviation_requires_findings(self):
        with self.assertRaises(SystemExit):
            self.conformance(self.conformance_raw(verdict="deviates", convergence="neutral", findings=[]))

    def test_conformance_refuses_code_after_architecture_veto(self):
        governor = self.compile(self.raw(
            decision="prefactor", convergence="neutral", required_changes=["Extract repository first."]
        ))
        with self.assertRaises(SystemExit):
            self.conformance(governor=governor)

    def test_conforming_result_cannot_hide_findings(self):
        with self.assertRaises(SystemExit):
            self.conformance(self.conformance_raw(findings=["A hidden architectural deviation."]))


if __name__ == "__main__":
    unittest.main()
