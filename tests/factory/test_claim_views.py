"""The offline claim views behind `explain-claims` and `plan-obligations`: real intent store,
real programme compilation, real claim compilation, real graph and scheduler, one bounded
record each, no network and no writer. Driven through the CLI entry point."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from factory_kernel import cli
from factory_kernel.canonical import canonical_bytes, sha256_bytes
from factory_kernel.dispatch_plan import DispatchObservation, select_dispatch
from factory_kernel.spine import load_policy
from tests.factory.test_project_graph import HEAD, compiled_claims, intake_events, proof_index

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / ".factory" / "evidence-spine.json")


def run_cli(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with patch.object(sys, "argv", ["python -m factory_kernel", *argv]), contextlib.redirect_stdout(out):
        code = cli.main()
    return code, out.getvalue()


class ClaimViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.tmp / "records"
        # The CLI reads the repository from the kernel configuration; the fixture spec names the
        # test repository, so the configuration loader is stood in for (the views themselves are real).
        repo = "owner/product"
        self.enterContext(patch("factory_kernel.cli.load_config", return_value=SimpleNamespace(repository=repo)))
        self.events = intake_events(self.state)
        from tests.factory.test_claims import programme_input, spec
        from factory_kernel.programme import compile_spec
        self.programme_path = self.tmp / "programme.json"
        self.programme_path.write_bytes(canonical_bytes(programme_input(compiled=compile_spec(spec(), repository=repo))))
        self.claims = compiled_claims()
        self.proof_root = self.tmp / "artifacts"
        (self.proof_root / "spine" / "attestations").mkdir(parents=True)
        index = {"schema": "dark-factory/attestation-index", "schema_version": "1.0", **proof_index(self.claims)}
        (self.proof_root / "spine" / "attestations" / "index.json").write_bytes(canonical_bytes(index))
        self.observations = self.tmp / "observations.json"
        self.observations.write_bytes(canonical_bytes({"items": {"snippet": {"issue": 11, "pr": 5, "head": HEAD}},
                                                       "heads": {"5": HEAD}, "issue_items": {"11": "snippet", "12": "playback"},
                                                       "exact_head_checked": {"5": HEAD}}))
        self.output = self.tmp / "out.json"

    def explain(self, *extra: str) -> tuple[int, str, dict | None]:
        code, out = run_cli("explain-claims", "--state-dir", str(self.state), "--owner", "maintainer", "--project", "citations",
                            "--output", str(self.output), *extra)
        record = json.loads(self.output.read_text(encoding="utf-8")) if self.output.exists() else None
        return code, out, record

    def test_explain_claims_projects_requirements_claims_and_the_graph_without_effects(self):
        before = sorted(p.name for p in self.state.rglob("*"))
        code, out, record = self.explain("--programme", str(self.programme_path), "--proof", str(self.proof_root),
                                         "--observations", str(self.observations))
        self.assertEqual(code, 0, out)
        self.assertIn("FACTORY_CLAIMS project=citations version=3 requirements=2", out)
        self.assertEqual((record["schema"], record["authority"], record["gaps"]), ("dark-factory/claim-explanation", "projection-only", []))
        self.assertEqual(len(record["requirements"]), 2)
        self.assertEqual(record["claim_set_digest"], self.claims.digest())
        self.assertEqual(set(record["claim_status"]), {c.key for c in self.claims.claims})
        graph = record["graph"]
        self.assertEqual(graph["counts"]["attestation"], len(POLICY.requirements))
        self.assertTrue(any(e["relation"] == "certifies" for e in graph["edges"]))
        self.assertEqual(sorted(p.name for p in self.state.rglob("*")), before, "a view writes nothing into the store")
        self.assertIn("sha256=" + sha256_bytes(self.output.read_bytes()), out)

    def test_explain_claims_without_a_programme_names_the_gap_and_still_projects_intent(self):
        code, out, record = self.explain()
        self.assertEqual(code, 0, out)
        self.assertEqual(record["gaps"], ["no-programme-candidate"])
        self.assertEqual(record["claim_set_digest"], None)
        self.assertEqual(record["graph"]["counts"].get("owner-decision"), 1)
        self.assertIn("gaps=no-programme-candidate", out)

    def test_refusals_are_loud_and_write_no_record(self):
        code, out, record = self.explain("--programme", str(self.observations))
        self.assertEqual((code, record), (1, None))
        self.assertIn("FACTORY_CLAIMS_REFUSED", out)
        code, out, record = self.explain("--proof", str(self.tmp))
        self.assertEqual((code, record), (1, None))
        self.assertIn("companion index", out)
        code, out = run_cli("explain-claims", "--state-dir", str(self.state), "--owner", "stranger", "--project", "citations",
                            "--output", str(self.output))
        self.assertEqual(code, 1)
        self.assertIn("FACTORY_CLAIMS_REFUSED", out)

    def plan(self, plan_record: dict, *extra: str) -> tuple[int, str, dict | None]:
        plan_path = self.tmp / "plan.json"
        plan_path.write_bytes(canonical_bytes(plan_record))
        code, out = run_cli("plan-obligations", "--state-dir", str(self.state), "--owner", "maintainer", "--project", "citations",
                            "--programme", str(self.programme_path), "--proof", str(self.proof_root), "--observations", str(self.observations),
                            "--dispatch-plan", str(plan_path), "--output", str(self.output), *extra)
        record = json.loads(self.output.read_text(encoding="utf-8")) if self.output.exists() else None
        return code, out, record

    def test_plan_obligations_compares_shadow_proposals_with_the_recorded_dispatch_decision(self):
        review = ({"number": 5, "updatedAt": "1"},)
        legacy = select_dispatch(DispatchObservation(control_observed=True, stopped=False, fenced=False, reconciliation_required=False,
                                                     review=review, build=({"number": 12, "updatedAt": "2", "labels": []},), budget=True))
        code, out, record = self.plan(legacy.record())
        self.assertEqual(code, 0, out)
        self.assertEqual((record["schema"], record["authority"]), ("dark-factory/obligation-plan", "shadow-record"))
        kinds = {(p["kind"], (p["subject"] or {}).get("pr"), (p["subject"] or {}).get("issue")) for p in record["proposals"]}
        self.assertIn(("validate-pr", 5, None), kinds)
        self.assertIn(("merge-pr", 5, None), kinds, "the retained companion index gives PR 5 a complete current closure")
        merge = next(p for p in record["proposals"] if p["kind"] == "merge-pr")
        self.assertEqual(len(merge["prerequisite_attestation_ids"]), len(POLICY.requirements))
        self.assertIn(("build-issue", None, 12), kinds, "playback's predecessor snippet is proved on PR 5")
        self.assertEqual(record["comparison"]["classification"], "agree")
        self.assertIn("classification=agree legacy=validate-pr", out)
        stopped = select_dispatch(DispatchObservation(control_observed=True, stopped=True, fenced=False, reconciliation_required=False, review=review))
        code, out, record = self.plan(stopped.record())
        self.assertEqual((code, record["proposals"], record["comparison"]["classification"]), (0, [], "agree"))
        self.assertTrue(all(b["reason_codes"] == ["stopped"] for b in record["blocked"]))

    def test_plan_obligations_refuses_a_record_that_is_not_a_dispatch_plan(self):
        code, out, record = self.plan({"schema": "something-else"})
        self.assertEqual((code, record), (1, None))
        self.assertIn("FACTORY_OBLIGATIONS_REFUSED", out)


if __name__ == "__main__":
    unittest.main()
