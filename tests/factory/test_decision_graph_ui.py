"""The static decision graph UI (SPECIFICATION 10, WP10B): served by the real Front Door under its
CSP, read-only (two GET routes, no mutation endpoint, no HTML injection), five views over the real
projection, predicted never shown as implemented, stale or unknown proof never green, exact
client-side deltas, keyboard/list alternative, polling paused while hidden, and a graph that is
reconstructed from the store alone after the service restarts. The pure JavaScript is run under
Node (a required tool of these detectors, not an optional one) over graphs the real projection made."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.frontdoor_http import FrontDoorApplication  # noqa: E402
from factory_kernel.frontdoor_intent import IntentStore  # noqa: E402
from factory_kernel.graph_transport import graph_view  # noqa: E402
from factory_kernel.project_graph import graph_delta  # noqa: E402
from tests.factory.test_frontdoor_intent import OWNER, REPO  # noqa: E402
from tests.factory.test_project_graph_http import ORIGIN, TOKEN, ProjectGraphHTTPTests  # noqa: E402

STATIC = ROOT / "factory_kernel" / "frontdoor_static"
SCRIPT = STATIC / "decision-graph.js"
NODE = shutil.which("node")


def run_node(program: str, payload: dict) -> dict:
    """Run a Node program that requires the real script and prints one JSON object."""
    if NODE is None:
        raise AssertionError("node is required to run the decision-graph detectors; it is not on PATH")
    completed = subprocess.run([NODE, "-e", program, str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True,
                               timeout=60, cwd=ROOT)
    if completed.returncode != 0:
        raise AssertionError("node failed: " + completed.stderr[-2000:])
    return json.loads(completed.stdout)


PROGRAM = r"""
const g = require(process.argv[1]);
let raw = ""; process.stdin.on("data", (c) => raw += c); process.stdin.on("end", () => {
  const input = JSON.parse(raw);
  const out = {};
  const model = g.graphViews(input.snapshot);
  out.views = Object.keys(model.views);
  out.built_ids = model.views.built.rows.map((r) => r.id);
  out.explore_predicted = model.views.explore.rows.filter((r) => r.predicted).map((r) => r.id);
  out.prove_states = Object.fromEntries(model.views.prove.rows.map((r) => [r.id, r.state]));
  out.blockers = model.views.prove.blockers.map((b) => b.id);
  out.intent_ids = model.views.intent.rows.map((r) => r.id);
  out.reconsider = { changed: model.views.reconsider.changed, ids: model.views.reconsider.rows.map((r) => r.id) };
  out.active = model.active;
  out.active_with_run_only = g.graphViews({nodes: input.snapshot.nodes.filter((n) => n.kind !== "programme-item"), edges: []}).active;
  out.active_with_pr = g.graphViews({nodes: [{id: "item:i1", kind: "programme-item", label: "i1", currentness: "recorded", gaps: [], issue: 7, pr: 9}], edges: []}).active;
  out.active_without_pr = g.graphViews({nodes: [{id: "item:i1", kind: "programme-item", label: "i1", currentness: "recorded", gaps: [], issue: 7, pr: null}], edges: []}).active;
  const big = { nodes: Array.from({length: 2000}, (_, i) => ({id: "claim:c" + i, kind: "requirement", label: "r" + i, currentness: "unobserved", gaps: ["no-attestation"], source_refs: []})),
                edges: Array.from({length: 1999}, (_, i) => ({relation: "depends_on", source: "claim:c" + (i + 1), target: "claim:c" + i, currentness: "derived", gaps: [], source_refs: []})) };
  const started = Date.now();
  const bigModel = g.graphViews(big);
  const bigLayout = g.layout(bigModel.views.prove.rows, g.GRAPH_VIEWS.prove.kinds);
  out.big = { ms: Date.now() - started, rows: bigModel.views.prove.rows.length, placed: bigLayout.positions.size, hidden: bigLayout.hidden,
              blockers: bigModel.views.prove.blockers.length, table_bound: g.MAX_TABLE_ROWS };
  out.states = { predicted: g.proofState({currentness: "predicted", gaps: ["prediction-not-observed"]}).state,
                 unknown: g.proofState({currentness: "unknown", gaps: []}).state,
                 gap: g.proofState({currentness: "current", gaps: ["subject-unlinked"]}).state,
                 current: g.proofState({currentness: "current", gaps: []}).state,
                 stale: g.proofState({currentness: "stale", gaps: ["base-moved"]}).state,
                 unverified: g.proofState({currentness: "unverified", gaps: ["verification-refused"]}).state };
  const merged = g.applyDelta(input.snapshot, input.delta);
  out.merged_equals_after = JSON.stringify({nodes: merged.nodes, edges: merged.edges}) === JSON.stringify({nodes: input.after.nodes, edges: input.after.edges});
  out.merged_version = merged.project_version;
  const rows = model.views.prove.rows;
  const a = g.layout(rows, g.GRAPH_VIEWS.prove.kinds), b = g.layout(rows.slice().reverse(), g.GRAPH_VIEWS.prove.kinds);
  out.layout_deterministic = JSON.stringify([...a.positions]) === JSON.stringify([...b.positions]);
  out.layout_bounded = g.layout(Array.from({length: 200}, (_, i) => ({id: "x" + String(i).padStart(3, "0"), kind: "run"})), ["run"], {maxRows: 60});
  out.layout_bounded = { placed: out.layout_bounded.positions.size, hidden: out.layout_bounded.hidden };
  out.poll = { active: g.POLL_ACTIVE_MS, idle: g.POLL_IDLE_MS };
  process.stdout.write(JSON.stringify(out));
});
"""


class DecisionGraphAssetTests(unittest.TestCase):
    """Reuses the real Front Door fixture by composition (a real store, the real WSGI app, a fake
    GitHub) without re-running that fixture's own tests."""

    def setUp(self) -> None:
        self.fixture = ProjectGraphHTTPTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.github = self.fixture.store, self.fixture.github

    @property
    def app(self):
        return self.fixture.app

    @app.setter
    def app(self, value):
        self.fixture.app = value

    def call(self, *args, **kwargs):
        return self.fixture.call(*args, **kwargs)

    def command(self, **changes):
        return self.fixture.command(**changes)

    def decide(self) -> int:
        return self.fixture.decide()

    def test_the_graph_assets_are_served_static_under_the_csp_and_the_page_carries_the_views(self) -> None:
        for path, mime in (("/decision-graph.js", "text/javascript"), ("/decision-graph.css", "text/css"), ("/", "text/html")):
            with self.subTest(path):
                response = self.call(path, HTTP_AUTHORIZATION="")
                self.assertEqual(response["status"], "200 OK")
                self.assertTrue(response["headers"]["Content-Type"].startswith(mime))
                self.assertEqual(response["headers"]["Cache-Control"], "no-store")
                self.assertIn("script-src 'self'", response["headers"]["Content-Security-Policy"])
                self.assertNotIn(TOKEN.encode(), response["body"])
        page = self.call("/", HTTP_AUTHORIZATION="")["body"].decode("utf-8")
        for needle in ('<script src="/decision-graph.js" defer>', '<link rel="stylesheet" href="/decision-graph.css">', 'id="graph-card"',
                       'role="tablist"', 'data-view="intent"', 'data-view="explore"', 'data-view="prove"', 'data-view="built"',
                       'data-view="reconsider"', '<svg id="graph-svg"', '<table id="graph-table"', 'id="graph-details"', 'id="graph-blockers"'):
            self.assertIn(needle, page, needle)
        self.assertNotIn("<script>", page)  # no inline script under the CSP
        css = (STATIC / "decision-graph.css").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":focus", css)

    def test_the_script_is_read_only_and_never_injects_html(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "srcdoc"):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertIn("textContent", source)
        paths = set(re.findall(r'api\("(/api/[a-z\-/]+)', source))
        self.assertEqual(paths, {"/api/project-graph", "/api/project-graph/details"})
        for mutation in ("/api/commands", "/api/stop", "/api/execution-budget", "/api/publication", "/api/exploration", "method: \"POST\"", "fetch("):
            self.assertNotIn(mutation, source, mutation)
        self.assertIn("encodeURIComponent(id)", source)
        self.assertIn('document.visibilityState === "hidden"', source)
        self.assertIn("createElementNS", source)
        self.assertIn("prefers-reduced-motion", (STATIC / "decision-graph.css").read_text(encoding="utf-8"))
        # The page's refresh hooks the graph in after the snapshot, and only when the script loaded.
        self.assertIn('if (typeof refreshDecisionGraph === "function") await refreshDecisionGraph();', (STATIC / "frontdoor.js").read_text(encoding="utf-8"))

    def test_the_views_over_a_real_projection_never_promote_predictions_or_unproven_claims(self) -> None:
        version = self.decide()
        snapshot = self.call("/api/project-graph")["json"]
        # A recorded owner decision, then an exploration candidate whose value is only predicted.
        self.call("/api/commands", body=self.command(idempotency_key="later", expected_project_version=version,
                                                     payload={"wording": "Also cite timestamps."}))
        after = self.call("/api/project-graph")["json"]
        delta = self.call("/api/project-graph", query=f"after_version={version}")["json"]
        self.assertEqual(delta["kind"], "delta")
        graph = {**snapshot["graph"], "nodes": snapshot["graph"]["nodes"] + [
            {"id": "candidate:s:index", "kind": "candidate", "label": "index", "currentness": "predicted", "gaps": ["prediction-not-observed"], "source_refs": ["exploration-session:s"]},
            {"id": "claim:r1", "kind": "requirement", "label": "cited words", "currentness": "unobserved", "gaps": ["no-attestation"], "source_refs": ["spec:1"]},
            {"id": "exploration:a1", "kind": "assumption", "label": "lookup stays small", "currentness": "invalidated", "gaps": [], "source_refs": ["exploration:a1"]},
            {"id": "recommendation:s:0", "kind": "recommendation", "label": "use index", "currentness": "recorded", "gaps": [], "source_refs": ["exploration-session:s"]},
            {"id": "run:7", "kind": "run", "label": "run 7", "currentness": "recorded", "gaps": [], "source_refs": ["attestations:7"]},
        ], "edges": snapshot["graph"]["edges"] + [
            {"relation": "depends_on", "source": "candidate:s:index", "target": "exploration:a1", "currentness": "recorded", "gaps": [], "source_refs": []},
            {"relation": "selected", "source": "recommendation:s:0", "target": "candidate:s:index", "currentness": "recorded", "gaps": [], "source_refs": []},
        ]}
        result = run_node(PROGRAM, {"snapshot": graph, "delta": graph_delta(snapshot["graph"], after["graph"]), "after": {
            **after["graph"], "nodes": after["graph"]["nodes"] + graph["nodes"][len(snapshot["graph"]["nodes"]):],
            "edges": after["graph"]["edges"] + graph["edges"][len(snapshot["graph"]["edges"]):]}})
        self.assertEqual(result["views"], ["intent", "explore", "prove", "built", "reconsider"])
        self.assertNotIn("candidate:s:index", result["built_ids"])          # a prediction is never shown as implemented
        self.assertIn("candidate:s:index", result["explore_predicted"])      # it is shown as a prediction where it belongs
        self.assertEqual(result["prove_states"]["claim:r1"], "insufficient")  # unproven is never green
        self.assertIn("claim:r1", result["blockers"])
        self.assertTrue(any(i.startswith("decision:") for i in result["intent_ids"]) and any(i.startswith("spec:") for i in result["intent_ids"]))
        self.assertEqual(result["reconsider"]["changed"], ["exploration:a1"])
        self.assertEqual(sorted(result["reconsider"]["ids"]), ["candidate:s:index", "exploration:a1", "recommendation:s:0"])  # propagation along dependencies
        self.assertEqual(result["states"], {"predicted": "insufficient", "unknown": "unknown", "gap": "insufficient", "current": "current",
                                            "stale": "stale", "unverified": "insufficient"})
        # The fast poll needs an in-progress signal: a programme item whose issue is observed but whose PR
        # is not. A retained run alone, or an item that already has a PR, is history and polls at idle.
        self.assertFalse(result["active"])
        self.assertFalse(result["active_with_run_only"])
        self.assertFalse(result["active_with_pr"])
        self.assertTrue(result["active_without_pr"])
        # The projection's own maximum (2000 nodes, MAX_NODES) is projected into views, laid out and bounded
        # without stalling; the table alternative is bounded to MAX_TABLE_ROWS and counts the rest.
        self.assertEqual((result["big"]["rows"], result["big"]["placed"], result["big"]["hidden"], result["big"]["blockers"]), (2000, 60, 1940, 2000))
        self.assertLess(result["big"]["ms"], 5000, result["big"])
        self.assertEqual(result["big"]["table_bound"], 400)
        self.assertIn("view.rows.slice(0, MAX_TABLE_ROWS)", SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(result["poll"], {"active": 5000, "idle": 30000})
        self.assertTrue(result["layout_deterministic"])
        self.assertEqual(result["layout_bounded"], {"placed": 60, "hidden": 140})

    def test_a_client_side_delta_merge_equals_the_servers_next_snapshot(self) -> None:
        version = self.decide()
        snapshot = self.call("/api/project-graph")["json"]
        self.call("/api/commands", body=self.command(idempotency_key="later", expected_project_version=version, payload={"wording": "Also cite timestamps."}))
        after = self.call("/api/project-graph")["json"]
        delta = self.call("/api/project-graph", query=f"after_version={version}")["json"]
        self.assertEqual(delta["from_snapshot_id"], snapshot["snapshot_id"])
        result = run_node(PROGRAM, {"snapshot": snapshot["graph"], "delta": delta["delta"], "after": after["graph"]})
        self.assertTrue(result["merged_equals_after"])
        self.assertEqual(result["merged_version"], after["project_version"])

    def test_the_graph_is_reconstructed_from_the_store_alone_after_a_restart(self) -> None:
        self.decide()
        before = self.call("/api/project-graph")["json"]
        # A new service over the same directory: no in-memory state survives, only the store.
        restarted = IntentStore(self.store.directory, repository=REPO, owner=OWNER.identity)
        self.app = FrontDoorApplication(store=restarted, project="citations", token=TOKEN, origin=ORIGIN,
                                        github=self.github, labels={}, app_login="factory[bot]")
        after = self.call("/api/project-graph")["json"]
        self.assertEqual(after["snapshot_id"], before["snapshot_id"])
        self.assertEqual(after["graph"], before["graph"])
        self.assertEqual(graph_view(restarted, "citations", principal=OWNER)["snapshot_id"], before["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
