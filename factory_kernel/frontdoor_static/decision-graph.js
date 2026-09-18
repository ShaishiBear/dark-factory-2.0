"use strict";
// The decision and implementation graph (SPECIFICATION 10, WP10B): a read-only projection of
// `GET /api/project-graph` into five views, an SVG with a list alternative, keyboard selection
// and a details panel. Nothing here approves, publishes or transitions anything: the only
// requests are the two GET routes, and every string reaches the page through textContent.
//
// The pure functions (views, layout, delta merge, proof state) take and return plain data so
// they run under Node for the detector tests; the DOM code below only calls them.

const GRAPH_VIEWS = Object.freeze({
  intent: { title: "Intent", kinds: ["owner-decision", "specification", "proposal", "requirement", "question"] },
  explore: { title: "Explore", kinds: ["question", "assumption", "candidate", "recommendation", "observation"] },
  prove: { title: "Prove", kinds: ["requirement", "proof-obligation", "implementation-claim", "attestation", "run", "programme-item"] },
  built: { title: "Built system", kinds: ["implementation-component", "implementation-claim", "programme-item", "run"] },
  reconsider: { title: "Reconsider", kinds: ["assumption", "recommendation", "candidate", "requirement", "proof-obligation", "programme-item"] },
});
const POLL_ACTIVE_MS = 5000;   // only while work is active
const POLL_IDLE_MS = 30000;    // while visible and idle
const MAX_TABLE_ROWS = 400;    // the list alternative is bounded; the rest is counted, not rendered
// Work is "active" only on an in-progress signal the projection carries: a programme item whose
// issue is observed but whose pull request is not yet (admitted work with nothing published).
// A retained run or a linked item with a PR is history, not activity, and polls at the idle rate.
// A node is green only when it is current and has no gap. Predicted, proposed, unverified,
// stale and unknown are never green: the view says why, it never rounds up.
const GREEN = new Set(["current", "recorded", "supported", "observed", "derived"]);
const AMBER = new Set(["unobserved", "unapproved", "predicted", "proposed", "unlinked", "unverified", "reconsider"]);

function proofState(node) {
  const gaps = Array.isArray(node.gaps) ? node.gaps : [];
  const currentness = String(node.currentness || "unknown");
  if (gaps.length === 0 && GREEN.has(currentness)) return { state: "current", reasons: [] };
  if (AMBER.has(currentness) || (gaps.length && GREEN.has(currentness))) return { state: "insufficient", reasons: [currentness, ...gaps] };
  return { state: currentness === "unknown" ? "unknown" : "stale", reasons: [currentness, ...gaps] };
}

function graphViews(graph) {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const views = {};
  for (const [name, view] of Object.entries(GRAPH_VIEWS)) {
    const wanted = new Set(view.kinds);
    let rows = nodes.filter((node) => wanted.has(node.kind));
    if (name === "reconsider") {
      // Changed assumption or dependency -> what depends on it -> what would be repaired.
      const changed = new Set(rows.filter((n) => ["invalidated", "reconsider"].includes(n.currentness)
        || (n.gaps || []).includes("assumption-invalidated")).map((n) => n.id));
      const affected = new Set(changed);
      let grew = true;
      while (grew) {
        grew = false;
        for (const edge of edges) {
          if (affected.has(edge.target) && !affected.has(edge.source) && ["depends_on", "selected", "scheduled_as", "certifies", "implements"].includes(edge.relation)) {
            affected.add(edge.source); grew = true;
          }
        }
      }
      rows = nodes.filter((n) => affected.has(n.id));
      views[name] = { title: view.title, changed: [...changed].sort(), rows: rows.map((n) => row(n, changed.has(n.id) ? "changed" : "affected")),
        edges: edges.filter((e) => affected.has(e.source) && affected.has(e.target)) };
      continue;
    }
    const ids = new Set(rows.map((n) => n.id));
    views[name] = { title: view.title, rows: rows.map((n) => row(n)), edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
  }
  const blockers = nodes.filter((n) => proofState(n).state !== "current" && ["requirement", "proof-obligation", "implementation-claim", "attestation"].includes(n.kind))
    .map((n) => ({ id: n.id, kind: n.kind, label: n.label, ...proofState(n) }));
  views.prove.blockers = blockers;
  const active = nodes.some((n) => n.kind === "programme-item" && n.currentness === "recorded" && n.issue != null && n.pr == null);
  return { views, byId, active, truncated: Boolean(graph?.truncated), counts: graph?.counts || {} };

  function row(node, role) {
    const proof = proofState(node);
    return { id: node.id, kind: node.kind, label: node.label, currentness: node.currentness, gaps: [...(node.gaps || [])],
      state: proof.state, reasons: proof.reasons, source_refs: [...(node.source_refs || [])], role: role || "member",
      predicted: node.currentness === "predicted" || node.currentness === "proposed" };
  }
}

// Deterministic layered layout: one column per kind in view order, rows sorted by id. Bounded
// so a truncated projection stays drawable; coordinates are integers for stable SVG text.
function layout(rows, kinds, options) {
  const columnWidth = options?.columnWidth || 220;
  const rowHeight = options?.rowHeight || 44;
  const maxRows = options?.maxRows || 60;
  const columns = new Map(kinds.map((kind, index) => [kind, index]));
  const positions = new Map();
  const perColumn = new Map();
  for (const item of [...rows].sort((a, b) => (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : a.id < b.id ? -1 : 1))) {
    const column = columns.has(item.kind) ? columns.get(item.kind) : kinds.length;
    const index = perColumn.get(column) || 0;
    if (index >= maxRows) continue;
    perColumn.set(column, index + 1);
    positions.set(item.id, { x: 20 + column * columnWidth, y: 30 + index * rowHeight, column, index });
  }
  const width = 40 + Math.max(1, Math.max(...[...perColumn.keys(), 0]) + 1) * columnWidth;
  const height = 60 + Math.max(0, ...perColumn.values()) * rowHeight;
  return { positions, width, height, hidden: rows.length - positions.size };
}

// Client-side merge of an exact delta onto the snapshot the delta names.
function applyDelta(graph, delta) {
  const removed = new Set(delta.removed_nodes || []);
  const changed = new Map((delta.changed_nodes || []).map((n) => [n.id, n]));
  const added = delta.added_nodes || [];
  const nodes = graph.nodes.filter((n) => !removed.has(n.id)).map((n) => changed.get(n.id) || n).concat(added);
  const edgeKey = (e) => `${e.relation}\u001f${e.source}\u001f${e.target}`;
  const removedEdges = new Set((delta.removed_edges || []).map(edgeKey));
  const edges = graph.edges.filter((e) => !removedEdges.has(edgeKey(e))).concat(delta.added_edges || []);
  const kept = new Set(nodes.map((n) => n.id));
  return { ...graph, nodes: nodes.sort((a, b) => (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0)),
    edges: edges.filter((e) => kept.has(e.source) && kept.has(e.target)).sort((a, b) => edgeKey(a) < edgeKey(b) ? -1 : 1),
    project_version: delta.to_version ?? graph.project_version };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { GRAPH_VIEWS, POLL_ACTIVE_MS, POLL_IDLE_MS, MAX_TABLE_ROWS, proofState, graphViews, layout, applyDelta };
}

// ---- DOM (browser only) ----------------------------------------------------------------------
if (typeof document !== "undefined" && typeof $ === "function") {
  let graphState = null;      // the last snapshot, as the server rendered it (plus applied deltas)
  let graphMeta = null;       // project_version and snapshot_id the server named
  let graphView = "intent";
  let graphSelected = null;
  let graphTimer = null;
  const SVG = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => {
    const node = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
    return node;
  };

  async function refreshDecisionGraph() {
    clearTimeout(graphTimer);
    if (!token) return;
    try {
      const query = graphMeta?.project_version !== undefined ? `?after_version=${graphMeta.project_version}` : "";
      const result = await api("/api/project-graph" + query);
      if (result.kind === "snapshot") {
        graphState = result.graph;
      } else if (result.kind === "delta" && graphState && result.from_snapshot_id === graphMeta?.snapshot_id) {
        graphState = applyDelta(graphState, result.delta);
      } else {
        // A resnapshot instruction, or a delta against a snapshot this tab no longer holds:
        // discard and fetch the full snapshot; never mix lineages.
        graphMeta = null;
        graphState = (await api("/api/project-graph")).graph;
        result.snapshot_id = undefined;
      }
      graphMeta = { project_version: result.project_version, snapshot_id: result.snapshot_id,
        observation_cursor: result.observation_cursor, freshness: result.source_freshness };
      if (graphMeta.snapshot_id === undefined) {
        const fresh = await api("/api/project-graph");
        graphState = fresh.graph;
        graphMeta = { project_version: fresh.project_version, snapshot_id: fresh.snapshot_id, observation_cursor: fresh.observation_cursor, freshness: fresh.source_freshness };
      }
      renderDecisionGraph();
    } catch (error) {
      $("graph-state").textContent = "Graph unavailable: " + error.message;
    } finally {
      scheduleGraphPoll();
    }
  }

  function scheduleGraphPoll() {
    clearTimeout(graphTimer);
    if (!token || document.visibilityState === "hidden") return;  // paused while hidden
    const active = graphState ? graphViews(graphState).active : false;
    graphTimer = setTimeout(refreshDecisionGraph, active ? POLL_ACTIVE_MS : POLL_IDLE_MS);
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshDecisionGraph(); else clearTimeout(graphTimer); });

  function renderDecisionGraph() {
    if (!graphState) return;
    const model = graphViews(graphState);
    const view = model.views[graphView];
    $("graph-state").textContent = `Project version ${graphMeta.project_version}; observations: ${graphMeta.freshness?.observations || "none"}` +
      (model.truncated ? "; the projection is truncated at its bound" : "") + `; ${view.rows.length} nodes in this view. Projection only: nothing here is proof.`;
    for (const button of document.querySelectorAll("#graph-views button")) {
      button.setAttribute("aria-selected", String(button.dataset.view === graphView));
      button.tabIndex = button.dataset.view === graphView ? 0 : -1;
    }
    renderGraphSvg(view);
    renderGraphTable(view);
    renderGraphBlockers(model.views.prove.blockers);
    renderGraphDetails(model);
  }

  function renderGraphSvg(view) {
    const kinds = GRAPH_VIEWS[graphView].kinds;
    const plan = layout(view.rows, kinds);
    const svg = $("graph-svg");
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${plan.width} ${plan.height}`);
    svg.setAttribute("aria-label", `${view.title} view, ${view.rows.length} nodes` + (plan.hidden ? `, ${plan.hidden} not drawn` : ""));
    for (const edge of view.edges) {
      const a = plan.positions.get(edge.source), b = plan.positions.get(edge.target);
      if (!a || !b) continue;
      const line = svgEl("line", { x1: a.x + 90, y1: a.y + 12, x2: b.x + 90, y2: b.y + 12, class: `edge edge-${edge.relation}` });
      const title = svgEl("title");
      title.textContent = `${edge.source} ${edge.relation} ${edge.target} (${edge.currentness})`;
      line.append(title);
      svg.append(line);
    }
    for (const row of view.rows) {
      const at = plan.positions.get(row.id);
      if (!at) continue;
      const group = svgEl("g", { class: `node node-${row.state} node-role-${row.role}${row.predicted ? " node-predicted" : ""}`, tabindex: 0, role: "button",
        "data-id": row.id, "aria-pressed": String(graphSelected === row.id), transform: `translate(${at.x},${at.y})` });
      group.append(svgEl("rect", { width: 180, height: 24, rx: 4 }));
      const label = svgEl("text", { x: 6, y: 16 });
      label.textContent = `${row.kind}: ${row.label}`.slice(0, 28);
      const title = svgEl("title");
      title.textContent = `${row.kind} ${row.label} [${row.state}${row.reasons.length ? ": " + row.reasons.join(", ") : ""}]`;
      group.append(label, title);
      group.addEventListener("click", () => selectGraphNode(row.id));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectGraphNode(row.id); }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const all = [...svg.querySelectorAll("g.node")];
          const index = all.indexOf(group) + (event.key === "ArrowDown" ? 1 : -1);
          if (all[index]) all[index].focus();
        }
      });
      svg.append(group);
    }
  }

  function renderGraphTable(view) {
    const body = $("graph-rows");
    body.replaceChildren();
    for (const row of view.rows.slice(0, MAX_TABLE_ROWS)) {
      const tr = document.createElement("tr");
      tr.className = `state-${row.state}` + (row.predicted ? " predicted" : "");
      for (const value of [row.kind, row.label, row.role === "changed" ? "changed" : row.state, row.reasons.join(", ") || "none"]) tr.append(text("td", value));
      const cell = document.createElement("td");
      const button = text("button", "Details");
      button.type = "button";
      button.addEventListener("click", () => selectGraphNode(row.id));
      cell.append(button);
      tr.append(cell);
      body.append(tr);
    }
    if (view.rows.length > MAX_TABLE_ROWS) {
      const tr = document.createElement("tr");
      const cell = text("td", `${view.rows.length - MAX_TABLE_ROWS} more node(s) in this view are not listed; use the views or the projection's own bound`, "muted");
      cell.colSpan = 5;
      tr.append(cell);
      body.append(tr);
    }
    $("graph-empty").hidden = view.rows.length > 0;
  }

  function renderGraphBlockers(blockers) {
    const list = $("graph-blockers");
    list.replaceChildren();
    for (const blocker of blockers) list.append(text("li", `${blocker.kind} ${blocker.label}: ${blocker.state} (${blocker.reasons.join(", ") || "no reason recorded"})`));
    $("graph-blockers-title").textContent = blockers.length ? `${blockers.length} blocker(s): nothing below is proven until each is resolved` : "No blocker recorded in this projection (that is not a proof of completion)";
  }

  async function selectGraphNode(id) {
    graphSelected = id;
    const panel = $("graph-details");
    panel.replaceChildren(text("p", "Loading details…", "muted"));
    try {
      const details = await api("/api/project-graph/details?id=" + encodeURIComponent(id));
      panel.replaceChildren();
      const node = details.node;
      const proof = proofState(node);
      panel.append(text("h3", `${node.kind}: ${node.label}`));
      panel.append(text("p", `State: ${proof.state}${proof.reasons.length ? " (" + proof.reasons.join(", ") + ")" : ""}. ${node.currentness === "predicted" ? "A prediction, not an implemented or measured fact." : ""}`));
      list(panel, "Source records", node.source_refs || []);
      list(panel, "Edges", details.edges.map((e) => `${e.source} ${e.relation} ${e.target} (${e.currentness})`));
      for (const key of ["intent_status", "proof_status", "authority_profile", "issue", "pr", "head", "evidence_class", "trusted_currency"]) {
        if (node[key] !== undefined && node[key] !== null) panel.append(text("p", `${key}: ${typeof node[key] === "object" ? JSON.stringify(node[key]) : node[key]}`, "muted"));
      }
      panel.append(text("p", "Actions on this node use the named owner controls above (approval, publication, stop); selecting a node changes nothing.", "muted"));
    } catch (error) {
      panel.replaceChildren(text("p", "Details unavailable: " + error.message, "error"));
    }
    renderDecisionGraph();
  }

  for (const button of document.querySelectorAll("#graph-views button")) {
    button.addEventListener("click", () => { graphView = button.dataset.view; renderDecisionGraph(); });
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const all = [...document.querySelectorAll("#graph-views button")];
      const next = all[(all.indexOf(button) + (event.key === "ArrowRight" ? 1 : all.length - 1)) % all.length];
      next.focus(); next.click();
    });
  }
  $("graph-refresh").addEventListener("click", () => refreshDecisionGraph());
  window.refreshDecisionGraph = refreshDecisionGraph;
}
