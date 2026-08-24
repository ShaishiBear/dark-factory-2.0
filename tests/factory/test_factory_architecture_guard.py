import importlib.util
import unittest
from pathlib import Path

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
            ]
        }

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


if __name__ == "__main__":
    unittest.main()
