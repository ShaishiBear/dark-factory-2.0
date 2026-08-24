import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "scripts" / "factory_evidence.py"
spec = importlib.util.spec_from_file_location("factory_evidence_security", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SecurityEvidenceTests(unittest.TestCase):
    def good(self, **overrides):
        value = {
            "version": "1.0",
            "verdict": "pass",
            "protected_paths": [],
            "dependency_changes": [
                {"ecosystem": "python", "scope": "runtime", "name": "resend", "kind": "added"}
            ],
            "secret_findings": [],
            "findings": [],
        }
        value.update(overrides)
        return value

    def test_security_pass_is_hashed_into_safe_summary(self):
        result = m.verify_security_result(self.good())
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["dependency_changes"], 1)
        self.assertEqual(len(result["sha256"]), 64)

    def test_security_fail_cannot_authorize_merge(self):
        with self.assertRaises(SystemExit):
            m.verify_security_result(self.good(verdict="fail"))

    def test_security_pass_with_secret_is_rejected(self):
        with self.assertRaises(SystemExit):
            m.verify_security_result(self.good(secret_findings=[{"kind": "private_key", "path": "x.py"}]))

    def test_security_pass_with_protected_path_is_rejected(self):
        with self.assertRaises(SystemExit):
            m.verify_security_result(self.good(protected_paths=[".github/workflows/ci.yml"]))

    def test_security_pass_with_findings_is_rejected(self):
        with self.assertRaises(SystemExit):
            m.verify_security_result(self.good(findings=[{"kind": "lockfile", "path": "app/backend/uv.lock"}]))


if __name__ == "__main__":
    unittest.main()
