"""Resuming a factory PR that pushed and opened but died before publish/handoff.

Canary attempt 10 built issue #49 end to end and opened PR #74, then died in
`_attach_and_publish`. The run's artifacts survive only as an uploaded workflow artifact. A
resume finishes such a PR from those artifacts without rebuilding and without a model: it
verifies the artifacts belong to exactly this head, re-binds contract/design/proof and the
provenance note, finishes the lease, and hands the PR to independent validation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import refusal as R  # noqa: E402
from factory_kernel.provenance import BUILDER_ARTIFACTS  # noqa: E402
from harness.rehearsal import HEAD, PR_NUMBER, Scenario, rehearse  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


RED_OK = {"tests/red_test.py": sha("assert True\n")}
WT_OK = {"tests/red_test.py": "assert True\n"}


def artifacts(*, head: str = HEAD, red_files=RED_OK) -> dict[str, dict]:
    out = {
        "task-contract.json": {"version": "2.0", "issue": {"number": 42, "title": "t"}},
        "ticket.json": {"version": "1.0", "issue": 42},
        "frontier.json": {"version": "1.0", "issue": 42, "ready": True},
        "context.json": {"version": "1.0"},
        "design.json": {"version": "1.0"},
        "architecture-governor.json": {"version": "1.0", "decision": "proceed"},
        "test-plan.json": {"version": "2.0"},
        "red-proof.json": {"version": "2.0", "test_commit": "3" * 40, "files": dict(red_files)},
        "final-green-proof.json": {"version": "2.0", "green_commit": head,
                                   "green_results": [{"exit": 0}]},
        "final-green-proof.impact.json": {"version": "1.0"},
        "final-green-proof.architecture.json": {"version": "1.0"},
        "architecture-conformance.json": {"version": "1.0", "verdict": "conform"},
        "factory-lease.json": {"lease_id": "x", "comment_id": 1, "stage": "final-green",
                               "state": "active"},
    }
    return out


def resume_scenario(name: str, **overrides) -> Scenario:
    values = dict(
        name=name, command="resume", labels=(), artifacts=artifacts(),
        worktree_files=WT_OK,
    )
    values.update(overrides)
    return Scenario(**values)


class HappyResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.trace = rehearse(resume_scenario("resume-happy"))

    def test_the_resume_completes_and_hands_the_pr_to_validation(self):
        t = self.trace
        self.assertEqual(t.outcome, "returned", t.error)
        names = t.names()
        self.assertIn("add_pr_label:factory:needs-review", names)
        self.assertIn("remove_issue_label:factory:needs-human", names)
        self.assertIn("remove_issue_label:factory:in-progress", names)
        self.assertIn("add_issue_label:factory:accepted", names)
        self.assertEqual(R.resume_count(t.pr_comments), 1)
        self.assertIn(f"`{HEAD}`", t.pr_comments[-1])
        self.assertFalse(t.happened("merge_squash"))
        self.assertTrue(t.happened("worktree_removed"))

    def test_no_model_runs_and_nothing_is_rebuilt(self):
        t = self.trace
        self.assertEqual(t.names("agent"), [])
        self.assertEqual(t.execs("factory_proof.py", "green"), [])
        self.assertEqual(t.execs("factory_proof.py", "red"), [])
        self.assertFalse(t.happened("factory_evidence.py"))
        self.assertFalse(any(n.startswith("push_branch") for n in t.names()),
                         "the branch is already pushed; a resume never pushes")

    def test_attach_attach_publish_lease_then_labels_in_order(self):
        t = self.trace
        contract = t.steps.index(t.execs("factory_protocol.py", "attach")[0])
        proof = t.steps.index(t.execs("factory_proof.py", "attach")[0])
        publish = t.steps.index(t.execs("factory_provenance.py", "publish")[0])
        lease = t.steps.index(t.execs("factory_lease.py", "finish")[0])
        review = t.index("add_pr_label:factory:needs-review")
        self.assertLess(contract, proof)
        self.assertLess(proof, publish)
        self.assertLess(publish, lease)
        self.assertLess(lease, review)
        lease_argv = t.execs("factory_lease.py", "finish")[0].argv
        self.assertIn("--pr", lease_argv)
        self.assertIn(str(PR_NUMBER), lease_argv)
        self.assertIn("pr-handoff", lease_argv)

    def test_the_resume_worktree_is_blinded_like_a_build(self):
        self.assertIn("worktree_blind=yes", self.trace.names())


class RefusalTests(unittest.TestCase):
    def test_a_proof_bound_to_another_head_is_refused(self):
        t = rehearse(resume_scenario("wrong-head", artifacts=artifacts(head="9" * 40)))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("different build", t.error)
        self.assertEqual(t.execs("factory_provenance.py", "publish"), [])
        self.assertNotIn("add_pr_label:factory:needs-review", t.names())

    def test_a_missing_builder_artifact_is_refused(self):
        partial = artifacts()
        del partial["architecture-conformance.json"]
        t = rehearse(resume_scenario("missing", artifacts=partial))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("incomplete", t.error)
        self.assertIn("architecture-conformance.json", t.error)

    def test_a_red_hashed_test_that_differs_at_the_head_is_refused(self):
        t = rehearse(resume_scenario("red-changed",
                                     worktree_files={"tests/red_test.py": "assert False\n"}))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("RED-hashed", t.error)
        self.assertEqual(t.execs("factory_protocol.py", "attach"), [])

    def test_a_pr_not_opened_by_the_factory_is_refused(self):
        t = rehearse(resume_scenario("human-pr", author="ShaishiBear"))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("not opened by the factory", t.error)

    def test_a_second_resume_escalates(self):
        marker = R.render_resume_marker({"version": "1.0", "pr": PR_NUMBER, "head": HEAD})
        t = rehearse(resume_scenario("twice", comments=(marker,)))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("already resumed", t.error)
        # A precondition refusal happens before any worktree or GitHub mutation, exactly like
        # the re-head's: nothing is labelled, nothing is attached, nothing is published.
        self.assertEqual(t.execs("factory_provenance.py", "publish"), [])
        self.assertNotIn("add_pr_label:factory:needs-review", t.names())
        self.assertFalse(t.happened("worktree_blind=yes"))

    def test_an_issue_in_neither_recoverable_state_is_refused(self):
        t = rehearse(resume_scenario("issue-closed-out", issue_labels=("factory:rejected",)))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("refusing to resume", t.error)

    def test_a_closed_pr_is_refused(self):
        t = rehearse(resume_scenario("closed", state="MERGED"))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("not open", t.error)


class AttachIdempotenceTests(unittest.TestCase):
    """Each attach program replaces an existing block of its kind rather than duplicating it,
    so a resume after a partial attach leaves exactly one block of each kind in the body."""

    def _body_after(self, script: str, pattern: str, body: str) -> str:
        source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        self.assertIn(pattern, source)
        return source

    def test_protocol_attach_strips_a_prior_contract_block(self):
        source = (ROOT / "scripts" / "factory_protocol.py").read_text(encoding="utf-8")
        match = re.search(r're\.sub\(r"(.*?)", "\\n", body, flags=re\.S\)', source)
        self.assertIsNotNone(match, "run_attach must strip an existing contract block")
        pattern = match.group(1).encode().decode("unicode_escape")
        body = ("Fixes #42\n\n<!-- factory-contract:start -->\n```factory-contract\n{}\n```\n"
                "contract-sha256: a\n<!-- factory-contract:end -->\n")
        stripped = re.sub(pattern, "\n", body, flags=re.S)
        self.assertNotIn("factory-contract:start", stripped)
        self.assertIn("Fixes #42", stripped)

    def test_proof_attach_strips_prior_proof_and_design_blocks(self):
        source = (ROOT / "scripts" / "factory_proof.py").read_text(encoding="utf-8")
        self.assertIn("DESIGN_BLOCK.sub('\\n',PROOF_BLOCK.sub('\\n',", source)
        for name in ("DESIGN_BLOCK", "PROOF_BLOCK"):
            self.assertRegex(source, rf"{name}\s*=\s*re\.compile\(")

    def test_double_resume_of_the_attach_pair_yields_one_block_each(self):
        """Drive the two strip-then-append rules the same way twice on one body."""
        src_p = (ROOT / "scripts" / "factory_protocol.py").read_text(encoding="utf-8")
        pat_c = re.search(r're\.sub\(r"(.*?)", "\\n", body, flags=re\.S\)', src_p).group(1)
        pat_c = pat_c.encode().decode("unicode_escape")
        src_f = (ROOT / "scripts" / "factory_proof.py").read_text(encoding="utf-8")
        design = re.search(r"DESIGN_BLOCK\s*=\s*re\.compile\((r?['\"].*?['\"])(?:,\s*re\.S)?\)", src_f)
        proof = re.search(r"PROOF_BLOCK\s*=\s*re\.compile\((r?['\"].*?['\"])(?:,\s*re\.S)?\)", src_f)
        self.assertIsNotNone(design); self.assertIsNotNone(proof)
        design_re = re.compile(eval(design.group(1)), re.S)  # noqa: S307 - literal from trust root
        proof_re = re.compile(eval(proof.group(1)), re.S)  # noqa: S307

        def attach_contract(body: str) -> str:
            body = re.sub(pat_c, "\n", body, flags=re.S).rstrip()
            return body + "\n\n<!-- factory-contract:start -->\n```factory-contract\n{}\n```\ncontract-sha256: a\n<!-- factory-contract:end -->\n"

        def attach_proof(body: str) -> str:
            body = design_re.sub("\n", proof_re.sub("\n", body)).rstrip()
            return (body + "\n<!-- factory-design:start -->\n```factory-design\n{}\n```\ndesign-sha256: d\n<!-- factory-design:end -->\n"
                    + "\n<!-- factory-proof:start -->\n```factory-proof\n{}\n```\nproof-sha256: p\n<!-- factory-proof:end -->\n")

        body = "Fixes #42\n"
        for _ in range(2):
            body = attach_proof(attach_contract(body))
        for kind in ("contract", "design", "proof"):
            self.assertEqual(body.count(f"<!-- factory-{kind}:start -->"), 1, kind)
        self.assertEqual(body.count("Fixes #42"), 1)


class ArtifactListTests(unittest.TestCase):
    def test_every_builder_artifact_except_policy_must_be_supplied(self):
        required = [rel for claim, rel in BUILDER_ARTIFACTS if claim != "architecture-policy"]
        self.assertEqual(sorted(required), sorted(k for k in artifacts() if k != "factory-lease.json"))


if __name__ == "__main__":
    unittest.main()
