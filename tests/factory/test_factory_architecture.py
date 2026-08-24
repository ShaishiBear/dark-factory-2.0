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

    def test_good_governor_is_bound_to_policy_and_design(self):
        result = self.compile()
        self.assertEqual(result["decision"], "proceed")
        self.assertEqual(result["migrations"], ["MIG-REPO"])
        self.assertEqual(result["design_sha256"], m.digest(self.design(self.context())))

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


if __name__ == "__main__":
    unittest.main()
