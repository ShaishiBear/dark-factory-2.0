"""Pure project-graph projection (SPECIFICATION section 10, WP04): typed nodes and edges with
source references and currentness, recorded-match separate from trusted currency, a bounded
deterministic snapshot and an exact delta."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.claims import bind_programme_claims, claim_status, compile_requirement_claims
from factory_kernel.frontdoor_intent import IntentStore, Principal
from factory_kernel.programme import compile_programme, compile_spec
from factory_kernel.project_graph import MAX_NODES, RELATIONS, graph_delta, project_graph
from factory_kernel.spine import load_policy
from tests.factory.test_claims import REPO, programme_input, spec

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / ".factory" / "evidence-spine.json")
OWNER = Principal(identity="maintainer", role="owner")
HEAD = "b" * 40


def intake_events(directory: Path) -> list:
    store = IntentStore(directory, repository=REPO, owner="maintainer")
    version = 0
    for key, operation, payload in (
        ("k1", "record-intent", {"wording": "Viewers should inspect citations."}),
        ("k2", "propose-spec", {"spec": spec(), "assumptions": [], "open_questions": [], "technical_questions": []}),
    ):
        store.execute("citations", {"idempotency_key": key, "expected_project_version": version, "operation": operation,
                                    "payload": payload}, principal=OWNER)
        version += 1
    compiled = compile_spec(spec(), repository=REPO)
    store.execute("citations", {"idempotency_key": "k3", "expected_project_version": version, "operation": "approve-spec",
                                "payload": {"draft_version": 2, "spec_sha256": sha256_value(compiled), "wording": "Approved as proposed."}}, principal=OWNER)
    with store._locked("citations") as path:
        return store._read(path)


def compiled_claims():
    compiled = compile_spec(spec(), repository=REPO)
    programme = compile_programme(programme_input(compiled=compiled), repository=REPO)
    requirements = compile_requirement_claims(compiled, repository_id="1341036238", project="citations")
    return bind_programme_claims(requirements, programme, None, POLICY)


def proof_index(claims, *, verified: bool = True, currency: str = "current", head: str = HEAD) -> dict:
    records, verification, shadow = [], {}, {}
    for req in POLICY.requirements:
        aid = sha256_value({"claim": req.claim_id, "head": head})
        records.append({"attestation_id": aid, "claim_key": req.claim_id,
                        "subject": {"candidate_commit": head, "base_commit": "a" * 40}})
        verification[aid] = {"verified": verified}
        shadow[aid] = {"status": currency}
    return {"run_id": "pr-5-evidence-abc", "head_sha": head, "base_sha": "a" * 40, "attestations": records,
            "verification": verification, "shadow_currency": shadow}


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.events = intake_events(self.tmp / "records")
        self.claims = compiled_claims()
        self.statuses = claim_status(self.claims, approved=True)

    def test_nodes_and_typed_edges_carry_sources_and_currentness(self):
        graph = project_graph(self.events, self.claims, None, {"claim_status": self.statuses, "cursor": {"events": 3}})
        self.assertEqual((graph["schema"], graph["authority"], graph["project_version"], graph["observation_cursor"]),
                         ("dark-factory/project-graph", "projection-only", 3, {"events": 3}))
        kinds = graph["counts"]
        self.assertEqual(kinds["requirement"], 2)
        self.assertEqual(kinds["proof-obligation"], 2 * len(POLICY.requirements))
        self.assertEqual((kinds["owner-decision"], kinds["specification"], kinds["programme-item"]), (1, 1, 2))
        by_id = {n["id"]: n for n in graph["nodes"]}
        requirement = next(n for n in graph["nodes"] if n["kind"] == "requirement")
        self.assertTrue(requirement["source_refs"][0].startswith("spec:"))
        self.assertEqual((requirement["intent_status"], requirement["proof_status"], requirement["currentness"]),
                         ("approved", "insufficient", "mixed"), "obligations exist but none is established")
        self.assertIn("obligations-incomplete", requirement["gaps"])
        relations = {e["relation"] for e in graph["edges"]}
        self.assertTrue({"depends_on", "supported_by", "scheduled_as", "decided_by"} <= relations)
        self.assertTrue(relations <= set(RELATIONS))
        # depends_on: source depends on target; playback's obligations depend on snippet's final ones.
        playback = set(self.claims.item_obligations["playback"])
        snippet = set(self.claims.item_obligations["snippet"])
        cross = [e for e in graph["edges"] if e["relation"] == "depends_on"
                 and e["source"][6:] in playback and e["target"][6:] in snippet]
        self.assertTrue(cross)
        self.assertTrue(all(e["source"] in by_id and e["target"] in by_id for e in graph["edges"]))
        item = by_id["item:playback"]
        self.assertEqual((item["currentness"], item["gaps"]), ("unlinked", ["no-issue-observed"]))

    def test_recorded_match_and_trusted_currency_are_separate_labels(self):
        proof = proof_index(self.claims)
        observations = {"claim_status": self.statuses, "items": {"snippet": {"issue": 11, "pr": 5, "head": HEAD}}}
        graph = project_graph(self.events, self.claims, proof, observations)
        attestations = [n for n in graph["nodes"] if n["kind"] == "attestation"]
        self.assertEqual(len(attestations), len(POLICY.requirements))
        self.assertTrue(all(n["recorded_match"] and n["trusted_currency"] == "current" and n["verified"] for n in attestations))
        certifies = [e for e in graph["edges"] if e["relation"] == "certifies"]
        self.assertEqual(len(certifies), len(POLICY.requirements), "each attestation certifies the snippet obligation with its profile")
        self.assertTrue(all(e["target"][6:] in set(self.claims.item_obligations["snippet"]) for e in certifies))
        self.assertTrue(all(e["currentness"] == "current" and e["gaps"] == [] for e in certifies))
        # Stale proof: recorded match stays true, trusted currency does not.
        stale = project_graph(self.events, self.claims, proof_index(self.claims, currency="stale"), observations)
        node = next(n for n in stale["nodes"] if n["kind"] == "attestation")
        self.assertEqual((node["recorded_match"], node["trusted_currency"], node["currentness"]), (True, "stale", "stale"))
        self.assertTrue(all(e["gaps"] == ["not-current-proof"] for e in stale["edges"] if e["relation"] == "certifies"))
        # Unverified proof is never green; unlinked subject is named as a gap.
        unverified = project_graph(self.events, self.claims, proof_index(self.claims, verified=False), observations)
        node = next(n for n in unverified["nodes"] if n["kind"] == "attestation")
        self.assertEqual((node["currentness"], node["gaps"]), ("unverified", ["verification-refused"]))
        unlinked = project_graph(self.events, self.claims, proof_index(self.claims, head="c" * 40), observations)
        node = next(n for n in unlinked["nodes"] if n["kind"] == "attestation")
        self.assertIn("subject-unlinked", node["gaps"])
        self.assertEqual([e for e in unlinked["edges"] if e["relation"] == "certifies"], [])

    def test_a_predicted_candidate_is_never_shown_as_implemented(self):
        graph = project_graph(self.events, self.claims, None, {"components": [
            {"subject_identity": "sha256:abc", "claim_key": self.claims.claims[0].key, "source": "model", "standing": "proposed"},
            {"subject_identity": "sha256:def", "claim_key": self.claims.claims[0].key, "source": "observer:trace", "standing": "observed"},
        ]})
        components = {n["label"]: n for n in graph["nodes"] if n["kind"] == "implementation-component"}
        self.assertEqual(components["sha256:abc"]["currentness"], "proposed")
        self.assertEqual(components["sha256:abc"]["gaps"], ["model-proposed-not-observed"])
        self.assertEqual(components["sha256:def"]["currentness"], "observed")
        implements = {e["source"]: e["currentness"] for e in graph["edges"] if e["relation"] == "implements"}
        self.assertEqual(set(implements.values()), {"proposed", "observed"})

    def test_the_snapshot_is_bounded_and_deterministic(self):
        graph = project_graph(self.events, self.claims, None, {"claim_status": self.statuses})
        again = project_graph(self.events, self.claims, None, {"claim_status": self.statuses})
        self.assertEqual(sha256_value(graph), sha256_value(again))
        self.assertFalse(graph["truncated"])
        self.assertEqual(graph["bounds"]["max_nodes"], MAX_NODES)
        small = project_graph(self.events, self.claims, None, {"claim_status": self.statuses}, max_nodes=5, max_edges=3)
        self.assertTrue(small["truncated"])
        self.assertEqual((len(small["nodes"]), len(small["edges"]) <= 3), (5, True))
        kept = {n["id"] for n in small["nodes"]}
        self.assertTrue(all(e["source"] in kept and e["target"] in kept for e in small["edges"]))

    def test_delta_is_exact(self):
        before = project_graph(self.events, self.claims, None, {"claim_status": self.statuses})
        after = project_graph(self.events, self.claims, proof_index(self.claims),
                              {"claim_status": self.statuses, "items": {"snippet": {"issue": 11, "pr": 5, "head": HEAD}}})
        delta = graph_delta(before, after)
        self.assertEqual((delta["from_version"], delta["to_version"]), (3, 3))
        self.assertEqual(len(delta["added_nodes"]), len(POLICY.requirements) + 1)
        self.assertIn("item:snippet", delta["changed_nodes"])
        self.assertNotIn("item:playback", delta["changed_nodes"])
        self.assertEqual(delta["removed_nodes"], [])
        self.assertTrue(any(key[0] == "certifies" for key in delta["added_edges"]))
        self.assertEqual(graph_delta(after, after)["added_nodes"], [])
        self.assertEqual(graph_delta(after, after)["changed_nodes"], [])

    def test_an_unregistered_relation_cannot_enter_the_graph(self):
        from factory_kernel.project_graph import _edge
        with self.assertRaises(ValueError):
            _edge([], "vibes", "a", "b")


if __name__ == "__main__":
    unittest.main()
