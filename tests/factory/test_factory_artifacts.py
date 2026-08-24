from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class ArtifactTests(unittest.TestCase):
    def test_design_requires_exact_ac_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            c = root / "contract.json"; c.write_text(json.dumps(contract()))
            ctx = root / "context.json"; ctx.write_text(json.dumps({"x": 1}))
            raw = root / "design.json"; raw.write_text(json.dumps({
                "version": "1.0",
                "modules": ["service"],
                "seams": ["api.create", "service.update"],
                "public_interfaces": [],
                "invariants": ["preserve auth"],
                "data_flows": ["request -> service"],
                "ac_mapping": {"AC-1": ["api.create"]},
            }))
            args = argparse.Namespace(input=str(raw), contract=str(c), context=str(ctx), output=str(root / "out.json"))
            with self.assertRaises(SystemExit):
                artifacts.compile_design(args)

    def test_design_rejects_undeclared_seam(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            c = root / "contract.json"; c.write_text(json.dumps(contract()))
            ctx = root / "context.json"; ctx.write_text(json.dumps({"x": 1}))
            raw = root / "design.json"; raw.write_text(json.dumps({
                "version": "1.0",
                "modules": ["service"],
                "seams": ["api.create", "service.update"],
                "public_interfaces": [],
                "invariants": ["preserve auth"],
                "data_flows": ["request -> service"],
                "ac_mapping": {"AC-1": ["missing.seam"], "AC-2": ["service.update"]},
            }))
            args = argparse.Namespace(input=str(raw), contract=str(c), context=str(ctx), output=str(root / "out.json"))
            with self.assertRaises(SystemExit):
                artifacts.compile_design(args)

    def test_ticket_fails_closed_when_blocker_open(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            c = root / "contract.json"; c.write_text(json.dumps(contract()))
            issue = {
                "number": 7,
                "title": "Example",
                "body": "Blocked by: #8",
                "state": "OPEN",
                "labels": [{"name": "factory:accepted"}],
                "url": "https://example/7",
            }
            blocker = {"number": 8, "state": "OPEN", "labels": [], "body": "", "title": "Blocker"}
            args = argparse.Namespace(issue=7, contract=str(c), ticket_output=str(root / "ticket.json"), frontier_output=str(root / "frontier.json"))
            with mock.patch.object(artifacts, "gh_issue", side_effect=lambda n: issue if n == 7 else blocker):
                with self.assertRaises(SystemExit):
                    artifacts.compile_ticket(args)


if __name__ == "__main__":
    unittest.main()
