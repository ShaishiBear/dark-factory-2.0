from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.evidence_closure import compile_full_spine
from factory_kernel.provenance import BUILDER_CLAIMS, verify_pack

ROOT = Path(__file__).parents[2]
BASE = "a" * 40
HEAD = "b" * 40
ISSUE = 7


class EvidenceClosureTests(unittest.TestCase):
    def fixture(self, root: Path):
        contract = {
            "version": "2.0",
            "issue": {"number": ISSUE, "title": "Example"},
            "summary": "Example contract summary",
            "behaviors": [{"id": "AC-1", "given": "x", "when": "y", "then": "z", "seam": "svc"}],
            "invariants": [], "out_of_scope": [], "risks": [], "ambiguities": [],
        }
        ch = sha256_value(contract)
        ticket = {"version": "1.0", "issue": ISSUE, "contract_sha256": ch}
        frontier = {
            "version": "1.0", "issue": ISSUE, "ready": True,
            "ticket_sha256": sha256_value(ticket),
        }
        context = {"version": "1.0", "contract_sha256": ch}
        policy = {"version": "1.0", "principles": [], "migrations": [], "debt": []}
        design = {
            "version": "1.0", "contract_sha256": ch,
            "context_sha256": sha256_value(context),
        }
        hashes = {
            "contract": sha256_value(contract),
            "tickets": sha256_value(ticket),
            "frontier": sha256_value(frontier),
            "context": sha256_value(context),
            "architecture-policy": sha256_value(policy),
            "design": sha256_value(design),
        }
        governor = {
            "version": "1.0", "decision": "proceed",
            "policy_sha256": hashes["architecture-policy"],
            "contract_sha256": hashes["contract"],
            "context_sha256": hashes["context"],
            "design_sha256": hashes["design"],
        }
        hashes["architecture-governor"] = sha256_value(governor)
        test_plan = {"version": "1.0", "checkpoints": [{"acceptance_id": "AC-1"}]}
        hashes["test-plan"] = sha256_value(test_plan)
        red = {
            "version": "2.0", "test_commit": "c" * 40,
            "contract_sha256": hashes["contract"], "design_sha256": hashes["design"],
            "files": {"tests/test_acceptance.py": "d" * 64},
            "checkpoints": [{"acceptance_id": "AC-1"}],
            "test_plan_sha256": hashes["test-plan"],
        }
        hashes["red-proof"] = sha256_value(red)
        impact = {"version": "1.0", "head_sha": HEAD, "verdict": "pass"}
        hashes["impact"] = sha256_value(impact)
        drift = {
            "version": "1.0", "base_sha": BASE, "head_sha": HEAD,
            "policy_sha256": hashes["architecture-policy"],
            "design_sha256": hashes["design"], "verdict": "pass",
        }
        hashes["architecture-drift"] = sha256_value(drift)
        conformance = {
            "version": "1.0", "verdict": "conform", "head_sha": HEAD,
            "policy_sha256": hashes["architecture-policy"],
            "contract_sha256": hashes["contract"],
            "context_sha256": hashes["context"],
            "design_sha256": hashes["design"],
            "governor_sha256": hashes["architecture-governor"],
        }
        hashes["architecture-conformance"] = sha256_value(conformance)
        green = dict(red)
        green.update(
            {
                "green_commit": HEAD,
                "green_results": [{"acceptance_id": "AC-1", "exit": 0}],
                "change_impact": {"sha256": hashes["impact"]},
                "architecture_guard": {"sha256": hashes["architecture-drift"]},
                "architecture_builder_sha256": hashes["architecture-conformance"],
            }
        )
        hashes["green-proof"] = sha256_value(green)

        values = {
            "contract": contract, "tickets": ticket, "frontier": frontier, "context": context,
            "architecture-policy": policy, "design": design,
            "architecture-governor": governor, "test-plan": test_plan,
            "red-proof": red, "green-proof": green, "impact": impact,
            "architecture-drift": drift, "architecture-conformance": conformance,
        }
        pack = {
            "version": "1.0", "issue": ISSUE, "base_sha": BASE, "head_sha": HEAD,
            "note_ref": "refs/notes/dark-factory-provenance",
            "artifacts": {
                claim_id: {"source": f"{claim_id}.json", "sha256": sha256_value(value), "content": value}
                for claim_id, value in values.items()
            },
        }
        self.assertEqual(set(pack["artifacts"]), set(BUILDER_CLAIMS))
        verify_pack(pack, expected_head_sha=HEAD, expected_base_sha=BASE, expected_issue=ISSUE)
        builder_root = root / "spine" / "builder"
        builder_root.mkdir(parents=True)
        for claim_id, record in pack["artifacts"].items():
            (builder_root / f"{claim_id}.json").write_bytes(canonical_bytes(record["content"]))

        observed = {
            "e2e_steps": 5,
            "holdout_assertions": 9,
            "mutations_total": 9, "mutations_caught": 9, "mutations_not_injected": 0,
            "mutations_quick_caught": 5, "mutations_independent_caught": 7,
            "mutations_citation_caught": 3, "mutations_security_caught": 3,
            "factory_mutations_total": 59, "factory_mutations_caught": 59,
            "factory_mutations_not_injected": 0,
            "immunity_entries": 3, "immunity_assertions": 7,
            "immunity_sha256": "e" * 64,
            "unit_tests": 781, "static_checks": 5,
        }
        legacy = {
            "version": "5.0", "pr": 42, "issue": ISSUE,
            "base_sha": BASE, "head_sha": HEAD,
            "contract_sha256": hashes["contract"], "contract": {"verdict": "pass"},
            "design_sha256": hashes["design"],
            "proof_sha256": hashes["green-proof"],
            "proof": {"red_replay": [{"acceptance_id": "AC-1"}], "green_replay": [{"acceptance_id": "AC-1", "exit": 0}]},
            "architecture": {"sha256": hashes["architecture-conformance"], "verdict": "conform"},
            "architecture_guard": {"sha256": hashes["architecture-drift"], "verdict": "pass"},
            "architecture_holdout": {"version": "1.0", "verdict": "pass", "convergence": "improves"},
            "security": {"version": "1.0", "verdict": "pass"},
            "harness_sha256": "f" * 64,
            "observed": observed,
        }
        return pack, legacy

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_complete_spine_reaches_100_percent_for_all_required_claims(self, immunity):
        immunity.return_value = {
            "registry_sha256": "e" * 64, "active_entries": 3,
            "assertions": 7, "entry_ids": ["IMM-001", "IMM-002", "IMM-003"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            manifest, index = compile_full_spine(
                repo_root=ROOT, artifact_root=root, legacy_bundle=legacy,
                builder_pack=pack, holdout={"version": "1.0", "verdict": "pass"},
                architecture_holdout={"version": "1.0", "verdict": "pass", "convergence": "improves"},
                pr_number=42,
            )
            self.assertEqual(index["completion_level"], 100)
            self.assertEqual(len(index["claims"]), 21)
            self.assertTrue(all(row["completion_level"] == 100 for row in index["claims"]))
            self.assertEqual(index["manifest_sha256"], manifest.sha256())
            self.assertTrue((root / "spine/run-manifest.json").is_file())
            self.assertTrue((root / "spine/evidence-index.json").is_file())

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_tampered_context_cannot_close_spine(self, immunity):
        immunity.return_value = {
            "registry_sha256": "e" * 64, "active_entries": 3,
            "assertions": 7, "entry_ids": ["IMM-001", "IMM-002", "IMM-003"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            pack["artifacts"]["context"]["content"]["tampered"] = True
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                compile_full_spine(
                    repo_root=ROOT, artifact_root=root, legacy_bundle=legacy,
                    builder_pack=pack, holdout={"version": "1.0", "verdict": "pass"},
                    architecture_holdout={"version": "1.0", "verdict": "pass", "convergence": "improves"},
                    pr_number=42,
                )


if __name__ == "__main__":
    unittest.main()
