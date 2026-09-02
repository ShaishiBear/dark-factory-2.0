from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "factory_proof.py"
spec = importlib.util.spec_from_file_location("factory_proof", SCRIPT)
assert spec and spec.loader
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

TEST_FILE = "tests/factory/test_factory_evidence.py"


class ProofSpecTests(unittest.TestCase):
    def contract(self):
        return {
            "version": "2.0",
            "behaviors": [{"id": "AC-1"}, {"id": "AC-2"}],
        }

    def design(self):
        return {"ac_mapping": {"AC-1": ["api.create"], "AC-2": ["service.update"]}}

    def checkpoint(self, ac):
        return {
            "acceptance_id": ac, "cwd": ".", "argv": ["python", "-V"],
            "files": [TEST_FILE], "expected_failure": f"{ac} expected",
        }

    def write_spec(self, checkpoints):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"version": "2.0", "checkpoints": checkpoints}, tmp)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return tmp.name

    def test_complete_matrix_is_accepted_and_gets_design_seams(self):
        path = self.write_spec([self.checkpoint("AC-1"), self.checkpoint("AC-2")])
        with mock.patch.object(proof, "artifacts", return_value=(self.contract(), self.design(), ["AC-1", "AC-2"])):
            value = proof.spec(path)
        self.assertEqual([x["acceptance_id"] for x in value["checkpoints"]], ["AC-1", "AC-2"])
        self.assertEqual(value["checkpoints"][0]["seams"], ["api.create"])

    def test_missing_ac_is_rejected(self):
        path = self.write_spec([self.checkpoint("AC-1")])
        with mock.patch.object(proof, "artifacts", return_value=(self.contract(), self.design(), ["AC-1", "AC-2"])):
            with self.assertRaises(SystemExit):
                proof.spec(path)

    def test_duplicate_ac_is_rejected(self):
        path = self.write_spec([self.checkpoint("AC-1"), self.checkpoint("AC-1")])
        with mock.patch.object(proof, "artifacts", return_value=(self.contract(), self.design(), ["AC-1", "AC-2"])):
            with self.assertRaises(SystemExit):
                proof.spec(path)

    def test_unknown_ac_is_rejected(self):
        path = self.write_spec([self.checkpoint("AC-1"), self.checkpoint("AC-3")])
        with mock.patch.object(proof, "artifacts", return_value=(self.contract(), self.design(), ["AC-1", "AC-2"])):
            with self.assertRaises(SystemExit):
                proof.spec(path)


if __name__ == "__main__":
    unittest.main()
