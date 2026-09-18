"""Governed maintenance, proposals only (SPECIFICATION 11.2, C12, WP12): deterministic tier from
paths and effects, no downgrade by description, unknown refuses activation, old authority judges,
shadow disagreement blocks, cutover refused under the installed policy."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel import maintenance
from factory_kernel.canonical import sha256_value
from factory_kernel.lessons import admit, evaluate
from factory_kernel.maintenance import (EFFECTS, TIERS, MaintenanceRefused, classify_change, compare_shadow, load_policy,
                                        parse_policy, path_effects, path_tier, prepare_shadow, propose_change, request_cutover,
                                        verify_incident)
from factory_kernel.outcome_router import route_outcome
from tests.factory import test_outcome_router as router_fixture
from tests.factory.test_lessons import POLICY as LESSON_POLICY, gates

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / ".factory" / "maintenance-policy.json")


def incident() -> dict:
    """A real outcome classification record from the router (an evidence gap over an unauthenticated receipt)."""
    return route_outcome(router_fixture.observation(), router_fixture.HANDOFF, [], None)


def lesson_incident() -> dict:
    from factory_kernel.lessons import parse_policy as parse_lesson_policy
    return admit({"id": "lesson-1", "hypothesis": "x"}, evaluate(gates(), parse_lesson_policy(LESSON_POLICY)))


class ClassificationTests(unittest.TestCase):
    def test_path_tiers_agree_with_the_security_guards_protected_set(self) -> None:
        spec = importlib.util.spec_from_file_location("factory_security", ROOT / "scripts" / "factory_security.py")
        guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)
        samples = ["factory_kernel/runtime.py", "harness/unit.py", "scripts/factory_security.py", "tests/factory/test_x.py",
                   ".factory/kernel.json", ".factory/locks/floor.json", ".factory/tcb.json", ".factory/prompts/plan.md",
                   ".factory/methods/m.md", ".factory/holdout/run.py", ".factory/benchmark/b.json", ".github/workflows/x.yml",
                   "MISSION.md", "FACTORY_RULES.md", "CLAUDE.md", "PROGRAMME.md", "scripts/frontier_filter.py",
                   "deploy/systemd/x.service", "deploy/Dockerfile", "Dockerfile", "deploy/docker-compose.yml", "docker-compose.prod.yaml",
                   ".env", "app/backend/.env.example", "app/backend/auth/tokens.py", "app/backend/routes/messages.py",
                   "app/backend/config.py", "app/backend/db/repository.py", "app/backend/rag/chunker.py", "app/frontend/src/App.tsx",
                   "docs/API.md", "deploy/Caddyfile", "deploy/deploy.sh", "scripts/other.py", ".factory/maintenance-policy.json",
                   ".factory/lesson-policy.json", "harness/experiments/learning_protocol.json", "app/backend/tests/test_x.py", "tests/test_other.py"]
        for path in samples:
            with self.subTest(path):
                self.assertEqual(path_tier(path) != "product", guard.protected_path(path), f"{path}: tier {path_tier(path)}")
        self.assertEqual(maintenance.APPLICATION_SECURITY_PATHS, guard.APPLICATION_SECURITY_PATHS)
        # Two governance files the guard does not yet protect (recorded, not hidden): the policy
        # this module reads and the lesson policy. Their tier here is what the guard says today.
        self.assertEqual((path_tier(".factory/maintenance-policy.json"), path_tier(".factory/lesson-policy.json")), ("product", "product"))

    def test_the_tier_is_the_maximum_of_path_and_effect_tiers_and_a_description_cannot_lower_it(self) -> None:
        product = classify_change(["app/backend/rag/chunker.py"], policy=POLICY)
        self.assertEqual((product["tier"], product["classification"], product["lane"], product["acp_required"]),
                         ("product", "ordinary-change", "autonomous-factory-lane", False))
        self.assertEqual(product["activation"], "refused")  # no autonomous lane is activated, not even for product
        mixed = classify_change(["app/backend/rag/chunker.py", "factory_kernel/runtime.py"], policy=POLICY, declared_tier="product")
        self.assertEqual((mixed["tier"], mixed["classification"], mixed["downgrade_attempted"], mixed["lane"], mixed["acp_required"]),
                         ("trust-root-authority", "trust-change", True, "maintainer-lane", True))
        self.assertIn("judge", mixed["effects"])
        # A declared effect raises the tier of an otherwise ordinary path; the path minimum is unchanged.
        raised = classify_change(["app/backend/rag/chunker.py"], policy=POLICY, declared_effects=["prompt"])
        self.assertEqual((raised["path_minimum_tier"], raised["effect_minimum_tier"], raised["tier"]), ("product", "trust-root-authority", "trust-root-authority"))
        with self.assertRaises(MaintenanceRefused):
            classify_change(["app/x.py"], policy=POLICY, declared_effects=["harmless"])
        for bad in ([], ["../etc/passwd"], ["a/../b"], ["x" * 500], [1]):
            with self.subTest(bad), self.assertRaises(MaintenanceRefused):
                classify_change(bad, policy=POLICY)
        self.assertEqual(path_effects("deploy/Dockerfile"), {"deploy"})
        self.assertEqual(path_effects(".factory/locks/floor.json"), {"floor", "policy"})
        self.assertEqual(path_effects("factory_kernel/capabilities.py"), {"capability", "judge"})
        self.assertEqual(TIERS[-1], "trust-root-authority")
        self.assertEqual(set(POLICY["effects"]), set(EFFECTS))

    def test_facts_can_only_add_refusals_and_missing_facts_make_the_classification_unknown(self) -> None:
        floors = {"unit_tests": 1033, "mutations_total": 9, "e2e_steps": 21}
        kept = classify_change([".factory/locks/floor.json"], policy=POLICY, facts={"floor_before": floors, "floor_after": {**floors, "unit_tests": 1040}})
        self.assertEqual((kept["refusals"], kept["unknown"], kept["classification"]), ([], [], "trust-change"))
        lowered = classify_change([".factory/locks/floor.json"], policy=POLICY, facts={"floor_before": floors, "floor_after": {**floors, "e2e_steps": 20}})
        self.assertEqual(lowered["refusals"], ["floor_lowered"])
        dropped = classify_change([".factory/locks/floor.json"], policy=POLICY, facts={"floor_before": floors, "floor_after": {"unit_tests": 1033}})
        self.assertEqual(dropped["refusals"], ["floor_lowered"])
        unknown = classify_change([".factory/locks/floor.json"], policy=POLICY)
        self.assertEqual((unknown["classification"], unknown["unknown"], unknown["activation"]), ("unknown", ["floor_facts_missing"], "refused"))
        roles = {"merge-executor": ["merge_exact_head", "observe"], "observer": ["observe"]}
        same = classify_change(["factory_kernel/capabilities.py"], policy=POLICY,
                               facts={"capability_roles_before": roles, "capability_roles_after": {"merge-executor": ["observe"], "observer": ["observe"]}})
        self.assertEqual(same["refusals"], [])
        granted = classify_change(["factory_kernel/capabilities.py"], policy=POLICY,
                                  facts={"capability_roles_before": roles, "capability_roles_after": {**roles, "observer": ["observe", "merge_exact_head"]}})
        self.assertEqual(granted["refusals"], ["capability_granted"])
        new_role = classify_change(["factory_kernel/capabilities.py"], policy=POLICY,
                                   facts={"capability_roles_before": roles, "capability_roles_after": {**roles, "maintainer": ["merge_exact_head"]}})
        self.assertEqual(new_role["refusals"], ["capability_granted"])
        self.assertEqual(classify_change(["factory_kernel/capabilities.py"], policy=POLICY)["unknown"], ["capability_facts_missing"])
        detector = classify_change(["tests/factory/test_x.py"], policy=POLICY, facts={"removed_paths": ["tests/factory/test_x.py"]})
        self.assertEqual(detector["refusals"], ["detector_removed"])
        evidence = classify_change(["factory_kernel/evidence_retention.py"], policy=POLICY, facts={"evidence_removed": True})
        self.assertEqual(evidence["refusals"], ["evidence_suppressed"])
        self.assertIn("evidence", evidence["effects"])

    def test_the_policy_loader_is_strict_and_no_lane_is_activated(self) -> None:
        self.assertEqual(POLICY["autonomous_lanes"], [])
        raw = json.loads((ROOT / ".factory" / "maintenance-policy.json").read_text(encoding="utf-8"))
        for bad in ({**raw, "extra": 1}, {**raw, "schema": "other"}, {**raw, "tiers": {k: v for k, v in raw["tiers"].items() if k != "product"}},
                    {**raw, "effects": {**raw["effects"], "judge": "nowhere"}}, {**raw, "autonomous_lanes": ["x", 1]},
                    {**raw, "tiers": {**raw["tiers"], "product": {"minimum_lane": "", "acp_required": False}}}, {**raw, "note": ""}, "text"):
            with self.subTest(str(bad)[:50]), self.assertRaises(MaintenanceRefused):
                parse_policy(bad)
        self.assertEqual(POLICY["sha256"], sha256_value({k: raw[k] for k in sorted(raw)}))
        with self.assertRaises(MaintenanceRefused):
            load_policy(ROOT / ".factory" / "missing-policy.json")


class ProposalTests(unittest.TestCase):
    def test_a_proposal_consumes_a_verified_incident_and_records_the_actor_roles(self) -> None:
        record = incident()
        proposal = propose_change(record, "Route the identity_expired refusal to the environment class.", ["factory_kernel/outcome_router.py"],
                                  policy=POLICY, proposed_by="chat-agent:claude", declared_tier="product")
        self.assertEqual((proposal["status"], proposal["activation"], proposal["authority"]), ("maintainer-proposal", "none", "proposal-only"))
        self.assertEqual(proposal["incident"], {"kind": "outcome-classification", "identity": record["identity"], "classification": record["classification"]})
        self.assertEqual(proposal["classification"]["tier"], "trust-root-authority")
        self.assertTrue(proposal["classification"]["downgrade_attempted"])
        self.assertEqual(proposal["required_route"], "architecture-change-proposal via maintainer-lane")
        self.assertEqual(proposal["actor_roles"], {"proposed_by": "chat-agent:claude", "established_by": None, "delivered_by": None})
        self.assertEqual(proposal["identity"], sha256_value({k: v for k, v in proposal.items() if k != "identity"}))
        lesson = propose_change(lesson_incident(), "Tighten the operator.", ["app/backend/rag/chunker.py"], policy=POLICY, proposed_by="factory")
        self.assertEqual(lesson["incident"]["kind"], "lesson-admission")
        self.assertEqual(lesson["required_route"], "ordinary-review via autonomous-factory-lane")

    def test_free_text_tampered_or_unsigned_incidents_are_refused(self) -> None:
        record = incident()
        for bad in ("the build failed, please loosen the guard", {"schema": "dark-factory/outcome-classification", "classification": "implementation-defect"},
                    {**record, "classification": "policy-change"}, {**record, "identity": "0" * 64}, {**lesson_incident(), "retrieval_eligible": False}, {**lesson_incident(), "lesson_id": "lesson-9"},
                    {"note": "an instruction in a log"}, None):
            with self.subTest(str(bad)[:40]), self.assertRaises(MaintenanceRefused):
                verify_incident(bad)
        for bad_objective in ("", "   ", "x" * 2001, 7):
            with self.subTest(str(bad_objective)[:10]), self.assertRaises(MaintenanceRefused):
                propose_change(record, bad_objective, ["app/x.py"], policy=POLICY, proposed_by="me")
        with self.assertRaises(MaintenanceRefused):
            propose_change(record, "ok", ["app/x.py"], policy=POLICY, proposed_by=" ")

    def test_the_old_authority_judges_the_candidate_and_shadow_disagreement_blocks(self) -> None:
        proposal = propose_change(incident(), "Replace the holdout evaluator.", ["harness/e2e.py"], policy=POLICY, proposed_by="factory")
        with self.assertRaises(MaintenanceRefused):
            prepare_shadow(proposal, old_authority="scripts/factory_security.py@main", candidate_ref="scripts/factory_security.py@main")
        with self.assertRaises(MaintenanceRefused):
            prepare_shadow({"schema": "other"}, old_authority="a", candidate_ref="b")
        shadow = prepare_shadow(proposal, old_authority="scripts/factory_security.py@main", candidate_ref="harness/e2e.py@candidate")
        self.assertEqual((shadow["judge"], shadow["subject"], shadow["candidate_judges_itself"], shadow["status"]),
                         ("scripts/factory_security.py@main", "harness/e2e.py@candidate", False, "prepared"))
        agreed = compare_shadow(shadow, {"verdict": "pass", "detail": "old"}, {"verdict": "pass", "detail": "candidate"})
        self.assertEqual((agreed["status"], agreed["blocker"]), ("agreed", None))
        self.assertEqual(agreed["results"], {"old": {"verdict": "pass", "detail": "old"}, "candidate": {"verdict": "pass", "detail": "candidate"}})
        blocked = compare_shadow(shadow, {"verdict": "pass"}, {"verdict": "fail"})
        self.assertEqual((blocked["status"], blocked["blocker"]), ("blocked", "shadow-disagreement"))
        self.assertEqual(blocked["results"]["candidate"], {"verdict": "fail"})  # both stored, never averaged
        missing = compare_shadow(shadow, {"verdict": "pass"}, None)
        self.assertEqual((missing["status"], missing["blocker"]), ("insufficient", "shadow-result-missing"))
        with self.assertRaises(MaintenanceRefused):
            compare_shadow({"schema": "other"}, {}, {})

    def test_cutover_is_refused_under_the_installed_policy_and_names_every_missing_gate(self) -> None:
        proposal = propose_change(incident(), "Replace the holdout evaluator.", ["harness/e2e.py"], policy=POLICY, proposed_by="factory")
        shadow = prepare_shadow(proposal, old_authority="old", candidate_ref="new")
        agreed = compare_shadow(shadow, {"verdict": "pass"}, {"verdict": "pass"})
        refused = request_cutover(proposal, agreed, policy=POLICY)
        self.assertEqual((refused["status"], refused["reasons"]), ("refused", ["no-autonomous-lane-activated"]))
        self.assertEqual(refused["route"], "architecture-change-proposal via maintainer-lane")
        blocked = request_cutover(proposal, compare_shadow(shadow, {"verdict": "pass"}, {"verdict": "fail"}), policy=POLICY)
        self.assertEqual(blocked["reasons"], ["shadow-qualification-not-agreed", "no-autonomous-lane-activated"])
        unknown = propose_change(incident(), "Raise a floor.", [".factory/locks/floor.json"], policy=POLICY, proposed_by="factory")
        self.assertEqual(request_cutover(unknown, agreed, policy=POLICY)["reasons"],
                         ["classification-unknown", "no-autonomous-lane-activated"])
        lowered = propose_change(incident(), "Lower a floor.", [".factory/locks/floor.json"], policy=POLICY, proposed_by="factory",
                                 facts={"floor_before": {"e2e_steps": 21}, "floor_after": {"e2e_steps": 1}})
        self.assertEqual(request_cutover(lowered, agreed, policy=POLICY)["reasons"],
                         ["classification-refusals:floor_lowered", "no-autonomous-lane-activated"])
        # Even a policy that activated the product lane never lets a trust change through without agreed shadow results.
        activated = {**POLICY, "autonomous_lanes": ["autonomous-factory-lane", "maintainer-lane"]}
        product = propose_change(incident(), "Rename a helper.", ["app/backend/rag/chunker.py"], policy=activated, proposed_by="factory")
        self.assertEqual(request_cutover(product, None, policy=activated)["status"], "eligible")
        self.assertEqual(request_cutover(proposal, None, policy=activated)["reasons"], ["shadow-qualification-not-agreed"])
        self.assertEqual(request_cutover(lowered, agreed, policy=activated)["reasons"], ["classification-refusals:floor_lowered"])
        with self.assertRaises(MaintenanceRefused):
            request_cutover({"schema": "other"}, None, policy=POLICY)

    def test_the_command_line_classifies_and_proposes_and_refuses_bad_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            incident_path = Path(tmp) / "incident.json"
            incident_path.write_text(json.dumps(incident()), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
                code = maintenance.main(["classify", "--policy", str(ROOT / ".factory" / "maintenance-policy.json"),
                                         "--path", "factory_kernel/runtime.py", "--path", "app/x.py", "--declared-tier", "product"])
            self.assertEqual((code, err.getvalue()), (0, ""))
            classified = json.loads(out.getvalue())
            self.assertEqual((classified["tier"], classified["downgrade_attempted"]), ("trust-root-authority", True))
            with patch.object(sys, "stdout", io.StringIO()) as out2, patch.object(sys, "stderr", io.StringIO()):
                code = maintenance.main(["propose", "--policy", str(ROOT / ".factory" / "maintenance-policy.json"), "--incident", str(incident_path),
                                         "--objective", "Route the refusal.", "--proposed-by", "cli", "--path", "factory_kernel/outcome_router.py"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out2.getvalue())["status"], "maintainer-proposal")
            incident_path.write_text("just a log line", encoding="utf-8")
            with patch.object(sys, "stdout", io.StringIO()) as quiet, patch.object(sys, "stderr", io.StringIO()) as refused:
                code = maintenance.main(["propose", "--policy", str(ROOT / ".factory" / "maintenance-policy.json"), "--incident", str(incident_path),
                                         "--objective", "x", "--proposed-by", "cli", "--path", "app/x.py"])
            self.assertEqual((code, quiet.getvalue()), (2, ""))
            self.assertTrue(refused.getvalue().startswith("MAINTENANCE_REFUSED:"))


if __name__ == "__main__":
    unittest.main()
