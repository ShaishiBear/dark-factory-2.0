"""Dependency currency (contract C04): strict protected profiles, currency verdicts in the fixed
order, and a frontier that walks proof-dependence edges only and never returns an all-clear
from an invalid index."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.proof_dependencies import (
    DependencyIndexInvalid,
    DependencyProfile,
    ProfileRefused,
    assess_currency,
    build_dependency_index,
    collect_dependencies,
    invalidated_frontier,
    load_profiles,
)
from factory_kernel.spine import load_policy

ROOT = Path(__file__).parents[2]
PROFILE = DependencyProfile(profile_id="full-closure-v1", version="1.0",
                            required_identities=("candidate_tree", "spine_policy"), live_observers=())


def attestation(**overrides) -> dict:
    fields = {
        "obligation_profile_id": "full-closure-v1",
        "inputs": [{"kind": "exact-tree", "identity": "candidate_tree", "digest": "A", "coverage": "complete"},
                   {"kind": "trusted-policy", "identity": "spine_policy", "digest": "P", "coverage": "complete"}],
        "outcome": {"verdict": "pass", "reason_codes": []},
    }
    fields.update(overrides)
    return fields


def observations(**overrides) -> dict:
    fields = {"record_valid": True, "issuer_valid": True, "subject_match": True, "retained": True,
              "dependencies": {"candidate_tree": "A", "spine_policy": "P"}, "coverage": "complete", "predecessors": {}}
    fields.update(overrides)
    return fields


class ProfileTests(unittest.TestCase):
    def test_the_repository_profiles_name_exactly_the_protected_spine_claims(self):
        policy = load_policy(ROOT / ".factory" / "evidence-spine.json")
        profiles = load_profiles(ROOT / ".factory" / "authority-profiles.json",
                                 spine_claim_ids=[requirement.claim_id for requirement in policy.requirements])
        self.assertEqual(set(profiles.obligations), {requirement.claim_id for requirement in policy.requirements})
        profile = profiles.profile_for("green-proof")
        self.assertIn("candidate_tree", profile.required_identities)
        self.assertIn("program_closure", profile.required_identities)
        self.assertIn("lock:app/backend/uv.lock", profile.required_identities)
        self.assertFalse(profile.narrowing_allowed)
        with self.assertRaises(ProfileRefused):
            profiles.profile_for("vibes")

    def write(self, root: Path, mutate) -> Path:
        raw = json.loads((ROOT / ".factory" / "authority-profiles.json").read_text(encoding="utf-8"))
        mutate(raw)
        path = root / "profiles.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_the_loader_is_strict(self):
        def drop_claim(raw):
            del raw["obligations"]["mutation"]

        def add_claim(raw):
            raw["obligations"]["vibes"] = "full-closure-v1"

        def allow_narrowing(raw):
            raw["profiles"]["full-closure-v1"]["narrowing_allowed"] = True

        def empty_profile(raw):
            raw["profiles"]["full-closure-v1"]["required_identities"] = []

        def unknown_profile(raw):
            raw["obligations"]["mutation"] = "narrow"

        def extra_field(raw):
            raw["exceptions"] = ["mutation"]

        def other_compiler(raw):
            raw["compiler_version"] = "2.0"

        claims = [r.claim_id for r in load_policy(ROOT / ".factory" / "evidence-spine.json").requirements]
        with tempfile.TemporaryDirectory() as tmp:
            for name, mutate in (("drop", drop_claim), ("add", add_claim), ("narrow", allow_narrowing), ("empty", empty_profile),
                                 ("unknown", unknown_profile), ("extra", extra_field), ("compiler", other_compiler)):
                with self.subTest(name), self.assertRaises(ProfileRefused):
                    load_profiles(self.write(Path(tmp), mutate), spine_claim_ids=claims)
            # Without the spine, dropping an obligation is not detectable here; with it, it is.
            load_profiles(self.write(Path(tmp), drop_claim))


class CollectTests(unittest.TestCase):
    def test_an_unobservable_identity_makes_coverage_unknown_not_assumed(self):
        reader = {"spine_policy": "P"}.get
        complete = collect_dependencies("closure", {"candidate_tree": "A"}, reader, profile=PROFILE)
        self.assertEqual((complete.coverage, complete.identities, complete.missing), ("complete", {"candidate_tree": "A", "spine_policy": "P"}, ()))
        partial = collect_dependencies("closure", {}, reader, profile=PROFILE)
        self.assertEqual((partial.coverage, partial.missing), ("unknown", ("candidate_tree",)))
        self.assertNotEqual(complete.sha256(), partial.sha256())
        other = DependencyProfile(profile_id="full-closure-v1", version="1.1", required_identities=PROFILE.required_identities, live_observers=())
        self.assertNotEqual(collect_dependencies("closure", {"candidate_tree": "A"}, reader, profile=other).sha256(), complete.sha256(),
                            "the profile version is part of the dependency-set identity")


class CurrencyTests(unittest.TestCase):
    def test_the_current_case_satisfies_a_success_obligation_only_with_a_passing_verdict(self):
        result = assess_currency(attestation(), observations(), PROFILE)
        self.assertEqual((result.status, result.reason_codes, result.satisfies_obligation), ("current", (), True))
        failed = assess_currency(attestation(outcome={"verdict": "fail", "reason_codes": ["red"]}), observations(), PROFILE)
        self.assertEqual((failed.status, failed.satisfies_obligation), ("current", False), "a current observation of failure")

    def test_missing_is_insufficient_and_changed_is_stale(self):
        missing = assess_currency(attestation(), observations(dependencies={"candidate_tree": "A"}), PROFILE)
        self.assertEqual((missing.status, missing.reason_codes, missing.affected), ("insufficient", ("dependency_missing",), ("spine_policy",)))
        changed = assess_currency(attestation(), observations(dependencies={"candidate_tree": "A", "spine_policy": "Q"}), PROFILE)
        self.assertEqual((changed.status, changed.reason_codes, changed.affected), ("stale", ("dependency_changed",), ("spine_policy",)))
        both = assess_currency(attestation(), observations(dependencies={"spine_policy": "Q"}), PROFILE)
        self.assertEqual((both.status, both.reason_codes), ("insufficient", ("dependency_changed", "dependency_missing")))

    def test_an_identity_the_profile_requires_but_the_attestation_never_declared_is_missing(self):
        wider = DependencyProfile(profile_id="full-closure-v1", version="1.0",
                                  required_identities=("candidate_tree", "spine_policy", "lock:app/backend/uv.lock"), live_observers=())
        result = assess_currency(attestation(), observations(dependencies={"candidate_tree": "A", "spine_policy": "P", "lock:app/backend/uv.lock": "L"}), wider)
        self.assertEqual((result.status, result.affected), ("insufficient", ("lock:app/backend/uv.lock",)))

    def test_unknown_coverage_is_never_a_cache_hit(self):
        result = assess_currency(attestation(), observations(coverage="unknown"), PROFILE)
        self.assertEqual((result.status, result.reason_codes), ("insufficient", ("dependency_coverage_unknown",)))
        partial = attestation(inputs=[{**row, "coverage": "partial"} for row in attestation()["inputs"]])
        self.assertEqual(assess_currency(partial, observations(), PROFILE).status, "insufficient")

    def test_forged_material_is_rejected_and_nothing_after_it_is_consulted(self):
        result = assess_currency(attestation(), observations(issuer_valid=False, dependencies={}, coverage="unknown"), PROFILE)
        self.assertEqual((result.status, result.reason_codes), ("rejected", ("issuer_invalid",)))
        self.assertEqual(assess_currency(attestation(), observations(record_valid=False), PROFILE).reason_codes, ("record_invalid",))
        self.assertEqual(assess_currency(attestation(), observations(subject_match=False), PROFILE).reason_codes, ("subject_mismatch",))
        self.assertEqual(assess_currency(attestation(), observations(), None).reason_codes, ("profile_unknown",))
        other = DependencyProfile(profile_id="narrow", version="1.0", required_identities=("candidate_tree",), live_observers=())
        self.assertEqual(assess_currency(attestation(), observations(), other).reason_codes, ("profile_mismatch",))

    def test_an_observer_that_never_looked_at_the_record_or_subject_is_insufficient_not_a_pass(self):
        for absent, code in (("record_valid", "record_unobserved"), ("subject_match", "subject_unobserved")):
            with self.subTest(absent):
                rows = {k: v for k, v in observations().items() if k != absent}
                result = assess_currency(attestation(), rows, PROFILE)
                self.assertEqual((result.status, result.reason_codes, result.satisfies_obligation), ("insufficient", (code,), False))

    def test_missing_artifacts_are_insufficient_and_name_the_objects(self):
        result = assess_currency(attestation(), observations(retained={"spine/a.json": True, "spine/b.json": False}), PROFILE)
        self.assertEqual((result.status, result.reason_codes, result.affected), ("insufficient", ("artifact_missing",), ("spine/b.json",)))
        self.assertEqual(assess_currency(attestation(), observations(retained=False), PROFILE).status, "insufficient")

    def test_live_observations_follow_the_profile(self):
        live = DependencyProfile(profile_id="full-closure-v1", version="1.0", required_identities=PROFILE.required_identities,
                                 live_observers=("github-pr",))
        self.assertEqual(assess_currency(attestation(), observations(), live).reason_codes, ("live_observation_missing",))
        expired = assess_currency(attestation(), observations(live={"github-pr": {"valid": False}}), live)
        self.assertEqual((expired.status, expired.reason_codes), ("stale", ("live_observation_expired",)))
        self.assertEqual(assess_currency(attestation(), observations(live={"github-pr": {"valid": True}}), live).status, "current")

    def test_predecessor_results_propagate_with_their_own_class(self):
        for status, expected, code in (("stale", "stale", "predecessor_stale"), ("insufficient", "insufficient", "predecessor_insufficient"),
                                       ("rejected", "rejected", "predecessor_rejected"), ("unknown", "insufficient", "predecessor_insufficient")):
            with self.subTest(status):
                result = assess_currency(attestation(), observations(predecessors={"p": status}), PROFILE)
                self.assertEqual((result.status, result.reason_codes, result.affected), (expected, (code,), ("p",)))


class FrontierTests(unittest.TestCase):
    def setUp(self):
        self.verified = {
            "a1": attestation(),
            "a2": attestation(inputs=[{"kind": "trusted-policy", "identity": "spine_policy", "digest": "P", "coverage": "complete"}]),
            "a3": attestation(inputs=[{"kind": "environment", "identity": "toolchain", "digest": "T", "coverage": "complete"}]),
            "a4": attestation(inputs=[{"kind": "environment", "identity": "toolchain", "digest": "T", "coverage": "complete"}]),
        }
        self.index = build_dependency_index(self.verified, [
            {"source": "a3", "target": "a1", "kind": "depends_on"},   # a3 depends on a1
            {"source": "a4", "target": "a3", "kind": "selected"},     # a UI relation, not proof dependence
            {"source": "a1", "target": "a3", "kind": "produced"},
        ])

    def test_changes_propagate_along_depends_on_only_and_terminate_on_cycles(self):
        self.assertEqual(invalidated_frontier(["candidate_tree"], self.verified, self.index), ("a1", "a3"))
        self.assertEqual(invalidated_frontier(["spine_policy"], self.verified, self.index), ("a1", "a2", "a3"))
        self.assertEqual(invalidated_frontier(["toolchain"], self.verified, self.index), ("a3", "a4"))
        self.assertEqual(invalidated_frontier([], self.verified, self.index), ())
        self.assertEqual(invalidated_frontier(["nothing-depends-on-this"], self.verified, self.index), ())
        cyclic = build_dependency_index(self.verified, [{"source": "a3", "target": "a1", "kind": "depends_on"},
                                                        {"source": "a1", "target": "a3", "kind": "depends_on"}])
        self.assertEqual(invalidated_frontier(["candidate_tree"], self.verified, cyclic), ("a1", "a3"))

    def test_an_invalid_index_raises_instead_of_returning_an_all_clear(self):
        cases = {
            "incomplete": {**self.index, "complete": False},
            "unversioned": {**self.index, "version": "0.9"},
            "missing segment": {**self.index, "identities": {k: v for k, v in self.index["identities"].items() if k != "toolchain"}},
            "foreign attribution": {**self.index, "identities": {**self.index["identities"], "toolchain": ["a1", "a3", "a4"]}},
            "unknown edge node": {**self.index, "edges": self.index["edges"] + [{"source": "ghost", "target": "a1", "kind": "depends_on"}]},
            "untyped edge": {**self.index, "edges": [{"source": "a3", "target": "a1"}]},
            "not an index": None,
        }
        for name, index in cases.items():
            with self.subTest(name), self.assertRaises(DependencyIndexInvalid):
                invalidated_frontier(["nothing-depends-on-this"], self.verified, index)


if __name__ == "__main__":
    unittest.main()
