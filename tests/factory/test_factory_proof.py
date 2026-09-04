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


class CheckpointEnvironmentSanitizationTests(unittest.TestCase):
    @mock.patch("subprocess.run")
    def test_run_strips_gh_token_and_github_token_from_child_environment(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.dict("os.environ", {"GH_TOKEN": "secret_gh_token", "GITHUB_TOKEN": "secret_github_token", "PATH": "/bin"}, clear=True):
            rc, out = proof.run(["python", "-c", "import os; print('GH_TOKEN' in os.environ)"], ".")
            self.assertEqual(rc, 0)
            self.assertEqual(out, "ok")
            passed_env = mock_run.call_args.kwargs["env"]
            self.assertNotIn("GH_TOKEN", passed_env)
            self.assertNotIn("GITHUB_TOKEN", passed_env)


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
