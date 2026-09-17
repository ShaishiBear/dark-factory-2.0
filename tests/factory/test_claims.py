"""Claims: stable identities, protected obligations, typed edges and a derived status (3.3, C03).

WP04 acceptance pinned here: cycles, orphan tasks, unowned acceptance, cross-spec claims,
forged owner origin, claim self-certification and stale predecessor propagation refuse or
degrade; an unchanged unrelated assumption reopens nothing; the programme's legacy hash is
untouched by binding.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from factory_kernel.canonical import sha256_bytes, sha256_value
from factory_kernel.claims import (
    ClaimBinding, ClaimBindings, ClaimDefinition, ClaimRefused, ClaimSet, Edge, bind_implementation_claims,
    bind_programme_claims, claim_status, compile_claims, compile_exploratory_claims, compile_requirement_claims,
    requirement_key, validate_claim_set,
)
from factory_kernel.code_subjects import MemoryTreeReader, index_source, resolve_span
from factory_kernel.programme import compile_programme, compile_spec
from factory_kernel.spine import ClaimRequirement, SpinePolicy, load_policy

ROOT = Path(__file__).resolve().parents[2]
REPO = "owner/product"
POLICY = load_policy(ROOT / ".factory" / "evidence-spine.json")


def spec(revision=1, repository=REPO):
    return {"id": "citations", "revision": revision, "repository": repository, "title": "Inspect citations",
            "outcome": "Viewers can inspect cited words.",
            "requirements": [{"id": "R1", "acceptance": [{"id": "AC1", "text": "Opening a citation shows its snippet."},
                                                         {"id": "AC2", "text": "Playback keeps running."}]}],
            "constraints": ["Preserve video playback."], "non_goals": ["No sharing."]}


def programme_input(items=None, compiled=None):
    compiled = compiled or compile_spec(spec(), repository=REPO)
    items = items or [{"id": "snippet", "acceptance": ["AC1"], "blocked_by": []},
                      {"id": "playback", "acceptance": ["AC2"], "blocked_by": ["snippet"]}]
    return {"version": "1.0", "spec": compiled, "app_login": "factory[bot]",
            "proposal": {"spec_sha256": sha256_value(compiled), "items": items}}


class RequirementIdentityTests(unittest.TestCase):
    def setUp(self):
        self.spec = compile_spec(spec(), repository=REPO)
        self.requirements = compile_requirement_claims(self.spec, repository_id="1341036238", project="citations")

    def test_keys_are_deterministic_namespaced_and_scope_bound(self):
        again = compile_requirement_claims(self.spec, repository_id="1341036238", project="citations")
        self.assertEqual(self.requirements, again)
        self.assertEqual([c.acceptance_ids for c in self.requirements.claims], [("AC1",), ("AC2",)])
        key = requirement_key(repository_id="1341036238", project="citations", spec_sha256=sha256_value(self.spec), acceptance_id="AC1")
        self.assertEqual(self.requirements.keys["AC1"], key)
        other_project = compile_requirement_claims(self.spec, repository_id="1341036238", project="other")
        self.assertNotEqual(other_project.keys["AC1"], key, "same wording in another scope is another claim")
        other_spec = compile_requirement_claims(compile_spec(spec(revision=2), repository=REPO), repository_id="1341036238", project="citations")
        self.assertNotEqual(other_spec.keys["AC1"], key)
        self.assertEqual(self.requirements.claims[0].kind, "requirement")
        self.assertIsNone(self.requirements.claims[0].authority_profile)
        self.assertTrue(self.requirements.claims[0].source_ref.startswith("spec:"))

    def test_approval_is_the_owners_recorded_decision_not_a_boolean(self):
        approval = {"actor": {"identity": "maintainer", "role": "owner"}, "spec": self.spec, "spec_sha256": sha256_value(self.spec)}
        compile_requirement_claims(self.spec, repository_id="1", project="citations", approval=approval)
        for forged in ({**approval, "actor": {"identity": "intake-agent", "role": "proposal"}},
                       {**approval, "spec_sha256": "0" * 64}, {**approval, "spec": spec(revision=2)},
                       {**approval, "actor": {"role": "owner"}}, {"approved": True}):
            with self.subTest(forged=forged), self.assertRaises(ClaimRefused):
                compile_requirement_claims(self.spec, repository_id="1", project="citations", approval=forged)


class ExploratoryTests(unittest.TestCase):
    def setUp(self):
        self.spec = compile_spec(spec(), repository=REPO)
        self.requirements = compile_requirement_claims(self.spec, repository_id="1", project="citations")
        self.hypotheses = {
            "scan-assumption": {"id": "scan-assumption", "statement": "A scan is fast enough.", "kind": "assumption",
                                "depends_on": [], "acceptance": ["AC1"], "status": "active", "spec_sha256": sha256_value(self.spec)},
            "index-assumption": {"id": "index-assumption", "statement": "An index pays off.", "kind": "assumption",
                                 "depends_on": ["scan-assumption"], "acceptance": ["AC1", "AC2"], "status": "invalidated",
                                 "spec_sha256": sha256_value(self.spec)},
        }

    def test_assumptions_bind_to_requirement_keys_and_keep_their_status(self):
        result = compile_exploratory_claims(self.requirements, self.hypotheses)
        self.assertEqual([c.kind for c in result.claims], ["assumption", "assumption"])
        index = next(c for c in result.claims if c.source_ref == "exploration:index-assumption")
        self.assertIn(self.requirements.keys["AC2"], index.depends_on)
        self.assertEqual(result.statuses[index.key], "invalidated")
        self.assertIn(Edge("refines", index.key, self.requirements.keys["AC1"]), result.edges)
        self.assertEqual(sum(1 for e in result.edges if e.relation == "depends_on"), 1)

    def test_cross_spec_unknown_dependency_and_cycles_refuse_deterministically(self):
        foreign = deepcopy(self.hypotheses)
        foreign["scan-assumption"]["spec_sha256"] = "0" * 64
        with self.assertRaisesRegex(ClaimRefused, "another spec"):
            compile_exploratory_claims(self.requirements, foreign)
        missing = deepcopy(self.hypotheses)
        missing["index-assumption"]["depends_on"] = ["ghost"]
        with self.assertRaisesRegex(ClaimRefused, "unrecorded"):
            compile_exploratory_claims(self.requirements, missing)
        cyclic = deepcopy(self.hypotheses)
        cyclic["scan-assumption"]["depends_on"] = ["index-assumption"]
        with self.assertRaisesRegex(ClaimRefused, r"dependency cycle: \[") as ctx:
            compile_exploratory_claims(self.requirements, cyclic)
        first = str(ctx.exception)
        with self.assertRaises(ClaimRefused) as again:
            compile_exploratory_claims(self.requirements, cyclic)
        self.assertEqual(first, str(again.exception), "cycle members are reported in a deterministic order")


class ProgrammeBindingTests(unittest.TestCase):
    def setUp(self):
        self.spec = compile_spec(spec(), repository=REPO)
        self.requirements = compile_requirement_claims(self.spec, repository_id="1", project="citations")
        self.programme = compile_programme(programme_input(compiled=self.spec), repository=REPO)

    def test_complete_binding_covers_every_criterion_with_protected_obligations(self):
        before = self.programme.sha256
        claim_set = bind_programme_claims(self.requirements, self.programme, None, POLICY)
        self.assertEqual(self.programme.sha256, before, "binding never touches the legacy programme identity")
        obligations = [c for c in claim_set.claims if c.kind == "proof-obligation"]
        self.assertEqual(len(obligations), 2 * len(POLICY.requirements))
        self.assertEqual({c.authority_profile for c in obligations}, {r.claim_id for r in POLICY.requirements})
        self.assertEqual(set(claim_set.item_obligations), {"snippet", "playback"})
        # The dependent item's obligations depend on the predecessor's final-evidence obligations.
        playback = claim_set.by_key()[claim_set.item_obligations["playback"][0]]
        snippet_final = {claim_set.by_key()[k].authority_profile for k in claim_set.item_obligations["snippet"]
                         if claim_set.by_key()[k].authority_profile in {r.claim_id for r in POLICY.requirements if r.final_evidence_required}}
        self.assertTrue(snippet_final)
        self.assertTrue(any(claim_set.by_key()[d].authority_profile in snippet_final and d in claim_set.item_obligations["snippet"]
                            for d in playback.depends_on))
        relations = {e.relation for e in claim_set.edges}
        self.assertEqual(relations, {"supported_by", "depends_on"})
        self.assertEqual(claim_set.digest(), bind_programme_claims(self.requirements, self.programme, None, POLICY).digest())
        validate_claim_set(claim_set, POLICY)
        composed, exploratory = compile_claims(self.spec, self.programme, None, POLICY, repository_id="1", project="citations")
        self.assertEqual(composed.digest(), claim_set.digest())
        self.assertEqual(exploratory.claims, ())

    def test_cross_spec_orphan_unowned_unknown_blocker_and_cycle_refuse(self):
        other = compile_requirement_claims(compile_spec(spec(revision=2), repository=REPO), repository_id="1", project="citations")
        with self.assertRaisesRegex(ClaimRefused, "different spec"):
            bind_programme_claims(other, self.programme, None, POLICY)
        fake = lambda items: SimpleNamespace(spec=self.spec, items=tuple(items), sha256="f" * 64)  # noqa: E731
        with self.assertRaisesRegex(ClaimRefused, "orphan"):
            bind_programme_claims(self.requirements, fake([{"id": "a", "acceptance": [], "blocked_by": []},
                                                            {"id": "b", "acceptance": ["AC1", "AC2"], "blocked_by": []}]), None, POLICY)
        with self.assertRaisesRegex(ClaimRefused, "without assigned work"):
            bind_programme_claims(self.requirements, fake([{"id": "a", "acceptance": ["AC1"], "blocked_by": []}]), None, POLICY)
        with self.assertRaisesRegex(ClaimRefused, "outside the approved spec"):
            bind_programme_claims(self.requirements, fake([{"id": "a", "acceptance": ["AC1", "AC9"], "blocked_by": []},
                                                            {"id": "b", "acceptance": ["AC2"], "blocked_by": []}]), None, POLICY)
        with self.assertRaisesRegex(ClaimRefused, "owned by two"):
            bind_programme_claims(self.requirements, fake([{"id": "a", "acceptance": ["AC1"], "blocked_by": []},
                                                            {"id": "b", "acceptance": ["AC1", "AC2"], "blocked_by": []}]), None, POLICY)
        with self.assertRaisesRegex(ClaimRefused, "unknown item"):
            bind_programme_claims(self.requirements, fake([{"id": "a", "acceptance": ["AC1"], "blocked_by": ["zz"]},
                                                            {"id": "b", "acceptance": ["AC2"], "blocked_by": []}]), None, POLICY)
        with self.assertRaisesRegex(ClaimRefused, r"programme dependency cycle: \['a', 'b'\]"):
            bind_programme_claims(self.requirements, fake([{"id": "a", "acceptance": ["AC1"], "blocked_by": ["b"]},
                                                            {"id": "b", "acceptance": ["AC2"], "blocked_by": ["a"]}]), None, POLICY)

    def test_strategy_and_policy_cannot_weaken_or_self_certify(self):
        strategy = {"spec_sha256": sha256_value(self.spec), "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}
        bind_programme_claims(self.requirements, self.programme, strategy, POLICY)
        for bad in ({**strategy, "spec_sha256": "0" * 64}, {**strategy, "qualification_status": "PROVEN"},
                    {**strategy, "proof_reuse_allowed": True}):
            with self.subTest(bad=bad), self.assertRaises(ClaimRefused):
                bind_programme_claims(self.requirements, self.programme, bad, POLICY)
        broken = SpinePolicy("1.0", (ClaimRequirement("contract", "spec", ("ghost",), True, True, False, True),), {"version": "1.0"})
        with self.assertRaisesRegex(ClaimRefused, "unknown 'ghost'"):
            bind_programme_claims(self.requirements, self.programme, None, broken)
        with self.assertRaisesRegex(ClaimRefused, "no obligations"):
            bind_programme_claims(self.requirements, self.programme, None, SpinePolicy("1.0", (), {"version": "1.0"}))
        claim_set = bind_programme_claims(self.requirements, self.programme, None, POLICY)
        forged = ClaimSet(claim_set.repository_id, claim_set.project, claim_set.spec_sha256, claim_set.programme_sha256,
                          claim_set.claims + (ClaimDefinition("a" * 64, "proof-obligation", "citations", claim_set.spec_sha256,
                                                              "weaker", "model:invented", (), ("AC1",), "model-says-ok"),),
                          claim_set.edges, claim_set.item_obligations, claim_set.item_requirements)
        with self.assertRaisesRegex(ClaimRefused, "outside the protected policy"):
            validate_claim_set(forged, POLICY)


class ImplementationBindingTests(unittest.TestCase):
    def setUp(self):
        self.spec = compile_spec(spec(), repository=REPO)
        requirements = compile_requirement_claims(self.spec, repository_id="1", project="citations")
        self.claim_set = bind_programme_claims(requirements, compile_programme(programme_input(compiled=self.spec), repository=REPO), None, POLICY)
        raw = b"def lookup(items, key):\n    return items.get(key)\n"
        index = index_source(MemoryTreeReader({"app/lookup.py": raw}, repository_id="1"))
        self.subject = resolve_span(index, {"path": "app/lookup.py", "byte_start": 0, "byte_end": len(raw) - 1,
                                            "source_bytes_sha256": sha256_bytes(raw)})
        self.key = requirements.keys["AC1"]

    def test_model_proposals_stay_proposed_and_cannot_claim_observation(self):
        proposals = [{"claim_key": self.key, "subject_identity": self.subject.identity(), "relation": "proposed_implementation",
                      "method": "model-trace", "source": "model"},
                     {"claim_key": self.key, "subject_identity": self.subject.identity(), "relation": "observed_implementation",
                      "method": "model-says-so", "source": "model"},
                     {"claim_key": "0" * 64, "subject_identity": self.subject.identity(), "relation": "proposed_implementation"},
                     {"claim_key": self.key, "subject_identity": "not-a-subject", "relation": "proposed_implementation"}]
        observations = [{"claim_key": self.key, "subject_identity": self.subject.identity(), "relation": "observed_implementation",
                         "observer": "trace-runner-v1", "method": "executed-acceptance-test", "coverage": "partial"},
                        {"claim_key": self.key, "subject_identity": self.subject.identity(), "relation": "observed_implementation",
                         "method": "no observer named"}]
        result = bind_implementation_claims(self.claim_set, [self.subject], observations, proposals)
        self.assertIsInstance(result, ClaimBindings)
        standings = [(b.relation, b.standing, b.source) for b in result.for_claim(self.key)]
        self.assertEqual(standings, [("proposed_implementation", "proposed", "model"),
                                     ("observed_implementation", "observed", "observer:trace-runner-v1")])
        reasons = [r["reason"] for r in result.refused]
        self.assertEqual(len(reasons), 4)
        self.assertTrue(any("not permitted for model" in r for r in reasons), "self-certification refused")
        self.assertTrue(any("unknown claim key" in r for r in reasons))
        self.assertTrue(any("unknown code subject" in r for r in reasons))
        self.assertTrue(any("without an observer" in r for r in reasons))
        self.assertIsInstance(result.bindings[0], ClaimBinding)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.spec = compile_spec(spec(), repository=REPO)
        self.requirements = compile_requirement_claims(self.spec, repository_id="1", project="citations")
        self.claim_set = bind_programme_claims(self.requirements, compile_programme(programme_input(compiled=self.spec), repository=REPO), None, POLICY)

    def attest(self, item, verdict="pass", currency="current", only=None):
        return [{"claim_key": key, "verdict": verdict, "currency": currency}
                for key in self.claim_set.item_obligations[item] if only is None or self.claim_set.by_key()[key].authority_profile in only]

    def test_axes_stay_separate_and_predecessor_staleness_propagates(self):
        status = claim_status(self.claim_set, approved=True)
        ac1 = status[self.requirements.keys["AC1"]]
        self.assertEqual((ac1["intent_status"], ac1["proof_status"], ac1["currency"]), ("approved", "insufficient", "mixed"))
        self.assertIn("obligations-incomplete", ac1["reason_codes"])
        first = status[self.claim_set.item_obligations["snippet"][0]]
        self.assertEqual((first["proof_status"], first["reason_codes"]), ("insufficient", ["no-attestation"]))
        established = claim_status(self.claim_set, approved=True, attestations=self.attest("snippet") + self.attest("playback"))
        self.assertEqual(established[self.requirements.keys["AC1"]]["proof_status"], "established")
        self.assertEqual(established[self.requirements.keys["AC2"]]["proof_status"], "established")
        # One stale predecessor obligation: the dependent item's obligations are insufficient with the reason named.
        stale = claim_status(self.claim_set, approved=True,
                             attestations=self.attest("snippet", currency="stale", only={"green-proof"}) +
                             self.attest("snippet", only={r.claim_id for r in POLICY.requirements} - {"green-proof"}) + self.attest("playback"))
        dependent = stale[self.claim_set.item_obligations["playback"][0]]
        self.assertEqual(dependent["proof_status"], "insufficient")
        self.assertTrue(any(code.startswith("predecessor-insufficient") for code in dependent["reason_codes"]))
        self.assertEqual(stale[self.requirements.keys["AC2"]]["proof_status"], "insufficient")
        failed = claim_status(self.claim_set, approved=False, attestations=self.attest("snippet", verdict="fail"))
        self.assertEqual(failed[self.claim_set.item_obligations["snippet"][0]]["reason_codes"], ["observed-failure"])
        self.assertEqual(failed[self.requirements.keys["AC1"]]["intent_status"], "unapproved")

    def test_unrelated_assumption_change_does_not_touch_other_claims(self):
        hypotheses = {"h": {"id": "h", "statement": "s", "kind": "assumption", "depends_on": [], "acceptance": ["AC2"],
                            "status": "active", "spec_sha256": sha256_value(self.spec)}}
        active = compile_exploratory_claims(self.requirements, hypotheses)
        hypotheses["h"]["status"] = "invalidated"
        invalidated = compile_exploratory_claims(self.requirements, hypotheses)
        attestations = self.attest("snippet") + self.attest("playback")
        before = claim_status(self.claim_set, approved=True, exploratory=active, attestations=attestations)
        after = claim_status(self.claim_set, approved=True, exploratory=invalidated, attestations=attestations)
        for key in self.claim_set.by_key():
            self.assertEqual(before[key], after[key], "an assumption's status changes no proof or intent axis")
        self.assertEqual(active.statuses[active.claims[0].key], "active")
        self.assertEqual(invalidated.statuses[invalidated.claims[0].key], "invalidated")


if __name__ == "__main__":
    unittest.main()
