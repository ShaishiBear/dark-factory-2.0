import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "harness" / "immunity.py"
spec = importlib.util.spec_from_file_location("factory_immunity", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ImmunityTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "harness/mutations").mkdir(parents=True)
        (root / ".factory/locks").mkdir(parents=True)
        (root / "harness/ci.py").write_text(
            "from e2e import run_e2e\nprint(f'E2E_PASSED steps={steps}')\n", encoding="utf-8"
        )
        (root / ".factory/locks/floor.json").write_text(json.dumps({
            "mutations_independent_caught": 3,
            "mutations_security_caught": 3,
        }), encoding="utf-8")
        defects = {"defects": [
            {"id": "a", "must_catch": ["security"]},
            {"id": "b", "must_catch": ["security"]},
            {"id": "c", "must_catch": ["security"]},
        ]}
        (root / "harness/mutations/defects.json").write_text(json.dumps(defects), encoding="utf-8")
        return tmp, root

    def registry(self):
        return {
            "version": "1.0",
            "entries": [
                {
                    "id": "IMM-001", "status": "active",
                    "source": {"kind": "decision", "ref": "D-002"},
                    "failure_class": "split e2e definition", "lesson": "one canonical journey",
                    "assertions": [
                        {"kind": "text_contains", "path": "harness/ci.py", "value": "from e2e import run_e2e"},
                    ],
                },
                {
                    "id": "IMM-002", "status": "active",
                    "source": {"kind": "decision", "ref": "D-003"},
                    "failure_class": "builder-visible-only mutation catches", "lesson": "independent security catches",
                    "assertions": [
                        {"kind": "json_number_min", "path": ".factory/locks/floor.json", "pointer": "/mutations_security_caught", "minimum": 3},
                        {"kind": "json_array_match_min", "path": "harness/mutations/defects.json", "pointer": "/defects", "field": "must_catch", "contains": "security", "minimum": 3},
                    ],
                },
            ],
        }

    def test_active_immunities_pass(self):
        tmp, root = self.make_root()
        try:
            result = m.verify_registry(self.registry(), root)
            self.assertEqual(result["active_entries"], 2)
            self.assertEqual(result["assertions"], 3)
        finally:
            tmp.cleanup()

    def test_duplicate_ids_rejected(self):
        tmp, root = self.make_root()
        try:
            registry = self.registry()
            registry["entries"][1]["id"] = "IMM-001"
            with self.assertRaises(SystemExit):
                m.verify_registry(registry, root)
        finally:
            tmp.cleanup()

    def test_active_entry_without_assertions_rejected(self):
        tmp, root = self.make_root()
        try:
            registry = self.registry()
            registry["entries"][0]["assertions"] = []
            with self.assertRaises(SystemExit):
                m.verify_registry(registry, root)
        finally:
            tmp.cleanup()

    def test_missing_detector_file_rejected(self):
        tmp, root = self.make_root()
        try:
            registry = self.registry()
            registry["entries"][0]["assertions"][0]["path"] = "missing.py"
            with self.assertRaises(SystemExit):
                m.verify_registry(registry, root)
        finally:
            tmp.cleanup()

    def test_path_traversal_rejected(self):
        tmp, root = self.make_root()
        try:
            registry = self.registry()
            registry["entries"][0]["assertions"][0]["path"] = "../outside"
            with self.assertRaises(SystemExit):
                m.verify_registry(registry, root)
        finally:
            tmp.cleanup()

    def test_text_lesson_disappearing_is_rejected(self):
        tmp, root = self.make_root()
        try:
            (root / "harness/ci.py").write_text("print('different')\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                m.verify_registry(self.registry(), root)
        finally:
            tmp.cleanup()

    def test_numeric_floor_regression_rejected(self):
        tmp, root = self.make_root()
        try:
            (root / ".factory/locks/floor.json").write_text(
                json.dumps({"mutations_independent_caught": 3, "mutations_security_caught": 2}), encoding="utf-8"
            )
            with self.assertRaises(SystemExit):
                m.verify_registry(self.registry(), root)
        finally:
            tmp.cleanup()

    def test_security_probe_regression_rejected(self):
        tmp, root = self.make_root()
        try:
            defects = {"defects": [{"must_catch": ["security"]}, {"must_catch": ["security"]}]}
            (root / "harness/mutations/defects.json").write_text(json.dumps(defects), encoding="utf-8")
            with self.assertRaises(SystemExit):
                m.verify_registry(self.registry(), root)
        finally:
            tmp.cleanup()

    def test_retired_entry_is_not_executed(self):
        tmp, root = self.make_root()
        try:
            registry = self.registry()
            registry["entries"][0]["status"] = "retired"
            registry["entries"][0]["assertions"] = [{"kind": "text_contains", "path": "missing.py", "value": "x"}]
            result = m.verify_registry(registry, root)
            self.assertEqual(result["active_entries"], 1)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
