import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
P = ROOT / "scripts" / "factory_architecture_guard.py"
spec = importlib.util.spec_from_file_location("factory_architecture_guard", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ArchitectureGuardTests(unittest.TestCase):
    def policy(self):
        return {
            "layers": [
                {"id": "routes", "paths": ["app/backend/routes"], "allowed_imports": ["services"]},
                {"id": "services", "paths": ["app/backend/services"], "allowed_imports": ["db"]},
                {"id": "db", "paths": ["app/backend/db"], "allowed_imports": []},
                {"id": "components", "paths": ["app/frontend/src/components"], "allowed_imports": ["hooks", "lib"]},
                {"id": "hooks", "paths": ["app/frontend/src/hooks"], "allowed_imports": ["lib"]},
                {"id": "lib", "paths": ["app/frontend/src/lib"], "allowed_imports": []},
            ],
            "graph": {
                "enforce_new_forbidden_edges": True,
                "enforce_new_cycles": True,
                "enforce_no_growth_debt": True,
            },
            "debt": [],
        }

    def test_checked_in_architecture_policy_has_valid_layer_graph(self):
        policy = json.loads((ROOT / ".factory/architecture.json").read_text(encoding="utf-8"))
        paths, allowed = m.layer_table(policy)
        self.assertIn("backend-routes", paths)
        self.assertIn("frontend-lib", allowed)
        self.assertTrue(policy["graph"]["enforce_new_forbidden_edges"])
        self.assertTrue(policy["graph"]["enforce_new_cycles"])
        self.assertTrue(policy["graph"]["enforce_no_growth_debt"])

    def test_allowed_dependency_direction_passes(self):
        edges = {
            ("app/backend/routes/messages.py", "app/backend/services/messages.py"),
            ("app/backend/services/messages.py", "app/backend/db/messages.py"),
        }
        self.assertEqual(m.forbidden_edges(self.policy(), edges), [])

    def test_reverse_dependency_is_rejected(self):
        edges = {("app/backend/db/messages.py", "app/backend/routes/messages.py")}
        violations = m.forbidden_edges(self.policy(), edges)
        self.assertEqual(len(violations), 1)
        self.assertIn("db->routes", violations[0])

    def test_new_ui_reverse_dependency_is_rejected(self):
        edges = {("app/frontend/src/lib/api.ts", "app/frontend/src/components/ChatArea.tsx")}
        violations = m.forbidden_edges(self.policy(), edges)
        self.assertEqual(len(violations), 1)
        self.assertIn("lib->components", violations[0])

    def test_cycle_detection_finds_multi_file_cycle(self):
        cycles = m.cycle_sets({("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "a.py")})
        self.assertEqual(cycles, [["a.py", "b.py", "c.py"]])

    def test_design_envelope_rejects_unplanned_product_file(self):
        design = {
            "planned_files": ["app/backend/routes/messages.py"],
            "allowed_new_files": [],
        }
        unplanned, new = m.authorize_files(
            design,
            ["app/backend/routes/messages.py", "app/backend/services/new_service.py"],
            ["app/backend/services/new_service.py"],
        )
        self.assertEqual(unplanned, ["app/backend/services/new_service.py"])
        self.assertEqual(new, ["app/backend/services/new_service.py"])

    def test_explicit_new_file_authorization_passes(self):
        design = {
            "planned_files": [
                "app/backend/routes/messages.py",
                "app/backend/services/new_service.py",
            ],
            "allowed_new_files": ["app/backend/services/new_service.py"],
        }
        unplanned, new = m.authorize_files(
            design,
            ["app/backend/routes/messages.py", "app/backend/services/new_service.py"],
            ["app/backend/services/new_service.py"],
        )
        self.assertEqual(unplanned, [])
        self.assertEqual(new, [])

    def test_tests_do_not_expand_production_envelope(self):
        design = {
            "planned_files": ["app/backend/routes/messages.py"],
            "allowed_new_files": [],
        }
        unplanned, new = m.authorize_files(
            design,
            ["app/backend/routes/messages.py", "app/backend/tests/test_messages.py"],
            [],
        )
        self.assertEqual(unplanned, [])
        self.assertEqual(new, [])

    def test_touched_no_growth_debt_cannot_increase(self):
        policy = self.policy()
        policy["debt"] = [
            {
                "id": "DEBT-REPO",
                "paths": ["app/backend/db/repository.py"],
                "mode": "no-growth",
            }
        ]
        design = {
            "planned_files": ["app/backend/db/repository.py"],
            "allowed_new_files": [],
        }
        with (
            mock.patch.object(m, "graph_edges", side_effect=[set(), set()]),
            mock.patch.object(
                m, "changed_files", return_value=["app/backend/db/repository.py"]
            ),
            mock.patch.object(m, "new_product_files", return_value=[]),
            mock.patch.object(
                m,
                "debt_growth",
                return_value={
                    "DEBT-REPO": {
                        "base_bytes": 100,
                        "head_bytes": 101,
                        "delta_bytes": 1,
                        "touched": True,
                    }
                },
            ),
        ):
            with self.assertRaises(SystemExit):
                m.compute(policy, design, "base", "head")


if __name__ == "__main__":
    unittest.main()
