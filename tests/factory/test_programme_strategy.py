"""Planning advice reaches one worker without acquiring proof or scope authority."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from factory_kernel.canonical import sha256_value
from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.programme import ProgrammeRefused, compile_programme
from factory_kernel.programme_runtime import ProgrammeQueue
from factory_kernel.programme_strategy import planning_advice
from tests.factory import test_exploration as exploration_fixture
from tests.factory import test_factory_carry as carry_fixture
from tests.factory import test_factory_programme as programme_fixture
from tests.factory.test_frontdoor_intent import OWNER, WORKER


def example():
    raw = programme_fixture.example()
    raw["version"] = "1.1"
    raw["strategy"] = {
        "qualification_status": "UNPROVEN", "proof_reuse_allowed": False,
        "spec_sha256": raw["proposal"]["spec_sha256"],
        "source": {"session_id": "navigation", "recommendation_sha256": "a" * 64, "context_identity": "b" * 64},
        "candidate": {"id": "preserve-modal", "family": "local", "mechanism": "STRATEGY_SENTINEL: preserve the modal.",
            "claim_ids": ["focus"], "trajectory": {
                "implementation": "Keep the opener in a ref.", "validation": "Exercise keyboard focus independently.",
                "failure_repair": "Revisit if the opener unmounts.", "migration_reversal": "Revert the local ref."}},
        "claims": [
            {"id": "mounted", "statement": "The opener remains mounted.", "kind": "assumption",
             "depends_on": [], "acceptance": ["AC1"], "revisit_when": "The opener unmounts.", "status": "active"},
            {"id": "focus", "statement": "Focus can return to the opener.", "kind": "strategy",
             "depends_on": ["mounted"], "acceptance": ["AC2"], "revisit_when": "Focus cannot return.", "status": "active"}],
        "rationale": "A local change avoids replacing the navigation architecture.",
        "remaining_uncertainty": ["Production keyboard behaviour remains unqualified."]}
    return raw


class StrategyTests(unittest.TestCase):
    def compile(self, raw):
        return compile_programme(raw, repository=programme_fixture.REPO)

    def test_v1_identity_and_input_are_unchanged(self):
        raw = programme_fixture.example()
        programme = self.compile(raw)
        expected = {"spec": raw["spec"], "items": raw["proposal"]["items"], "app_login": raw["app_login"], "version": "1.0"}
        self.assertEqual(programme.sha256, sha256_value(expected))
        self.assertEqual(programme.to_input(), raw)
        self.assertEqual(planning_advice((programme, programme.items[0])), "")
        self.assertEqual(planning_advice(None), "")

    def test_v11_roundtrip_is_lossless_and_strategy_changes_identity(self):
        raw = example()
        programme = self.compile(raw)
        self.assertEqual(programme.to_input(), raw)
        self.assertEqual(self.compile(programme.to_input()).sha256, programme.sha256)
        for change in (lambda r: r["strategy"]["candidate"].update(mechanism="Use a different local mechanism."),
                       lambda r: r["strategy"]["source"].update(context_identity="c" * 64),
                       lambda r: r["strategy"]["claims"][0].update(statement="The opener sometimes unmounts.")):
            changed = deepcopy(raw)
            change(changed)
            self.assertNotEqual(self.compile(changed).sha256, programme.sha256)
        exported = programme.to_input()
        exported["strategy"]["candidate"]["mechanism"] = "changed"
        self.assertEqual(programme.strategy, raw["strategy"])

    def test_advice_is_not_rendered_as_scope_or_acceptance(self):
        old, new = self.compile(programme_fixture.example()), self.compile(example())
        old_title, old_body = old.render(old.items[0], {})
        title, body = new.render(new.items[0], {})
        self.assertEqual(title, old_title)
        self.assertEqual(body.split("\n", 1)[1], old_body.split("\n", 1)[1])
        self.assertNotIn("STRATEGY_SENTINEL", body)
        advice = planning_advice((new, new.items[0]))
        self.assertIn("STRATEGY_SENTINEL", advice)
        self.assertIn("UNPROVEN", advice)
        self.assertIn("historical exploration", advice)
        self.assertIn('"id": "mounted"', advice)
        self.assertIn('"id": "focus"', advice)

    def test_self_certification_unknown_fields_and_scope_expansion_are_refused(self):
        changes = [lambda s: s.update(qualification_status="PROVEN"),
                   lambda s: s.update(proof_reuse_allowed=True),
                   lambda s: s.update(spec_sha256="c" * 64),
                   lambda s: s.update(approval=True),
                   lambda s: s["source"].update(recommendation_sha256="unknown"),
                   lambda s: s["claims"][0].update(acceptance=["AC99"]),
                   lambda s: s["claims"][0].update(status="challenged"),
                   lambda s: s["claims"][0].update(status="invalidated"),
                   lambda s: s["candidate"].update(mechanism="Fixes #189"),
                   lambda s: s["candidate"].update(authority="qualified")]
        for change in changes:
            raw = example()
            change(raw["strategy"])
            with self.subTest(strategy=raw["strategy"]), self.assertRaises(ProgrammeRefused):
                self.compile(raw)

    def test_missing_ancestors_cycles_unrelated_claims_and_oversize_refuse(self):
        changes = [lambda s: s["claims"].pop(0),
                   lambda s: s["claims"][0].update(depends_on=["focus"]),
                   lambda s: s["candidate"].update(claim_ids=["missing"]),
                   lambda s: s["claims"].append({**s["claims"][0], "id": "unrelated"}),
                   lambda s: s.update(remaining_uncertainty=["x" * 2000] * 20)]
        for change in changes:
            raw = example()
            change(raw["strategy"])
            with self.subTest(change=change), self.assertRaises(ProgrammeRefused):
                self.compile(raw)
        raw = example()
        raw["version"] = "1.0"
        with self.assertRaises(ProgrammeRefused):
            self.compile(raw)
        raw = example()
        del raw["strategy"]
        with self.assertRaises(ProgrammeRefused):
            self.compile(raw)

    def test_changed_strategy_invalidates_old_issue_membership(self):
        gh = programme_fixture.FakeGitHub()
        gh.source = example()
        queue = ProgrammeQueue(gh, "main")
        queue.sync(Mock())
        issue = gh.issue(1)
        admitted = queue.admit(issue)
        self.assertIn("STRATEGY_SENTINEL", planning_advice(admitted))
        gh.source["strategy"]["candidate"]["mechanism"] = "Use a new mechanism."
        with self.assertRaises(ProgrammeRefused):
            queue.admit(issue)

    def test_runtime_delivers_advice_only_to_initial_planner(self):
        fixture = carry_fixture.CarryInBuildIssueTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        programme = self.compile(example())
        contexts = {}
        with patch("factory_kernel.runtime.ProgrammeQueue.admit", return_value=(programme, programme.items[0])):
            recorder, roles, _, paths = fixture.build(contexts=contexts)
        self.assertEqual(roles, ["plan", "contract", "context", "architecture", "test_author"])
        self.assertIn("STRATEGY_SENTINEL", contexts["plan"])
        for role in roles[1:]:
            self.assertNotIn("STRATEGY_SENTINEL", contexts[role], role)
        self.assertIn("architecture-gate", recorder.events)
        self.assertNotIn("STRATEGY_SENTINEL", (paths.artifacts / "issue.json").read_text())

    def test_bug_investigation_receives_advice_without_polluting_contract_context(self):
        fixture = carry_fixture.CarryInBuildIssueTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        programme = self.compile(example())
        contexts = {}
        with (patch("factory_kernel.runtime.ProgrammeQueue.admit", return_value=(programme, programme.items[0])),
              patch("factory_kernel.runtime.KernelRuntime._is_bug", return_value=True),
              patch("factory_kernel.runtime.KernelRuntime._observe_repro", return_value="independently observed repro")):
            _, roles, _, _ = fixture.build(contexts=contexts)
        self.assertEqual(roles[0], "investigate")
        self.assertIn("STRATEGY_SENTINEL", contexts["investigate"])
        self.assertIn("independently observed repro", contexts["contract"])
        self.assertNotIn("STRATEGY_SENTINEL", contexts["contract"])


class ExplorationStrategyExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = exploration_fixture.ExplorationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.add()
        f.engine.experiment("citations", f.command(f.experiment_request()), principal=OWNER)
        f.recommend()
        f.engine.handoff("citations", f.command({"proposal": f.fixture.request["proposal"]}), principal=OWNER)

    def export(self, include_strategy=True, principal=OWNER):
        f = self.fixture
        version = f.store.snapshot("citations", principal=OWNER)["project_version"]
        return f.engine.prepare_handoff("citations", "lookup", expected_project_version=version,
                                       principal=principal, include_strategy=include_strategy)

    def test_opt_in_export_binds_selected_strategy_without_changing_scope_or_budget(self):
        before = self.fixture.inspect()
        basic, enriched = self.export(False), self.export()
        self.assertEqual(basic["input"]["version"], "1.0")
        self.assertEqual(enriched["input"]["version"], "1.1")
        self.assertEqual(enriched["input"]["spec"], basic["input"]["spec"])
        self.assertEqual(enriched["input"]["proposal"], basic["input"]["proposal"])
        self.assertNotEqual(enriched["programme_sha256"], basic["programme_sha256"])
        strategy = enriched["input"]["strategy"]
        self.assertEqual(strategy["candidate"]["id"], "index")
        self.assertEqual([c["id"] for c in strategy["claims"]], ["index-assumption"])
        self.assertEqual(strategy["qualification_status"], "UNPROVEN")
        self.assertEqual(self.fixture.inspect(), before)
        self.assertEqual(enriched["input_sha256"], sha256_value(enriched["input"]))

    def test_opt_in_does_not_bypass_owner_currency_stop_or_claim_reconsideration(self):
        with self.assertRaises(IntentRefused):
            self.export(principal=WORKER)
        with self.assertRaises(IntentRefused):
            self.export(include_strategy="yes")
        self.fixture.stop.side_effect = RuntimeError("stopped")
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            self.export()
        self.fixture.stop.side_effect = None
        self.fixture.engine.observe_claim("citations", self.fixture.command({"claim_id": "index-assumption",
            "status": "invalidated", "observation": "New workload invalidated the assumption.", "source": "owner"}), principal=OWNER)
        with self.assertRaises(IntentRefused):
            self.export()

    def test_opt_in_export_refuses_changed_repository_context(self):
        self.fixture.context["commit"] = "c" * 40
        self.fixture.rehash()
        with self.assertRaises(IntentRefused):
            self.export()


if __name__ == "__main__":
    unittest.main()
