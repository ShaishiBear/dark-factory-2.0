"""The project graph over the real Front Door HTTP application (SPECIFICATION 10, C11, WP10A):
authenticated, read-only, cursor-exact, no side effects, details after authorization."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.frontdoor_http import FrontDoorApplication  # noqa: E402
from factory_kernel import graph_transport  # noqa: E402
from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal  # noqa: E402
from factory_kernel.graph_transport import MAX_DELTA_SPAN, GraphRefused, graph_view, node_details, snapshot_id  # noqa: E402
from factory_kernel.project_graph import graph_delta  # noqa: E402
from tests.factory.test_frontdoor_intent import OWNER, REPO, example_spec  # noqa: E402

TOKEN = "c" * 64
ORIGIN = "https://factory.example"


class ProjectGraphHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = IntentStore(Path(tmp.name), repository=REPO, owner=OWNER.identity)
        self.github = Mock()
        self.github.programme_issues.return_value = []
        self.app = FrontDoorApplication(store=self.store, project="citations", token=TOKEN, origin=ORIGIN,
                                        github=self.github, labels={}, app_login="factory[bot]")

    def call(self, path, *, body=None, method=None, query="", **overrides):
        data = json.dumps(body).encode() if body is not None else b""
        environ = {"REQUEST_METHOD": method or ("POST" if data else "GET"), "PATH_INFO": path,
                   "HTTP_HOST": "factory.example", "HTTP_ORIGIN": ORIGIN, "HTTP_AUTHORIZATION": f"Bearer {TOKEN}",
                   "CONTENT_LENGTH": str(len(data)), "CONTENT_TYPE": "application/json", "QUERY_STRING": query,
                   "wsgi.input": BytesIO(data), **overrides}
        response = {}

        def start(status, headers):
            response.update(status=status, headers=dict(headers))

        raw = b"".join(self.app(environ, start))
        response["body"] = raw
        if response["headers"]["Content-Type"].startswith("application/json"):
            response["json"] = json.loads(raw)
        return response

    def command(self, **changes):
        return {"idempotency_key": "test-command", "expected_project_version": 0, "operation": "record-intent",
                "payload": {"wording": "Show cited words."}, **changes}

    def decide(self) -> int:
        """record-intent, propose-spec, approve-spec: three owner decisions."""
        self.call("/api/commands", body=self.command())
        state = self.call("/api/commands", body=self.command(
            idempotency_key="draft-1", expected_project_version=1, operation="propose-spec",
            payload={"spec": example_spec(), "assumptions": [], "open_questions": [], "technical_questions": []}))["json"]
        self.call("/api/commands", body=self.command(
            idempotency_key="approve", expected_project_version=2, operation="approve-spec",
            payload={"draft_version": 2, "spec_sha256": state["draft"]["spec_sha256"], "wording": "Approved exact draft"}))
        return self.store.snapshot("citations", principal=OWNER)["project_version"]

    def test_the_graph_is_owner_only_and_read_only(self) -> None:
        for authorization in ("", "Bearer wrong", f"Basic {TOKEN}"):
            with self.subTest(authorization):
                self.assertEqual(self.call("/api/project-graph", HTTP_AUTHORIZATION=authorization)["status"], "401 Unauthorized")
                # Authorization before existence: an unknown id under the wrong token is still 401.
                self.assertEqual(self.call("/api/project-graph/details", query="id=claim%3Anope", HTTP_AUTHORIZATION=authorization)["status"],
                                 "401 Unauthorized")
        before = self.store.snapshot("citations", principal=OWNER)
        result = self.call("/api/project-graph")
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual((result["json"]["kind"], result["json"]["project_version"], result["json"]["authority"]), ("snapshot", 0, "projection-only"))
        self.assertEqual(self.store.snapshot("citations", principal=OWNER), before)  # no event created
        self.github.run.assert_not_called()
        self.github.run_as_app.assert_not_called()
        self.assertEqual(result["headers"]["Cache-Control"], "no-store")

    def test_query_parameters_stay_refused_everywhere_else_and_only_the_graph_shape_is_accepted(self) -> None:
        self.assertEqual(self.call("/api/history", query="after_version=0")["status"], "400 Bad Request")
        self.assertEqual(self.call("/api/snapshot", query="id=x")["status"], "400 Bad Request")
        # "²" (superscript two) satisfies str.isdigit() but not int(): a shape refusal, never an exception.
        for bad in ("after_version=-1", "after_version=abc", "after_version=1&id=2", "id=", "cursor=1", "after_version=" + "9" * 13,
                    "after_version=²", "after_version=1²"):
            with self.subTest(bad):
                self.assertEqual(self.call("/api/project-graph", query=bad)["status"], "400 Bad Request")
        # The shape rule is public and runs before authentication: a malformed query is 400 whoever asks,
        # and says nothing about the graph. Well-formed queries reach authentication (401 in the first test).
        self.assertEqual(self.call("/api/project-graph/details", query="id=../../etc", HTTP_AUTHORIZATION="")["status"], "400 Bad Request")
        self.assertEqual(self.call("/api/project-graph", query="after_version=²", HTTP_AUTHORIZATION="")["status"], "400 Bad Request")
        self.assertEqual(self.call("/api/project-graph/details")["status"], "400 Bad Request")  # no id
        self.assertEqual(self.call("/api/project-graph/details", query="id=../../etc")["status"], "400 Bad Request")
        self.assertEqual(self.call("/api/project-graph/details", query="id=decision%3Avé")["status"], "400 Bad Request")  # ASCII only

    def test_a_snapshot_then_an_exact_delta_then_a_stale_cursor_resnapshot(self) -> None:
        version = self.decide()
        snapshot = self.call("/api/project-graph")["json"]
        self.assertEqual((snapshot["kind"], snapshot["project_version"]), ("snapshot", version))
        kinds = {node["kind"] for node in snapshot["graph"]["nodes"]}
        self.assertTrue({"owner-decision", "specification"} <= kinds, kinds)
        self.assertTrue(all(node["source_refs"] for node in snapshot["graph"]["nodes"]))
        same = self.call("/api/project-graph")["json"]
        self.assertEqual(same["snapshot_id"], snapshot["snapshot_id"])
        # Nothing changed: the delta from the current version is empty and binds the same snapshot.
        empty = self.call("/api/project-graph", query=f"after_version={version}")["json"]
        self.assertEqual((empty["kind"], empty["delta"]["added_nodes"], empty["delta"]["changed_nodes"], empty["from_snapshot_id"]),
                         ("delta", [], [], snapshot["snapshot_id"]))
        # A new intent-ledger decision: the version advances and the snapshot identity changes (its
        # sources changed), while the exact delta says the graph gained no node and no edge.
        posted = self.call("/api/commands", body=self.command(idempotency_key="later", expected_project_version=version,
                                                              payload={"wording": "Also cite timestamps."}))
        self.assertEqual(posted["status"], "200 OK", posted.get("json"))
        delta = self.call("/api/project-graph", query=f"after_version={version}")["json"]
        self.assertEqual((delta["kind"], delta["after_version"], delta["project_version"], delta["from_snapshot_id"]),
                         ("delta", version, version + 1, snapshot["snapshot_id"]))
        self.assertNotEqual(delta["snapshot_id"], snapshot["snapshot_id"])
        after = self.call("/api/project-graph")["json"]
        # Exactness: the server's delta is precisely the difference between the two full snapshots
        # the client could have taken itself (here the projection drops the approved draft's
        # proposal node once a later decision exists; the delta says so rather than hiding it).
        self.assertEqual(delta["delta"], graph_delta(snapshot["graph"], after["graph"]))
        self.assertEqual((delta["delta"]["from_version"], delta["delta"]["to_version"]), (version, version + 1))
        self.assertEqual(delta["snapshot_id"], after["snapshot_id"])
        # The delta from before the approval adds exactly the decision and specification nodes that
        # the approval created (and whatever else changed between those two projections).
        early = self.call("/api/project-graph", query="after_version=1")["json"]
        self.assertTrue({n["id"] for n in after["graph"]["nodes"] if n["kind"] in ("owner-decision", "specification")}
                        <= set(early["delta"]["added_nodes"]))
        # A cursor ahead of the project, or too far behind, is a resnapshot instruction, never a mixed delta.
        ahead = self.call("/api/project-graph", query=f"after_version={version + 50}")["json"]
        self.assertEqual((ahead["kind"], ahead.get("reason")), ("resnapshot", "cursor-ahead-of-project"))
        self.assertNotIn("delta", ahead)
        self.assertEqual(graph_view(self.store, "citations", principal=OWNER, after_version=0)["kind"], "delta")
        events_needed = MAX_DELTA_SPAN + 1
        self.assertGreater(events_needed, version + 1)  # the bound is real, not the fixture's size
        with self.assertRaises(GraphRefused):
            graph_view(self.store, "citations", principal=OWNER, after_version=-1)

    def test_details_come_after_authorization_and_forged_ids_grant_nothing(self) -> None:
        version = self.decide()
        snapshot = self.call("/api/project-graph")["json"]
        decision = next(node for node in snapshot["graph"]["nodes"] if node["kind"] == "owner-decision")
        details = self.call("/api/project-graph/details", query="id=" + decision["id"].replace(":", "%3A"))["json"]
        self.assertEqual((details["node"]["id"], details["project_version"], details["snapshot_id"]), (decision["id"], version, snapshot["snapshot_id"]))
        self.assertTrue(any(e["source"] == decision["id"] or e["target"] == decision["id"] for e in details["edges"]))
        self.assertEqual(details["source_refs"], decision["source_refs"])
        forged = self.call("/api/project-graph/details", query="id=decision%3Av999")
        self.assertEqual(forged["status"], "404 Not Found")
        self.assertEqual(self.store.snapshot("citations", principal=OWNER)["project_version"], version)  # nothing created
        with self.assertRaises(GraphRefused):
            node_details(self.store, "citations", principal=OWNER, node_id={"id": decision["id"]})

    def test_a_non_owner_principal_cannot_read_the_graph(self) -> None:
        self.decide()
        with self.assertRaises(IntentRefused):
            graph_view(self.store, "citations", principal=Principal("someone-else", "owner"))
        with self.assertRaises(IntentRefused):
            node_details(self.store, "citations", principal=Principal("worker", "proposal"), node_id="decision:v3")
        # Authorization is checked before the id is looked at: a malformed id under a refused
        # principal is an authorization refusal, never a hint that the id shape was wrong.
        with self.assertRaises((IntentRefused, GraphRefused)) as refused:
            node_details(self.store, "citations", principal=Principal("worker", "proposal"), node_id={"id": "x"})
        self.assertIsInstance(refused.exception, IntentRefused, "the id was examined before authorization")

    def test_a_cursor_further_behind_than_the_bound_is_a_resnapshot(self) -> None:
        version = self.decide()
        self.assertGreater(MAX_DELTA_SPAN, version)  # the fixture cannot reach the real bound; lower it for the test
        with patch.object(graph_transport, "MAX_DELTA_SPAN", 1):
            behind = graph_view(self.store, "citations", principal=OWNER, after_version=0)
            self.assertEqual((behind["kind"], behind.get("reason"), behind.get("bound")), ("resnapshot", "cursor-gap-exceeds-bound", 1))
            self.assertNotIn("delta", behind)
            near = graph_view(self.store, "citations", principal=OWNER, after_version=version - 1)
            self.assertEqual(near["kind"], "delta")

    def test_the_snapshot_identity_names_its_sources(self) -> None:
        self.decide()
        events = self.store._read(self.store._path("citations")) if hasattr(self.store, "_path") else None
        snapshot = self.call("/api/project-graph")["json"]
        self.assertEqual(len(snapshot["snapshot_id"]), 64)
        if events is not None:
            self.assertEqual(snapshot["snapshot_id"], snapshot_id(events, project="citations", principal=OWNER, observation_cursor=None))
            self.assertNotEqual(snapshot["snapshot_id"], snapshot_id(events, project="citations", principal=Principal(OWNER.identity, "proposal"),
                                                                     observation_cursor=None))
        self.assertIsNone(snapshot["observation_cursor"])
        self.assertEqual(snapshot["source_freshness"]["observations"], "none")


if __name__ == "__main__":
    unittest.main()
