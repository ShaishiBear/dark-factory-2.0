import unittest
from pathlib import Path
from types import SimpleNamespace

from factory_kernel.triage import TriageEngine

ROOT = Path(__file__).parents[2]


class FakeGitHub:
    def __init__(self, issues):
        self.issues = issues
        self.labels_added = []
        self.comments = []
        self.calls = []

    def json(self, args):
        self.calls.append(args)
        if args[:2] == ["repo", "view"]:
            return {"owner": {"login": "current-owner"}}
        if args[:2] == ["issue", "list"]:
            return self.issues
        raise AssertionError(args)

    def add_issue_label(self, number, label):
        self.labels_added.append((number, label))

    def comment_issue(self, number, body):
        self.comments.append((number, body))


class TriageControlPlaneTests(unittest.TestCase):
    def engine(self, issues):
        runtime = SimpleNamespace(
            github=FakeGitHub(issues),
            config=SimpleNamespace(
                repository="ShaishiBear/dark-factory-2.0",
                labels={"rate_limited": "factory:rate-limited"},
            ),
            repo_root=ROOT,
        )
        return TriageEngine(runtime)

    def issue(self, number, author, created="2099-01-01T01:00:00Z", state="OPEN"):
        return {
            "number": number,
            "author": {"login": author},
            "createdAt": created,
            "state": state,
            "labels": [],
        }

    def test_flood_exemption_uses_current_repository_owner(self):
        engine = self.engine([])
        engine._apply_daily_cap()
        self.assertTrue(
            any(call[:2] == ["repo", "view"] for call in engine.github.calls),
            "triage must resolve the current repository owner rather than hard-code one",
        )

    def test_decision_set_must_cover_every_candidate_exactly_once(self):
        engine = self.engine([])
        candidates = [{"number": 11}, {"number": 12}]
        good = {
            "version": "1.0",
            "decisions": [
                {
                    "issue_number": 11,
                    "verdict": "accept",
                    "priority": "high",
                    "classification": "bug",
                    "reason": "Reproducible defect.",
                    "duplicate_of": None,
                },
                {
                    "issue_number": 12,
                    "verdict": "reject",
                    "priority": "low",
                    "classification": "enhancement",
                    "reason": "Outside the mission.",
                    "duplicate_of": None,
                },
            ],
        }
        self.assertEqual(len(engine._validate_decisions(good, candidates)), 2)
        bad = {"version": "1.0", "decisions": good["decisions"][:1]}
        with self.assertRaisesRegex(RuntimeError, "every candidate"):
            engine._validate_decisions(bad, candidates)


if __name__ == "__main__":
    unittest.main()
