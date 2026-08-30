from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory_kernel.canonical import canonical_bytes, sha256_value
from factory_kernel.evidence_closure import compile_full_spine
from factory_kernel.independence import build_certificate
from factory_kernel.provenance import BUILDER_CLAIMS, verify_pack
from factory_kernel.spine import load_policy

ROOT = Path(__file__).parents[2]
BASE = "a" * 40
HEAD = "b" * 40
ISSUE = 7


IMMUNITY_RESULT = {
    "registry_sha256": "e" * 64,
    "active_entries": 3,
    "assertions": 7,
    "entry_ids": ["IMM-001", "IMM-002", "IMM-003"],
}


def judgement(claim_id: str) -> dict:
    """A blinded pre-code certifier's non-authoritative judgement."""
    return {
        "version": "1.0",
        "verdict": "pass",
        "certifies": claim_id,
        "findings": [],
    }


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
        self.hashes = dict(hashes)
        return pack, legacy

    def certificates(self, *, head: str = HEAD, base: str = BASE, **overrides) -> dict:
        certs = {
            claim_id: build_certificate(
                claim_id=claim_id,
                claim_hashes=self.hashes,
                head_sha=head,
                base_sha=base,
                judgement=judgement(claim_id),
            )
            for claim_id in ("design", "architecture-governor")
        }
        certs.update(overrides)
        return certs

    def close(self, root: Path, pack, legacy, certificates, **kwargs):
        return compile_full_spine(
            repo_root=ROOT,
            artifact_root=root,
            legacy_bundle=legacy,
            builder_pack=pack,
            holdout={"version": "1.0", "verdict": "pass"},
            architecture_holdout={"version": "1.0", "verdict": "pass", "convergence": "improves"},
            pr_number=42,
            independent_certificates=certificates,
            **kwargs,
        )

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_complete_spine_reaches_100_percent_for_all_required_claims(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            manifest, index = self.close(root, pack, legacy, self.certificates())
            self.assertEqual(index["completion_level"], 100)
            self.assertEqual(len(index["claims"]), 21)
            self.assertTrue(all(row["completion_level"] == 100 for row in index["claims"]))
            self.assertEqual(index["manifest_sha256"], manifest.sha256())
            self.assertTrue((root / "spine/run-manifest.json").is_file())
            self.assertTrue((root / "spine/evidence-index.json").is_file())

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_independent_design_certification_satisfies_the_design_claim(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            manifest, _index = self.close(root, pack, legacy, self.certificates())
            design = manifest.claim("design")
            self.assertIsNotNone(design.independent)
            self.assertEqual(design.independent.authority_id, "blinded-design-certifier")
            self.assertNotEqual(design.independent.authority_id, design.producer)
            self.assertNotEqual(
                design.independent.authority_id, design.deterministic.authority_id
            )

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_independent_governor_authority_satisfies_the_governor_claim(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            manifest, _index = self.close(root, pack, legacy, self.certificates())
            governor = manifest.claim("architecture-governor")
            self.assertIsNotNone(governor.independent)
            self.assertEqual(governor.independent.authority_id, "blinded-governor-certifier")
            self.assertNotEqual(
                governor.independent.authority_id,
                manifest.claim("design").independent.authority_id,
            )

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_builder_design_evidence_cannot_fill_its_own_independent_slot(self, immunity):
        """The builder's own design artifact may not certify the design claim."""
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            forged = build_certificate(
                claim_id="design",
                claim_hashes=self.hashes,
                head_sha=HEAD,
                base_sha=BASE,
                judgement=pack["artifacts"]["design"]["content"],
            )
            with self.assertRaisesRegex(ValueError, "builder-produced artifact 'design'"):
                self.close(root, pack, legacy, self.certificates(design=forged))

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_architecture_conformance_cannot_substitute_for_design_certification(self, immunity):
        """The exact historical defect: post-code conformance reused as pre-code independence."""
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            forged = build_certificate(
                claim_id="design",
                claim_hashes=self.hashes,
                head_sha=HEAD,
                base_sha=BASE,
                judgement=pack["artifacts"]["architecture-conformance"]["content"],
            )
            with self.assertRaisesRegex(
                ValueError, "builder-produced artifact 'architecture-conformance'"
            ):
                self.close(root, pack, legacy, self.certificates(design=forged))

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_conformance_cannot_counterfeit_governor_independence(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            forged = build_certificate(
                claim_id="architecture-governor",
                claim_hashes=self.hashes,
                head_sha=HEAD,
                base_sha=BASE,
                judgement=pack["artifacts"]["architecture-conformance"]["content"],
            )
            with self.assertRaisesRegex(
                ValueError, "builder-produced artifact 'architecture-conformance'"
            ):
                self.close(
                    root, pack, legacy, self.certificates(**{"architecture-governor": forged})
                )

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_governor_certificate_cannot_be_aliased_from_the_design_certificate(self, immunity):
        """A design certification is not a governor certification, however well formed."""
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            certs = self.certificates()
            with self.assertRaisesRegex(ValueError, "certifies 'design'"):
                self.close(
                    root, pack, legacy, self.certificates(**{"architecture-governor": certs["design"]})
                )

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_certification_from_another_head_is_rejected(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            with self.assertRaisesRegex(ValueError, "different head SHA"):
                self.close(root, pack, legacy, self.certificates(head="9" * 40))

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_certification_from_another_base_is_rejected(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            with self.assertRaisesRegex(ValueError, "different base SHA"):
                self.close(root, pack, legacy, self.certificates(base="9" * 40))

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_certification_bound_to_a_stale_design_is_rejected(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            stale = self.certificates()
            stale["architecture-governor"]["bindings"]["design"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "not bound to this run's design"):
                self.close(root, pack, legacy, stale)

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_forged_certificate_hashes_are_rejected(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            forged = self.certificates()
            forged["design"]["subject_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "does not certify the design artifact"):
                self.close(root, pack, legacy, forged)

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_missing_independent_certification_fails_spine_closure(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            certs = self.certificates()
            certs.pop("design")
            with self.assertRaisesRegex(ValueError, "no .*independent certificate was supplied"):
                self.close(root, pack, legacy, certs)
            with self.assertRaisesRegex(ValueError, "no .*independent certificate was supplied"):
                self.close(root, pack, legacy, None)

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_certificates_for_unregistered_claims_are_refused(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            certs = self.certificates()
            certs["impact"] = dict(certs["design"])
            with self.assertRaisesRegex(ValueError, "do not accept them: impact"):
                self.close(root, pack, legacy, certs)

    @patch("factory_kernel.evidence_closure.load_policy")
    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_raising_required_authority_cannot_be_bypassed_by_legacy_evidence(
        self, immunity, policy_loader
    ):
        """A newly independence-required claim fails closed until an authority exists for it."""
        immunity.return_value = IMMUNITY_RESULT
        raw = json.loads((ROOT / ".factory" / "evidence-spine.json").read_text(encoding="utf-8"))
        for entry in raw["required_claims"]:
            if entry["id"] == "impact":
                entry["independent_required"] = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy_path = root / "raised-spine.json"
            policy_path.write_text(json.dumps(raw), encoding="utf-8")
            policy_loader.return_value = load_policy(policy_path)
            pack, legacy = self.fixture(root)
            with self.assertRaisesRegex(
                ValueError, "no independent authority is registered for claim 'impact'"
            ):
                self.close(root, pack, legacy, self.certificates())

    @patch("factory_kernel.evidence_closure._load_immunity")
    def test_tampered_context_cannot_close_spine(self, immunity):
        immunity.return_value = IMMUNITY_RESULT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, legacy = self.fixture(root)
            pack["artifacts"]["context"]["content"]["tampered"] = True
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                self.close(root, pack, legacy, self.certificates())


if __name__ == "__main__":
    unittest.main()
