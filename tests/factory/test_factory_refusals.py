"""Typed refusals and the model-free stale-base re-head.

Before D-023 every validation refusal reached GitHub as a bare exception class name, and the
logs that carried the reason stayed on an ephemeral runner; nothing could tell a security-guard
veto from a base that moved. These tests pin the vocabulary (reason codes, their producers, the
secret scrub, the markers), then exercise the real `validate_pr`, `rehead_pr` and
`dispatch_once` through the rehearsal harness with the same fakes the ordering suite uses.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import refusal as R  # noqa: E402
from factory_kernel.github_cli import GitHubClient  # noqa: E402
from factory_kernel.runtime import NeedsHuman  # noqa: E402
from harness.rehearsal import (  # noqa: E402
    AUTHORITY_ROLES, BASE, HEAD, NEW_BASE, NEW_HEAD, PR_NUMBER, Scenario, rehearse,
)

STALE_EVIDENCE = "PR trust root is not current with origin/main; rebase required: harness/ci.py"
STALE_MERGE = "main moved after evidence; rebase/revalidate before merge"
FAKE_GH_TOKEN = "ghp_" + "A" * 36
FAKE_ANTHROPIC = "sk-ant-" + "b" * 24


def refusal_marker(code: str, *, head: str = HEAD) -> str:
    record = R.refusal_record(
        R.Refusal(code, R.AUTHORITY[code], "", "", None, "", "NeedsHuman"),
        pr=PR_NUMBER, head=head, base=BASE, stage="x", timestamp="2026-09-04T00:00:00Z",
    )
    return R.render_refusal_marker(record) + "\nDark Factory validation failed closed."


def rehead_marker() -> str:
    return R.render_rehead_marker({"version": "1.0", "pr": PR_NUMBER, "old_head": HEAD,
                                   "new_head": NEW_HEAD, "old_base": BASE, "new_base": NEW_BASE})


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


RED_OK = {"tests/red_test.py": sha("assert True\n")}
WT_OK = {"tests/red_test.py": "assert True\n"}


def stale_pr_scenario(name: str, **overrides) -> Scenario:
    values = dict(
        name=name, command="rehead", labels=("factory:needs-fix",),
        comments=(refusal_marker("stale_base"),), red_files=RED_OK, worktree_files=WT_OK,
    )
    values.update(overrides)
    return Scenario(**values)


class VocabularyTests(unittest.TestCase):
    def test_every_stale_base_pattern_is_pinned_to_its_producer(self):
        """The class is detected by text, so the producer's text must not drift silently."""
        for producer, pattern in R.STALE_BASE_PATTERNS:
            with self.subTest(producer):
                self.assertIn(pattern, (ROOT / producer).read_text(encoding="utf-8"))

    def test_secret_shapes_equal_the_guard_shapes(self):
        spec = importlib.util.spec_from_file_location("fs", ROOT / "scripts/factory_security.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.assertEqual([p.pattern for p in R.SECRET_PATTERNS],
                         [p.pattern for _kind, p in guard.SECRET_PATTERNS])
        self.assertEqual(R.GENERIC_SECRET.pattern, guard.GENERIC_SECRET.pattern)

    def test_every_reason_code_names_an_authority(self):
        self.assertEqual(set(R.REASON_CODES), set(R.AUTHORITY))

    def test_tool_name_and_phase(self):
        self.assertEqual(R.tool_name(["python", "scripts/factory_security.py", "--pr", "1"]), "factory_security.py")
        self.assertEqual(R.tool_phase(["python", "scripts/factory_security.py", "--pr", "1"]), "")
        self.assertEqual(R.tool_phase(["python", "harness/merge_verify.py", "pre", "--pr", "1"]), "pre")
        self.assertEqual(R.tool_name(["git", "rebase", "origin/main"]), "git")
        self.assertEqual(R.tool_phase(["git", "rebase", "origin/main"]), "rebase")
        self.assertEqual(R.tool_name([]), "")

    def test_classification_table(self):
        def refused(argv, output):
            return R.ToolRefused(argv, rc=1, output=output)

        security = ["python", "scripts/factory_security.py", "--pr", "1"]
        provenance = ["python", "scripts/factory_provenance.py", "fetch", "--head", "x"]
        evidence = ["python", "scripts/factory_evidence_spine.py", "--pr", "1"]
        legacy_evidence = ["python", "scripts/factory_evidence.py", "--pr", "1"]
        pre = ["python", "harness/merge_verify.py", "pre", "--pr", "1"]
        cases = [
            ("security_guard", refused(security, "protected path"), "security_guard"),
            ("attached_evidence", NeedsHuman("attached factory-proof block is missing"), "attached_evidence"),
            ("code_holdout", NeedsHuman("blinded holdout rejected PR"), "code_holdout"),
            ("provenance", refused(provenance, "exact PR head has no builder provenance note"), "provenance"),
            ("provenance", NeedsHuman("builder provenance is unusable for certification: "
                                      "builder provenance was built from a different base"), "stale_base"),
            ("architecture_holdout", NeedsHuman("architecture holdout returned invalid JSON"), "architecture_holdout"),
            ("certifier", NeedsHuman("independent contract certifier rejected the contract claim"), "certifier:contract"),
            ("certifier", NeedsHuman("independent design certifier returned invalid JSON"), "certifier:design"),
            ("certifier", NeedsHuman("independent architecture-governor certifier did not declare its own subject"), "certifier:governor"),
            ("certifier", NeedsHuman("cannot certify pre-code claims without a linked issue number"), "unknown"),
            ("evidence_spine", refused(evidence, "... architecture holdout refused"), "architecture_holdout"),
            ("evidence_spine", refused(legacy_evidence, "RED replay failed"), "evidence_spine"),
            ("evidence_spine", refused(evidence, STALE_EVIDENCE), "stale_base"),
            ("merge_preauth", refused(pre, "evidence spine has not reached 100 percent completion"), "merge_preauth"),
            ("merge_preauth", refused(pre, STALE_MERGE), "stale_base"),
            ("identity", NeedsHuman("PR #1 is not open"), "identity"),
            ("no-such-stage", RuntimeError("?"), "unknown"),
        ]
        for stage, exc, expected in cases:
            with self.subTest(f"{stage}: {exc}"):
                self.assertEqual(R.classify(stage, exc), expected)
                self.assertIn(R.classify(stage, exc), R.REASON_CODES)

    def test_tool_refused_keeps_the_old_message_and_is_a_runtime_error(self):
        exc = R.ToolRefused(["python", "harness/merge_verify.py", "pre"], rc=3, output="x" * 5000)
        self.assertIsInstance(exc, RuntimeError)
        self.assertTrue(str(exc).startswith("python harness/merge_verify.py pre failed rc=3: "))
        self.assertEqual(len(exc.tail), 4000)
        self.assertEqual((exc.tool, exc.phase, exc.rc), ("merge_verify.py", "pre", 3))

    def test_scrub_redacts_every_secret_shape(self):
        # Assembled at run time so this file's own diff carries none of the shapes it tests;
        # the guard scans added lines and would refuse the PR otherwise.
        db_url = "postgresql://" + "alice:hunter22" + "@db.internal/app"
        pem = "-----BEGIN " + "PRIVATE KEY-----"
        generic = "password = " + '"correct-horse-' + 'battery-staple"'
        text = "\n".join([
            f"token={FAKE_GH_TOKEN}", f"key {FAKE_ANTHROPIC}", "sk-proj-" + "c" * 24,
            "AIza" + "d" * 32, "AKIA" + "E" * 16, db_url, pem, generic, "keep me",
        ])
        out = R.scrub(text)
        for secret in (FAKE_GH_TOKEN, FAKE_ANTHROPIC, "c" * 24, "d" * 32, "E" * 16,
                       "alice:hunter22", "BEGIN PRIVATE", "correct-horse-battery-staple"):
            self.assertNotIn(secret, out)
        self.assertIn("keep me", out)
        self.assertGreaterEqual(out.count(R.REDACTED), 8)

    def test_describe_scrubs_the_tail_and_bounds_it(self):
        exc = R.ToolRefused(["python", "scripts/factory_security.py"], rc=1,
                            output="x" * 3000 + f" {FAKE_GH_TOKEN}")
        refusal = R.describe("security_guard", exc)
        self.assertNotIn(FAKE_GH_TOKEN, refusal.detail)
        self.assertIn(R.REDACTED, refusal.detail)
        self.assertLessEqual(len(refusal.detail), 2000)
        self.assertEqual(refusal.exception, "ToolRefused")

    def test_markers_round_trip_and_eligibility(self):
        record = R.refusal_record(
            R.describe("code_holdout", NeedsHuman("blinded holdout rejected PR")),
            pr=7, head="a" * 40, base="b" * 40, stage="code_holdout", timestamp="t",
        )
        body = R.render_refusal_marker(record) + "\nhuman text"
        self.assertNotIn("detail", body.split("-->")[0].split("{", 1)[1])  # no tail in a comment
        found = R.latest_refusal([body])
        self.assertEqual(found["reason_code"], "code_holdout")
        self.assertEqual(found["head"], "a" * 40)
        stale = refusal_marker("stale_base")
        self.assertFalse(R.rehead_eligible([]))
        self.assertTrue(R.rehead_eligible([stale]))
        self.assertFalse(R.rehead_eligible([stale, rehead_marker()]))
        self.assertFalse(R.rehead_eligible([refusal_marker("code_holdout")]))
        self.assertFalse(R.rehead_eligible([stale, refusal_marker("code_holdout")]))
        self.assertTrue(R.rehead_eligible([refusal_marker("code_holdout"), stale]))
        self.assertEqual(R.rehead_count([stale, rehead_marker(), "plain", rehead_marker()]), 2)
        self.assertIsNone(R.latest_refusal(["<!-- dark-factory-refusal: not json -->"]))


class PushLeaseTests(unittest.TestCase):
    def _push(self, **kwargs) -> list[str]:
        seen: list[list[str]] = []

        def fake_run(argv, **_):
            seen.append(list(argv))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"GH_TOKEN": FAKE_GH_TOKEN}), \
             mock.patch("factory_kernel.github_cli.subprocess.run", side_effect=fake_run):
            GitHubClient("owner/repo", cwd=tmp).push_branch("factory/x", **kwargs)
        return seen[-1]

    def test_plain_push_is_never_forced(self):
        argv = self._push()
        self.assertEqual(argv[:2], ["git", "push"])
        self.assertFalse(any(a.startswith("--force") for a in argv))

    def test_rehead_push_is_forced_only_with_the_judged_lease(self):
        argv = self._push(force_with_lease="a" * 40)
        self.assertIn(f"--force-with-lease=refs/heads/factory/x:{'a' * 40}", argv)
        self.assertNotIn("--force", argv)

    def test_lease_must_be_an_exact_object_id(self):
        with self.assertRaises(ValueError):
            self._push(force_with_lease="main")


def latest_code(trace) -> str | None:
    found = R.latest_refusal(trace.pr_comments)
    return found["reason_code"] if found else None


class DurableRefusalTests(unittest.TestCase):
    """Every refusal the validator can raise leaves a reason code on the PR."""

    def test_each_refuser_yields_its_reason_code(self):
        cases = [
            (Scenario("guard", fail="factory_security.py"), "security_guard"),
            (Scenario("no-attachments", body=f"Fixes #42\n"), "attached_evidence"),
            (Scenario("holdout", reject="holdout"), "code_holdout"),
            (Scenario("provenance", fail="factory_provenance.py"), "provenance"),
            (Scenario("arch", reject="architecture-holdout"), "architecture_holdout"),
            (Scenario("c1", reject="contract-certifier"), "certifier:contract"),
            (Scenario("c2", reject="design-certifier"), "certifier:design"),
            (Scenario("c3", reject="governor-certifier"), "certifier:governor"),
            (Scenario("spine", fail="factory_evidence.py"), "evidence_spine"),
            (Scenario("preauth", fail="merge_verify.py:pre"), "merge_preauth"),
            (Scenario("stale-evidence", fail="factory_evidence.py", fail_detail=STALE_EVIDENCE), "stale_base"),
            (Scenario("stale-preauth", fail="merge_verify.py:pre", fail_detail=STALE_MERGE), "stale_base"),
        ]
        for scenario, expected in cases:
            with self.subTest(scenario.name):
                trace = rehearse(scenario)
                self.assertFalse(trace.happened("merge_squash"))
                self.assertIn("add_pr_label:factory:needs-fix", trace.names())
                self.assertEqual(latest_code(trace), expected, trace.error)
                self.assertIsNotNone(trace.refusal_record, "no validation-refusal.json was written")
                self.assertEqual(trace.refusal_record["reason_code"], expected)
                self.assertEqual(trace.refusal_record["head"], HEAD)
                self.assertIn(R.AUTHORITY[expected], trace.pr_comments[-1])
                self.assertNotIn("remains on the host", trace.pr_comments[-1])

    def test_a_build_fault_charges_the_rebuild_budget_and_a_stale_base_does_not(self):
        from factory_kernel.runtime import KernelRuntime

        fault = rehearse(Scenario("holdout", reject="holdout"))
        self.assertTrue(any(KernelRuntime.VALIDATION_FAILURE_MARKER in c for c in fault.issue_comments))

        stale = rehearse(Scenario("stale", fail="merge_verify.py:pre", fail_detail=STALE_MERGE))
        self.assertEqual(latest_code(stale), "stale_base")
        self.assertEqual(stale.issue_comments, [], "a moved base must not consume the issue's attempts")
        self.assertIn("re-heads the branch", stale.pr_comments[-1])
        self.assertNotIn("add_pr_label:factory:needs-human", stale.names())

    def test_a_second_stale_base_escalates_to_a_human(self):
        trace = rehearse(Scenario(
            "stale-twice", fail="merge_verify.py:pre", fail_detail=STALE_MERGE,
            comments=(refusal_marker("stale_base"), rehead_marker()),
        ))
        self.assertEqual(latest_code(trace), "stale_base")
        self.assertIn("add_pr_label:factory:needs-human", trace.names())
        self.assertEqual(trace.issue_comments, [])

    def test_a_secret_in_the_tool_tail_never_reaches_the_record_or_the_comment(self):
        trace = rehearse(Scenario(
            "leaky", fail="factory_security.py",
            fail_detail=f"guard died holding {FAKE_GH_TOKEN} and {FAKE_ANTHROPIC}",
        ))
        record = json.dumps(trace.refusal_record)
        for secret in (FAKE_GH_TOKEN, FAKE_ANTHROPIC):
            self.assertNotIn(secret, record)
            self.assertNotIn(secret, "\n".join(trace.pr_comments))
        self.assertIn(R.REDACTED, trace.refusal_record["detail"])

    def test_the_comment_carries_the_marker_the_dispatcher_reads(self):
        trace = rehearse(Scenario("stale", fail="merge_verify.py:pre", fail_detail=STALE_MERGE))
        self.assertTrue(R.rehead_eligible(trace.pr_comments))


class ReheadTests(unittest.TestCase):
    """The stale-base re-head is model-free, blinded, exact, and runs once."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.trace = rehearse(stale_pr_scenario("rehead-happy"))

    def test_the_rehead_completes_and_hands_the_pr_back_to_validation(self):
        t = self.trace
        self.assertEqual(t.outcome, "returned", t.error)
        names = t.names()
        self.assertIn("remove_pr_label:factory:needs-fix", names)
        self.assertIn("add_pr_label:factory:needs-review", names)
        self.assertEqual(R.rehead_count(t.pr_comments), 1)
        self.assertIn(f"onto `{NEW_BASE}`", t.pr_comments[-1])
        self.assertFalse(t.happened("merge_squash"))
        self.assertTrue(t.happened("worktree_removed"))

    def test_the_certified_pack_is_fetched_before_the_rebase_and_nothing_model_side_reruns(self):
        t = self.trace
        fetch = t.execs("factory_provenance.py", "fetch")
        self.assertEqual(len(fetch), 1)
        self.assertLess(t.steps.index(fetch[0]), t.index("git:rebase"))
        # No plan, contract, context, architecture, test author, implement or review ran.
        self.assertEqual(t.names("agent"), ["conformance"])
        for role in AUTHORITY_ROLES:
            self.assertNotIn(role, t.names("agent"))
        self.assertFalse(t.happened("factory_evidence.py"))

    def test_everything_a_new_head_invalidates_is_recomputed_in_order(self):
        t = self.trace
        greens = t.execs("factory_proof.py", "green")
        self.assertEqual(len(greens), 2, "GREEN after rebase and final GREEN after conformance")
        rebase = t.index("git:rebase")
        first_green = t.steps.index(greens[0])
        conformance_agent = t.index("conformance")
        conformance_gate = t.steps.index(t.execs("factory_architecture.py", "conformance")[0])
        final_green = t.steps.index(greens[1])
        quick = t.index("ci.py")
        push = t.index(f"push_branch(lease={HEAD})")
        publish = t.steps.index(t.execs("factory_provenance.py", "publish")[0])
        self.assertLess(rebase, first_green)
        self.assertLess(first_green, conformance_agent)
        self.assertLess(conformance_agent, conformance_gate)
        self.assertLess(conformance_gate, final_green)
        self.assertLess(final_green, quick)
        self.assertLess(quick, push)
        self.assertLess(push, t.steps.index(t.execs("factory_protocol.py", "attach")[0]))
        self.assertLess(push, t.steps.index(t.execs("factory_proof.py", "attach")[0]))
        self.assertLess(push, publish)
        self.assertLess(publish, t.index("add_pr_label:factory:needs-review"))
        self.assertEqual(greens[0].argv[-1].split(os.sep)[-1], "green-proof.json")
        self.assertEqual(greens[1].argv[-1].split(os.sep)[-1], "final-green-proof.json")

    def test_the_push_is_forced_only_against_the_judged_head(self):
        self.assertIn(f"push_branch(lease={HEAD})", self.trace.names())
        self.assertNotIn("push_branch(lease=None)", self.trace.names())

    def test_the_rehead_worktree_is_blinded_like_a_build(self):
        self.assertIn("worktree_blind=yes", self.trace.names())

    def test_a_rebase_conflict_escalates_without_pushing(self):
        t = rehearse(stale_pr_scenario("conflict", rebase_conflict=True))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("rebase conflict", t.error)
        self.assertIn("add_pr_label:factory:needs-human", t.names())
        self.assertEqual(t.execs("factory_proof.py", "green"), [])
        self.assertFalse(any(n.startswith("push_branch") for n in t.names()))

    def test_a_red_hashed_test_that_differs_after_rebase_refuses(self):
        t = rehearse(stale_pr_scenario(
            "red-changed", worktree_files={"tests/red_test.py": "assert False\n"}))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("RED-hashed", t.error)
        self.assertEqual(t.execs("factory_proof.py", "green"), [])
        self.assertFalse(any(n.startswith("push_branch") for n in t.names()))

    def test_a_pack_without_an_immutable_file_map_refuses(self):
        t = rehearse(stale_pr_scenario("no-red-map", red_files={}))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertIn("immutable file map", t.error)

    def test_only_a_first_stale_base_refusal_is_reheaded(self):
        cases = {
            "not-stale": (refusal_marker("code_holdout"),),
            "already-reheaded": (refusal_marker("stale_base"), rehead_marker(), refusal_marker("stale_base")),
            "no-refusal": (),
        }
        for name, comments in cases.items():
            with self.subTest(name):
                t = rehearse(stale_pr_scenario(name, comments=comments))
                self.assertEqual(t.outcome, "NeedsHuman")
                self.assertIn("not a first stale-base refusal", t.error)
                self.assertEqual(t.execs("factory_provenance.py"), [])
                self.assertFalse(t.happened("git:rebase"))

    def test_a_pr_without_the_needs_fix_label_is_refused(self):
        t = rehearse(stale_pr_scenario("wrong-label", labels=("factory:needs-review",)))
        self.assertEqual(t.outcome, "NeedsHuman")
        self.assertFalse(t.happened("git:rebase"))


class ReheadDispatchTests(unittest.TestCase):
    def dispatch(self, name: str, comments, labels=("factory:needs-fix",)):
        return rehearse(stale_pr_scenario(
            name, command="dispatch", comments=comments, labels=labels,
            prs=({"number": PR_NUMBER, "updatedAt": "2026-09-04", "labels": list(labels)},),
        ))

    def test_a_stale_base_refusal_is_reheaded_on_the_next_dispatch(self):
        t = self.dispatch("dispatch-stale", (refusal_marker("stale_base"),))
        self.assertEqual(t.outcome, "returned", t.error)
        self.assertIn("dispatch:rehead-pr", t.names())
        self.assertTrue(t.happened("git:rebase"))
        self.assertIn("add_pr_label:factory:needs-review", t.names())

    def test_rehead_runs_after_review_prs_and_before_builds(self):
        t = self.dispatch("dispatch-order", (refusal_marker("stale_base"),))
        names = t.names()
        self.assertLess(names.index("list_prs:factory:needs-review"), names.index("list_prs:factory:needs-fix"))
        self.assertNotIn("list_issues:factory:accepted", names)

    def test_a_non_stale_needs_fix_pr_is_left_alone(self):
        for name, comments in (
            ("code-holdout", (refusal_marker("code_holdout"),)),
            ("second-stale", (refusal_marker("stale_base"), rehead_marker(), refusal_marker("stale_base"))),
            ("unmarked", ()),
        ):
            with self.subTest(name):
                t = self.dispatch(name, comments)
                self.assertIn("dispatch:idle", t.names())
                self.assertFalse(t.happened("git:rebase"))


if __name__ == "__main__":
    unittest.main()
