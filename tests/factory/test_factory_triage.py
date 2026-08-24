import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
TRIAGE = ROOT / ".archon" / "workflows" / "dark-factory-triage.yaml"


class TriageControlPlaneTests(unittest.TestCase):
    def test_flood_exemption_uses_current_repository_owner(self):
        text = TRIAGE.read_text(encoding="utf-8")
        self.assertIn("gh repo view --json owner --jq '.owner.login'", text)
        self.assertNotIn('OWNER="coleam00"', text)


if __name__ == "__main__":
    unittest.main()
