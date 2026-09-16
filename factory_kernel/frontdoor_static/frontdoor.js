"use strict";
let token = "";
let snapshot = null;
let history = null;
let historyShown = 0;
let publicationPreview = null;
let publicationRequestId = null;
let busy = false;
let preparationBusy = false;
let synthesisBusy = false;
let stopBusy = false;
let stopRequested = false;
const $ = (id) => document.getElementById(id);
const text = (tag, value, className) => {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
};
function message(value, error = false) {
  $("message").textContent = value;
  $("message").className = error ? "error" : "";
}
async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    cache: "no-store", credentials: "omit", redirect: "error",
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request unavailable.");
  return result;
}
function list(parent, title, items) {
  if (!items?.length) return;
  parent.append(text("h3", title));
  const ul = document.createElement("ul");
  for (const item of items) ul.append(text("li", item));
  parent.append(ul);
}
function render() {
  $("login").hidden = true;
  $("workspace").hidden = false;
  $("logout").hidden = false;
  $("repository").textContent = snapshot.repository;
  $("project").textContent = snapshot.project.replaceAll("-", " ");
  const state = snapshot.intent;
  const executionBudget = $("execution-budget");
  executionBudget.replaceChildren();
  const budget = snapshot.execution_budget;
  if (!budget?.allowance) {
    executionBudget.append(text("p", "No cumulative execution allowance is recorded. Historical execution spending is unknown."));
  } else {
    executionBudget.append(text("p", `Recorded allowance: $${(budget.allowance.limit_microusd / 1000000).toFixed(2)} across ${budget.allowance.max_calls} attempts.`));
    executionBudget.append(text("p", `Retained reservations: $${(budget.reserved_microusd / 1000000).toFixed(2)} across ${budget.calls} ${budget.calls === 1 ? "attempt" : "attempts"}. This is reserved capacity, not a final bill.`));
    const statuses = {"historical-spend-unknown": "Earlier spending is unknown; this allowance cannot authorize execution.", "unresolved-attempt": "An attempt has unresolved spending. This allowance cannot authorize further calls.", overrun: "Reported spending exceeded a reservation. This allowance cannot authorize further calls.", exhausted: "The recorded execution allowance is exhausted.", available: "The recorded allowance has capacity."};
    executionBudget.append(text("p", statuses[budget.status] || "Execution budget state is unavailable."));
  }
  executionBudget.append(text("p", "The hosted execution worker is not yet connected to this ledger. Programme replacement remains blocked. Reservations are retained across strategy changes.", "muted"));
  if (history && history.project_version !== state.project_version) $("history-state").textContent = `Showing history through version ${history.project_version}. Saved decisions have changed; load history again for the latest.`;
  const ledger = $("ledger");
  ledger.replaceChildren();
  for (const row of state.ledger) {
    ledger.append(text("p", row.kind === "record-intent" ? "Intent" : "Exploration", "muted"), text("blockquote", row.wording));
  }
  const draft = $("draft");
  draft.replaceChildren();
  const proposal = state.draft;
  const alreadyApproved = proposal && state.approvals.some((a) => a.draft_version === proposal.draft_version);
  if (!proposal) {
    const recorded = state.ledger.some((row) => row.kind === "record-intent");
    draft.append(text("h2", "Your specification review"), text("p", recorded ? "Your original intent is saved. A prepared specification will appear here for explicit review." : "Save your intent first. A prepared specification will appear here for explicit review."));
    if (!snapshot.preparation_available) draft.append(text("p", "Specification preparation is not enabled on this host.", "muted"));
  } else {
    const spec = proposal.spec;
    draft.append(text("h2", spec.title), text("p", spec.outcome));
    list(draft, "Required behaviour", spec.requirements.flatMap((r) => r.acceptance.map((a) => a.text)));
    list(draft, "Hard constraints", spec.constraints);
    list(draft, "Outside this scope", spec.non_goals);
    list(draft, "Assumptions", proposal.assumptions);
    list(draft, "Product questions still open", proposal.open_questions);
    list(draft, "Technical questions for the factory", proposal.technical_questions);
    draft.append(text("p", `Scope revision ${spec.revision} · draft ${proposal.draft_version}`, "muted"), text("p", proposal.spec_sha256, "hash"));
  }
  $("approval-form").hidden = !proposal || alreadyApproved || proposal.open_questions.length > 0;
  const preparation = snapshot.preparation;
  const attempted = preparation?.identity.command.expected_project_version === state.project_version;
  const currentPreparation = preparation && (attempted || (proposal && proposal.draft_version === preparation.draft_version));
  $("prepare").hidden = !snapshot.preparation_available || !state.ledger.some((row) => row.kind === "record-intent") || attempted || Boolean(proposal);
  $("recovery-form").hidden = !snapshot.preparation_recovery;
  $("recover-check").checked = false;
  $("preparation-status").replaceChildren();
  if (preparation && !currentPreparation) $("preparation-status").append(text("p", "The recorded preparation applies to earlier intent. Prepare the current intent for a new review.", "muted"));
  if (currentPreparation) {
    const statuses = { pending: "Preparation is running or was interrupted. Refresh to observe its recorded result; it will not restart automatically.", failed: "Preparation could not produce a current, validated draft. Its attempt is recorded for inspection.", "needs-revision": "The intent audit found that the draft needs revision. It is not ready for approval.", question: "Answer the product question above by saving a clarification in your intent, then prepare scope again.", "ready-for-review": "Drafting and intent audit are complete. Review the scope before approving." };
    $("preparation-status").append(text("p", alreadyApproved ? "Intent audit completed for this approved scope." : statuses[preparation.state] || "Preparation state is unknown.", "muted"));
    if (preparation.audit) {
      const details = document.createElement("details"); details.append(text("summary", "Intent audit"));
      for (const [name, check] of Object.entries(preparation.audit.checks)) details.append(text("p", `${name.replaceAll("_", " ")}: ${check.basis}`));
      for (const [name, scenario] of Object.entries(preparation.audit.scenarios)) details.append(text("p", `${name.replaceAll("_", " ")}: ${scenario}`));
      $("preparation-status").append(details);
    }
  }
  $("approve-check").checked = false;
  $("approvals").replaceChildren();
  if (state.approvals.length) {
    const approval = state.approvals.at(-1);
    $("approvals").append(text("p", `Scope revision ${approval.spec.revision} approved`, "badge verified"), text("p", "Scope approval records intent. Execution requires a reviewed programme on the protected branch.", "muted"));
  }
  const synthesis = snapshot.synthesis;
  const currentSynthesis = synthesis?.identity.command.expected_project_version === state.project_version;
  if (publicationPreview && publicationPreview.project_version !== state.project_version) {
    publicationPreview = null; publicationRequestId = null;
    $("publication-review").replaceChildren(); $("publication-form").hidden = true;
  }
  const dispatchedCurrent = snapshot.publication?.project_version === state.project_version && snapshot.publication.state?.startsWith("dispatch-");
  $("preview-publication").hidden = !snapshot.publication_available || !currentSynthesis || !synthesis?.review || dispatchedCurrent;
  $("strategy-choices").replaceChildren();
  if (!dispatchedCurrent) for (const choice of snapshot.strategy_choices || []) {
    const section = document.createElement("section");
    section.append(text("h3", "Saved strategy recommendation"), text("p", choice.mechanism));
    section.append(text("p", "Planning advice awaiting fresh review. It is not qualified product evidence.", "muted"));
    const button = text("button", "Review this strategy for publication"); button.type = "button";
    button.addEventListener("click", () => perform(() => showPublication({session_id: choice.session_id,
      expected_project_version: choice.project_version}, "/api/strategy-publication-preview")));
    section.append(button); $("strategy-choices").append(section);
  }
  if (dispatchedCurrent) $("publication-form").hidden = true;
  $("publication-status").replaceChildren();
  if (snapshot.publication) {
    const publication = snapshot.publication;
    const statuses = { reserved: "Publication consent is reserved. Review it again to continue if it remains current.", "already-active": "This approved scope is already active. No duplicate work was dispatched.", "requires-governed-replacement": "The existing programme is preserved; governed replacement is required.", "dispatch-pending": "Publication was reserved. Its dispatch outcome is not confirmed; it will not be repeated automatically.", "dispatch-submitted": "Publication was submitted to the protected workflow. This is not evidence of delivery or product completion.", "dispatch-uncertain": "Publication dispatch has an uncertain outcome. Refresh to observe it; it will not be submitted again." };
    $("publication-status").append(text("p", statuses[publication.state] || "Publication observation is unavailable.", "muted"));
    if (publication.workflow_observation === "observed") {
      $("publication-status").append(text("p", `Publication workflow: ${publication.workflow_status}${publication.workflow_conclusion ? ` · ${publication.workflow_conclusion}` : ""}. Active programme and product evidence are shown separately.`));
      const link = text("a", "Publication workflow evidence");
      link.href = `https://github.com/${snapshot.repository}/actions/runs/${publication.run_id}`;
      link.target = "_blank"; link.rel = "noopener noreferrer";
      $("publication-status").append(link);
    }
  }
  $("synthesize").hidden = !snapshot.synthesis_available || !state.approvals.length || currentSynthesis;
  $("programme-review").replaceChildren();
  if (synthesis) {
    const review = synthesis.review;
    if (!currentSynthesis) $("programme-review").append(text("p", "The recorded programme proposal belongs to an earlier project version. Prepare the current approved scope for review.", "muted"));
    else if (review) {
      $("programme-review").append(text("h3", "Proposed execution programme"), text("p", "Compiled for your approved scope. Protected-branch review and delivery are still required before execution.", "muted"));
      for (const item of review.items) {
        const criteria = review.input.spec.requirements.flatMap((r) => r.acceptance).filter((a) => item.acceptance.includes(a.id));
        list($("programme-review"), item.id.replaceAll("-", " "), criteria.map((a) => a.text));
        if (item.blocked_by.length) $("programme-review").append(text("p", `After: ${item.blocked_by.join(", ")}`, "muted"));
      }
      const details = document.createElement("details");
      details.append(text("summary", "Programme review artifact"), text("pre", JSON.stringify(review, null, 2), "hash"));
      $("programme-review").append(details);
    } else $("programme-review").append(text("p", synthesis.state === "pending" ? "Programme preparation is running or was interrupted. Refresh to observe its recorded result." : "Programme preparation failed. No executable programme was delivered; the attempt is recorded for inspection.", "muted"));
  }
  $("progress").replaceChildren();
  if (snapshot.execution_fence?.state === "fenced") $("progress").append(text("p", "Programme transition: new work is blocked. Running work stops at its next checkpoint. Completed work and spending remain recorded.", "badge blocked"));
  $("observation").textContent = snapshot.observation_available ? `GitHub observed ${snapshot.observed_at ? new Date(snapshot.observed_at).toLocaleString() : "just now"}. Refresh to check for changes.` : "Some GitHub observations are unavailable. Programme progress and stop state are reported separately below.";
  if (snapshot.execution) {
    const execution = snapshot.execution;
    if (execution.programme) {
      $("progress").append(text("p", `Active scope: ${execution.spec} · revision ${execution.revision}`, "muted"));
      const approval = state.approvals.at(-1);
      if (approval) {
        const matches = execution.spec_sha256 === approval.spec_sha256;
        $("progress").append(text("p", matches ? "The active programme uses your latest approved scope." : "The active programme uses a different scope from your latest approval. The latest approval has not replaced it.", matches ? "muted" : "badge blocked"));
      }
      const identity = document.createElement("details");
      identity.append(text("summary", "Active programme identity"), text("p", execution.programme, "hash"));
      if (execution.spec_sha256) identity.append(text("p", `Scope: ${execution.spec_sha256}`, "hash"));
      $("progress").append(identity);
    }
    if (!snapshot.execution.items.length) $("progress").append(text("p", "No active programme."));
    for (const item of snapshot.execution.items) {
      const row = text("div", "", "work-item");
      row.append(text("strong", item.id.replaceAll("-", " ")), text("span", item.status.replaceAll("-", " "), `badge ${item.completion_verified ? "verified" : item.waiting_on.length ? "blocked" : ""}`));
      if (item.waiting_on.length) row.append(text("p", `Waiting for ${item.waiting_on.join(", ")}`, "muted"));
      if (item.issue) {
        const link = text("a", `Evidence on issue ${item.issue}`);
        link.href = `https://github.com/${snapshot.repository}/issues/${item.issue}`;
        link.target = "_blank"; link.rel = "noopener noreferrer";
        const p = document.createElement("p"); p.append(link); row.append(p);
      }
      $("progress").append(row);
    }
  }
  if (!snapshot.execution) $("progress").append(text("p", "Programme progress is unknown.", "muted"));
  $("stop-state").textContent = snapshot.stop?.state === "stopped" ? `Stop observed on GitHub: ${snapshot.stop.issues.map((n) => `#${n}`).join(", ")}.` : snapshot.stop ? "No open remote stop was observed." : "Stop state is unknown.";
  if (stopRequested && snapshot.stop?.state === "stopped") {
    stopRequested = false;
    message("Remote stop confirmed. Running work will stop at its next checkpoint.");
  }
}
async function refresh() { snapshot = await api("/api/snapshot"); render(); await refreshExploration(); }
async function command(operation, payload) {
  await api("/api/commands", { idempotency_key: crypto.randomUUID(), expected_project_version: snapshot.intent.project_version, operation, payload });
  await refresh();
}
function renderEarlierHistory() {
  const titles = { "record-intent": "Intent saved", "add-exploration": "Exploration recorded", "propose-spec": "Scope proposed", "approve-spec": "Scope approved", "execution-budget-event": "Execution allowance record" };
  const versions = new Map(history.events.map((row) => [row.event_id, row.project_version]));
  const rows = history.events.slice().reverse().slice(historyShown, historyShown + 20);
  for (const row of rows) {
    const detail = document.createElement("details");
    detail.append(text("summary", `Version ${row.project_version} · ${titles[row.operation] || row.operation}`));
    detail.append(text("p", `${row.actor.identity} (${row.actor.role}) · ${new Date(row.created_at).toLocaleString()}`, "muted"));
    if (row.record.wording) detail.append(text("blockquote", row.record.wording));
    if (row.operation === "execution-budget-event") {
      const data = row.record.data;
      if (row.record.kind === "approved") detail.append(text("p", `Approved allowance: $${(data.limit_microusd / 1000000).toFixed(2)}, up to ${data.max_calls} attempts. Earlier spending: ${data.opening.status === "verified-empty" ? "no prior scope execution observed" : "unknown"}.`));
      if (row.record.kind === "reserved") detail.append(text("p", `Reserved $${(data.microusd / 1000000).toFixed(2)} for ${data.role}, attempt ${data.attempt}. This charge is retained across strategy changes.`));
      if (row.record.kind === "started") detail.append(text("p", "Permission for this attempt was consumed once. Its actual outcome may still be unknown."));
      if (row.record.kind === "observed") detail.append(text("p", data.reported_microusd === null ? "Attempt ended with spending unresolved. The reservation remains charged." : `Reported cost: $${(data.reported_microusd / 1000000).toFixed(2)}. The full reservation remains charged.`));
    }
    if (row.record.spec) {
      const spec = row.record.spec;
      detail.append(text("h3", spec.title), text("p", spec.outcome));
      list(detail, "Acceptance", spec.requirements.flatMap((requirement) => requirement.acceptance.map((acceptance) => acceptance.text)));
      list(detail, "Constraints", spec.constraints);
      list(detail, "Outside scope", spec.non_goals);
      list(detail, "Assumptions", row.record.assumptions);
      list(detail, "Open product questions", row.record.open_questions);
      list(detail, "Technical questions", row.record.technical_questions);
    }
    if (row.basis.length) detail.append(text("p", `Based on ${row.basis.map((id) => `version ${versions.get(id)}`).join(", ")}.`, "muted"));
    if (row.supersedes) detail.append(text("p", `Replaces the approval at version ${versions.get(row.supersedes)}.`, "muted"));
    const identity = document.createElement("details");
    identity.append(text("summary", "Record identity"), text("p", row.event_sha256, "hash"));
    if (row.spec_sha256) identity.append(text("p", `Scope: ${row.spec_sha256}`, "hash"));
    detail.append(identity);
    $("history").append(detail);
  }
  historyShown += rows.length;
  $("earlier-history").hidden = historyShown >= history.events.length;
}
async function perform(action) {
  if (busy) return;
  busy = true;
  setButtons();
  try { await action(); } catch (error) { message(error.message, true); }
  finally { busy = false; setButtons(); }
}
function setButtons() {
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = button.closest("#stop-form") ? stopBusy : button.id === "logout" ? false : ["prepare", "recover"].includes(button.id) ? busy || preparationBusy : button.id === "synthesize" ? busy || synthesisBusy : busy;
  });
}
async function performStop(action) {
  if (stopBusy) return;
  stopBusy = true;
  setButtons();
  try { await action(); } catch (error) { message(error.message, true); }
  finally { stopBusy = false; setButtons(); }
}
$("login-form").addEventListener("submit", (event) => { event.preventDefault(); perform(async () => { token = $("owner-token").value.trim(); $("owner-token").value = ""; await refresh(); message(""); }); });
$("logout").addEventListener("click", () => { token = ""; snapshot = null; location.reload(); });
$("refresh").addEventListener("click", () => perform(refresh));
$("load-history").addEventListener("click", () => perform(async () => {
  history = await api("/api/history"); historyShown = 0; $("history").replaceChildren();
  $("history-state").textContent = history.events.length ? `History through version ${history.project_version}. Approvals record scope; execution evidence is shown separately.` : "No decisions have been saved.";
  renderEarlierHistory();
}));
$("earlier-history").addEventListener("click", renderEarlierHistory);
$("prepare").addEventListener("click", async () => {
  if (busy || preparationBusy) return;
  const version = snapshot.intent.project_version;
  preparationBusy = true;
  setButtons();
  message("Preparing scope and auditing intent. You can refresh, save a clarification or request a stop.");
  try {
    await api("/api/prepare", { idempotency_key: crypto.randomUUID(), expected_project_version: version });
    await refresh();
    message("Preparation result recorded. Review the current intent and draft before approving.");
  } catch (error) { message(error.message, true); }
  finally { preparationBusy = false; setButtons(); }
});
$("intent-form").addEventListener("submit", (event) => { event.preventDefault(); perform(async () => { await command("record-intent", { wording: $("intent").value }); $("intent").value = ""; message("Original intent saved. No scope has been approved by saving it."); }); });
$("recovery-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || preparationBusy || !snapshot.preparation_recovery) return;
  const offer = snapshot.preparation_recovery;
  preparationBusy = true; setButtons();
  message("Preparing one replacement draft. The previous attempt is retained; refresh and stop remain available.");
  try {
    await api("/api/prepare-recovery", { idempotency_key: crypto.randomUUID(), expected_project_version: offer.expected_project_version, failed_preparation_sha256: offer.failed_preparation_sha256, reason: "Owner requested one replacement draft and independent audit, up to $2 total." });
    await refresh(); message("Replacement attempt recorded. Review the current draft before approving scope.");
  } catch (error) { message(error.message, true); }
  finally { preparationBusy = false; setButtons(); }
});
$("synthesize").addEventListener("click", async () => {
  if (busy || synthesisBusy) return;
  const version = snapshot.intent.project_version;
  const approval = snapshot.intent.approvals.at(-1);
  synthesisBusy = true; setButtons();
  message("Preparing an execution programme for the approved scope. Refresh, clarification and stop remain available.");
  try {
    await api("/api/programme-prepare", { idempotency_key: crypto.randomUUID(), expected_project_version: version, approval_version: approval.project_version, spec_sha256: approval.spec_sha256 });
    await refresh(); message("Programme preparation recorded. Delivery and execution still require protected-branch review.");
  } catch (error) { message(error.message, true); }
  finally { synthesisBusy = false; setButtons(); }
});
async function showPublication(review, path = "/api/publication-preview") {
  publicationPreview = await api(path, review);
  publicationRequestId = publicationPreview.request_id || crypto.randomUUID().replaceAll("-", "");
  const target = $("publication-review"); target.replaceChildren();
  target.append(text("h3", "Publication destination"), text("p", `${publicationPreview.destination.repository} · ${publicationPreview.destination.visibility} repository`));
  target.append(text("p", "The approved specification and programme below will be visible to everyone who can read that repository. Your original interview and approval wording are excluded."));
  if (publicationPreview.input.version === "1.1") {
    const strategy = publicationPreview.input.strategy;
    target.append(text("h3", "Included planning advice"), text("p", strategy.candidate.mechanism));
    list(target, "Uncertainty still to resolve", strategy.remaining_uncertainty);
    target.append(text("p", "This recommendation can guide planning and design. All independent proof gates still apply.", "muted"));
  }
  const details = document.createElement("details");
  details.append(text("summary", "Exact content to publish"), text("pre", JSON.stringify(publicationPreview.input, null, 2), "hash"));
  target.append(details, text("p", `Content identity: ${publicationPreview.input_sha256}`, "hash"));
  const explanations = { "already-active": "This approved scope is already active. No duplicate programme or new execution will be created.", "requires-governed-replacement": "A different programme is active. Replacing it requires the governed replacement process, which is not yet connected here.", "requires-reconciliation": "An earlier publication reservation is no longer current. Its outcome must be reconciled before a new request; no work has been restarted." };
  if (explanations[publicationPreview.state]) target.append(text("p", explanations[publicationPreview.state], "muted"));
  const replan = publicationPreview.replanning;
  if (replan && replan.disposition !== "unchanged") {
    target.append(text("h3", "Different execution plan for the same approved scope"));
    target.append(text("p", "The proposal changes how work is divided, ordered or approached. The active programme, its work budget and its recorded outcomes remain in place. Applying this proposal requires a governed transition; this review grants no execution or proof authority."));
    list(target, "New work items in this proposal", replan.added_items);
    list(target, "Existing items replaced by this proposal", replan.retired_items);
    list(target, "Items whose scope or dependencies change", replan.changed_items);
    list(target, "Acceptance coverage", replan.coverage.map((row) => `${row.acceptance}: ${row.current_item} → ${row.proposed_item}`));
    for (const row of replan.dependency_changes) target.append(text("p", `${row.item}: after ${row.current.join(", ") || "nothing"} → after ${row.proposed.join(", ") || "nothing"}`));
    if (replan.strategy?.changed) {
      target.append(text("h3", "Planning advice changes"));
      target.append(text("p", `Current: ${replan.strategy.current?.mechanism || "No attached strategy"}`));
      target.append(text("p", `Proposed: ${replan.strategy.proposed?.mechanism || "No attached strategy"}`));
      target.append(text("p", "Strategy identity includes its assumptions, rationale, uncertainty and source references. None of these qualify the proposed work.", "muted"));
    }
    target.append(text("p", "Historical proof remains attached to the original programme and subject. It does not qualify this proposed plan.", "muted"));
    const reviewRequest = publicationPreview.review;
    const inspect = text("button", "Inspect replacement obligations"); inspect.type = "button";
    inspect.addEventListener("click", () => perform(async () => {
      const report = await api("/api/programme-replacement-review", reviewRequest);
      const result = document.createElement("section");
      const completed = report.preserved_completed_work.length, pending = report.pending_work.length;
      result.append(text("h3", "Replacement obligations observed"),
        text("p", `${completed} completed item${completed === 1 ? "" : "s"} verified against original receipts; ${pending} pending item${pending === 1 ? "" : "s"}.`));
      const reasons = {"completed-work-changed": "Completed work would change", "open-pending-work": "Existing work is still open", "active-worker": "A worker has not finished", "open-app-pull": "A factory pull request remains open"};
      list(result, "Current blockers", report.blockers.map((row) => `${reasons[row.kind] || row.kind}: ${row.item_id || row.run_id || row.pr}`));
      result.append(text("p", "This review does not pause work or approve replacement. Before switching, the factory must prevent old work from continuing, account for all spending and verify that only the replacement can run. The replacement must pass fresh qualification.", "muted"));
      const budget = report.exploration_budget.budget;
      result.append(text("p", budget ? `Exploration budget retained: ${budget.calls} calls, $${budget.usd} reserved. ${budget.uncertain ? "Spend remains uncertain." : "No refund or reset."}` : "No exploration budget has been recorded for this approved scope."));
      const record = document.createElement("details");
      record.append(text("summary", "Exact replacement review"), text("pre", JSON.stringify(report, null, 2), "hash"));
      result.append(record); target.append(result);
    }));
    target.append(inspect);
  }
  $("publish-check").checked = false;
  $("publication-form").hidden = publicationPreview.state !== "ready-for-consent";
  message("Review the destination and exact public content before deciding.");
}
$("preview-publication").addEventListener("click", () => perform(async () => {
  const synthesis = snapshot.synthesis;
  const command = synthesis.identity.command;
  const review = { expected_project_version: command.expected_project_version, approval_version: command.approval_version, spec_sha256: command.spec_sha256, proposal: synthesis.review.input.proposal };
  await showPublication(review);
}));
$("publication-form").addEventListener("submit", (event) => { event.preventDefault(); perform(async () => {
  if (!publicationPreview || publicationPreview.state !== "ready-for-consent") throw new Error("Review publication again before submitting.");
  const result = await api("/api/programme-publish", { request_id: publicationRequestId, review: publicationPreview.review, destination: publicationPreview.destination });
  $("publication-form").hidden = true; $("publish-check").checked = false;
  publicationPreview = null;
  await refresh();
  message(result.state === "already-active" ? "This approved scope is already active; no duplicate work was dispatched." : result.state === "requires-governed-replacement" ? "The active programme was preserved. Governed replacement is required." : "Publication request recorded. Observe its workflow and execution evidence below.");
}); });
$("exploration-form").addEventListener("submit", (event) => { event.preventDefault(); perform(async () => { await command("add-exploration", { wording: $("exploration").value }); $("exploration").value = ""; message("Exploration saved outside approved scope."); }); });
$("approval-form").addEventListener("submit", (event) => { event.preventDefault(); perform(async () => { const draft = snapshot.intent.draft; await command("approve-spec", { draft_version: draft.draft_version, spec_sha256: draft.spec_sha256, wording: "This scope represents what I want to build." }); message("This exact scope is approved. Programme delivery and execution evidence remain separate."); }); });
$("stop-form").addEventListener("submit", (event) => { event.preventDefault(); performStop(async () => { await api("/api/stop", { request_id: crypto.randomUUID().replaceAll("-", ""), reason: $("stop-reason").value }); stopRequested = true; message("Stop requested. Refresh evidence to confirm the remote stop is active."); }); });
