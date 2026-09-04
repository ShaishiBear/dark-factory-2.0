"""The credential-free build programs never call GitHub; the kernel snapshots the issue first.

After #54 the contract/context/proof programs run with `credential_scope="none"`. The ticket and
frontier compiler used to read the issue and its blockers through `gh`, so the second canary
attempt (run 33896546840) died at the context stage. The kernel now fetches the issue and every
`Blocked by: #N` issue with its own authority, writes `issue-frontier.json` before any model
stage, and the script judges readiness from that snapshot.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME = ROOT / "factory_kernel" / "runtime.py"
SCOPE_NONE_PROGRAMS = (
    "scripts/factory_artifacts.py",
    "scripts/factory_protocol.py",
    "scripts/factory_impact.py",
    "scripts/factory_architecture.py",
    "scripts/factory_proof.py",
)
# The two attach programs edit the PR through `gh` and run nothing model-authored; the kernel
# gives exactly those calls GitHub scope (runtime.py build_issue, after PR creation).
GH_ALLOWED_FUNCTIONS = {"attach_design", "attach", "run_attach"}

SCRIPT = ROOT / "scripts" / "factory_artifacts.py"
_spec = importlib.util.spec_from_file_location("factory_artifacts_snapshot_test", SCRIPT)
artifacts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(artifacts)


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


def _function(tree: ast.Module, class_name: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"{class_name}.{name} not found")


def _gh_calls(tree: ast.Module) -> list[tuple[str, int]]:
    """(enclosing function name, line) of every subprocess argv starting with the literal 'gh'."""
    found = []
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(func):
            if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "gh":
                found.append((func.name, node.lineno))
    return found


class NoGitHubInCredentialFreeProgramsTests(unittest.TestCase):
    def test_only_attach_paths_call_gh(self):
        checked = 0
        for rel in SCOPE_NONE_PROGRAMS:
            path = ROOT / rel
            if not path.exists():
                continue  # repo-shaped copy in the mutation runner
            checked += 1
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders = [(f, line) for f, line in _gh_calls(tree) if f not in GH_ALLOWED_FUNCTIONS]
            self.assertEqual(offenders, [], f"{rel} calls gh outside the attach path: {offenders}")
        self.assertGreaterEqual(checked, 2)

    def test_ticket_compiler_takes_the_snapshot_path(self):
        source = (ROOT / "scripts/factory_protocol.py").read_text(encoding="utf-8")
        self.assertIn('"--issue-json", str(artifacts / "issue-frontier.json")', source)


class KernelSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))

    def test_snapshot_is_written_before_the_first_model_stage(self):
        build = _function(self.tree, "KernelRuntime", "build_issue")
        order = []
        for node in ast.walk(build):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                if node.func.attr == "_agent":
                    order.append(("agent", node.lineno))
                if node.func.attr == "_write_json" and any(
                    isinstance(a, ast.BinOp) and isinstance(a.right, ast.Name) and a.right.id == "ISSUE_FRONTIER_ARTIFACT" for a in node.args
                ):
                    order.append(("snapshot", node.lineno))
        order.sort(key=lambda x: x[1])
        self.assertEqual(order[0][0], "snapshot", order)
        self.assertEqual(sum(1 for kind, _ in order if kind == "snapshot"), 1)

    def test_frontier_resolves_every_named_blocker_with_kernel_authority(self):
        from factory_kernel.runtime import KernelRuntime

        rt = KernelRuntime.__new__(KernelRuntime)
        seen = []

        class FakeGitHub:
            def issue(self, number):
                seen.append(number)
                return {"number": number, "state": "closed" if number == 8 else "OPEN"}

        rt.github = FakeGitHub()
        issue = {"number": 7, "body": "text\nBlocked by: #8\nBlocked by: #12\nBlocked by: #8\n", "labels": []}
        snap = rt._issue_frontier(issue)
        self.assertEqual(seen, [8, 12])
        self.assertEqual(snap["blockers"], [{"issue": 8, "state": "CLOSED"}, {"issue": 12, "state": "OPEN"}])
        self.assertEqual(snap["issue"], issue)
        self.assertEqual(snap["version"], "1.0")
        self.assertTrue(snap["fetched_at"].endswith("Z"))

    def test_frontier_without_blockers_is_empty(self):
        from factory_kernel.runtime import KernelRuntime

        rt = KernelRuntime.__new__(KernelRuntime)
        rt.github = mock.Mock()
        snap = rt._issue_frontier({"number": 7, "body": "no blockers here"})
        self.assertEqual(snap["blockers"], [])
        rt.github.issue.assert_not_called()


class TicketFromSnapshotTests(unittest.TestCase):
    # The compiler runs with no credentials. It never calls GitHub; the kernel wrote the issue
    # and its blockers to issue-frontier.json before any model stage.

    @staticmethod
    def issue_payload(body: str = "", labels=("factory:accepted",), state: str = "OPEN") -> dict:
        return {
            "number": 7, "title": "Example", "body": body, "state": state,
            "labels": [{"name": name} for name in labels], "url": "https://example/7",
        }

    def ticket(self, snapshot: dict | None, *, write_snapshot: bool = True):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        c = root / "contract.json"; c.write_text(json.dumps(contract()))
        snap = root / "issue-frontier.json"
        if write_snapshot:
            snap.write_text(json.dumps(snapshot))
        args = argparse.Namespace(
            issue=7, contract=str(c), issue_json=str(snap),
            ticket_output=str(root / "ticket.json"), frontier_output=str(root / "frontier.json"),
        )
        artifacts.compile_ticket(args)
        return json.loads((root / "ticket.json").read_text()), json.loads((root / "frontier.json").read_text())

    def test_ready_issue_compiles_from_the_snapshot(self) -> None:
        body = "Fix it\n\nBlocked by: #8\n"
        snapshot = {"version": "1.0", "issue": self.issue_payload(body), "blockers": [{"issue": 8, "state": "CLOSED"}], "fetched_at": "2026-09-04T00:00:00Z"}
        ticket, frontier = self.ticket(snapshot)
        self.assertTrue(frontier["ready"])
        self.assertEqual(frontier["blockers"], [{"issue": 8, "state": "CLOSED"}])
        self.assertEqual(ticket["body_sha256"], hashlib.sha256(body.encode()).hexdigest())
        self.assertEqual(ticket["acceptance"], ["AC-1", "AC-2"])

    def test_ticket_fails_closed_when_blocker_open(self) -> None:
        snapshot = {"version": "1.0", "issue": self.issue_payload("Blocked by: #8"), "blockers": [{"issue": 8, "state": "OPEN"}], "fetched_at": "x"}
        with self.assertRaises(SystemExit):
            self.ticket(snapshot)

    def test_ticket_fails_closed_without_the_accepted_label(self) -> None:
        snapshot = {"version": "1.0", "issue": self.issue_payload("", labels=()), "blockers": [], "fetched_at": "x"}
        with self.assertRaises(SystemExit):
            self.ticket(snapshot)

    def test_snapshot_for_another_issue_is_refused(self) -> None:
        payload = self.issue_payload(); payload["number"] = 9
        with self.assertRaises(SystemExit):
            self.ticket({"version": "1.0", "issue": payload, "blockers": [], "fetched_at": "x"})

    def test_missing_snapshot_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.ticket(None, write_snapshot=False)

    def test_snapshot_blockers_must_match_the_body(self) -> None:
        """A snapshot that omits a named blocker (or names an extra one) is not the kernel's."""
        with self.assertRaises(SystemExit):
            self.ticket({"version": "1.0", "issue": self.issue_payload("Blocked by: #8"), "blockers": [], "fetched_at": "x"})

    def test_compiler_never_asks_github(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("gh_issue", source)
        self.assertNotIn('"issue", "view"', source)



if __name__ == "__main__":
    unittest.main()
