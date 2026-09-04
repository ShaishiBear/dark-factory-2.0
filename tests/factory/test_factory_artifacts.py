from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "factory_artifacts.py"
spec = importlib.util.spec_from_file_location("factory_artifacts", SCRIPT)
assert spec and spec.loader
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


def contract() -> dict:
    return {
        "version": "2.0",
        "issue": {"number": 7, "title": "Example"},
        "summary": "Implement the requested observable behavior.",
        "behaviors": [
            {"id": "AC-1", "given": "state", "when": "action", "then": "result", "seam": "api.create"},
            {"id": "AC-2", "given": "state", "when": "other", "then": "result", "seam": "service.update"},
        ],
        "invariants": ["preserve auth"],
        "out_of_scope": [],
        "risks": [],
        "ambiguities": [],
    }


def raw_design(**overrides) -> dict:
    value = {
        "version": "1.0",
        "modules": ["service"],
        "seams": ["api.create", "service.update"],
        "public_interfaces": [],
        "invariants": ["preserve auth"],
        "data_flows": ["request -> service"],
        "ac_mapping": {"AC-1": ["api.create"], "AC-2": ["service.update"]},
        "planned_files": ["app/backend/routes/messages.py"],
        "allowed_new_files": [],
    }
    value.update(overrides)
    return value


class ArtifactTests(unittest.TestCase):
    def compile_design(self, raw_value: dict, context_value: dict | None = None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        c = root / "contract.json"; c.write_text(json.dumps(contract()))
        ctx = root / "context.json"; ctx.write_text(json.dumps(
            context_value or {"files": ["app/backend/routes/messages.py"]}
        ))
        raw = root / "design.json"; raw.write_text(json.dumps(raw_value))
        out = root / "out.json"
        args = argparse.Namespace(input=str(raw), contract=str(c), context=str(ctx), output=str(out))
        artifacts.compile_design(args)
        return json.loads(out.read_text())

    def test_valid_design_compiles_exact_file_envelope(self) -> None:
        result = self.compile_design(raw_design())
        self.assertEqual(result["planned_files"], ["app/backend/routes/messages.py"])
        self.assertEqual(result["allowed_new_files"], [])

    def test_design_requires_exact_ac_coverage(self) -> None:
        with self.assertRaises(SystemExit):
            self.compile_design(raw_design(ac_mapping={"AC-1": ["api.create"]}))

    def test_design_rejects_undeclared_seam(self) -> None:
        with self.assertRaises(SystemExit):
            self.compile_design(raw_design(
                ac_mapping={"AC-1": ["missing.seam"], "AC-2": ["service.update"]}
            ))

    def test_existing_planned_file_must_be_in_validated_context(self) -> None:
        with self.assertRaises(SystemExit):
            self.compile_design(raw_design(), {"files": ["app/backend/main.py"]})

    def test_allowed_new_file_must_be_planned(self) -> None:
        with self.assertRaises(SystemExit):
            self.compile_design(raw_design(
                allowed_new_files=["app/backend/services/new_service.py"]
            ))

    def test_nonexistent_planned_file_requires_explicit_new_authorization(self) -> None:
        new_path = "app/backend/services/dark_factory_test_new_service.py"
        self.assertFalse((ROOT / new_path).exists())
        with self.assertRaises(SystemExit):
            self.compile_design(raw_design(planned_files=[new_path]), {"files": []})

    def test_explicit_nonexistent_file_can_be_authorized(self) -> None:
        new_path = "app/backend/services/dark_factory_test_new_service.py"
        result = self.compile_design(
            raw_design(planned_files=[new_path], allowed_new_files=[new_path]),
            {"files": []},
        )
        self.assertEqual(result["allowed_new_files"], [new_path])


if __name__ == "__main__":
    unittest.main()
