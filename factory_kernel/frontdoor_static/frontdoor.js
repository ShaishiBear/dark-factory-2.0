"use strict";
let token = "";
let snapshot = null;
let busy = false;
let preparationBusy = false;
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
  $("progress").replaceChildren();
  $("observation").textContent = snapshot.observation_available ? `GitHub observed ${snapshot.observed_at ? new Date(snapshot.observed_at).toLocaleString() : "just now"}. Refresh to check for changes.` : "GitHub observation is unavailable. Completion and stop state are unknown.";
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
  $("stop-state").textContent = snapshot.stop?.state === "stopped" ? `Stop observed on GitHub: ${snapshot.stop.issues.map((n) => `#${n}`).join(", ")}.` : snapshot.stop ? "No open remote stop was observed." : "Stop state is unknown.";
  if (stopRequested && snapshot.stop?.state === "stopped") {
    stopRequested = false;
    message("Remote stop confirmed. Running work will stop at its next checkpoint.");
  }
}
async function refresh() { snapshot = await api("/api/snapshot"); render(); }
async function command(operation, payload) {
  await api("/api/commands", { idempotency_key: crypto.randomUUID(), expected_project_version: snapshot.intent.project_version, operation, payload });
  await refresh();
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
    button.disabled = button.closest("#stop-form") ? stopBusy : button.id === "logout" ? false : button.id === "prepare" ? busy || preparationBusy : busy;
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
$("exploration-form").addEventListener("submit", (event) => { event.preventDefault(); perform(async () => { await command("add-exploration", { wording: $("exploration").value }); $("exploration").value = ""; message("Exploration saved outside approved scope."); }); });
$("approval-form").addEventListener("submit", (event) => { event.preventDefault(); perform(async () => { const draft = snapshot.intent.draft; await command("approve-spec", { draft_version: draft.draft_version, spec_sha256: draft.spec_sha256, wording: "This scope represents what I want to build." }); message("This exact scope is approved. Programme delivery and execution evidence remain separate."); }); });
$("stop-form").addEventListener("submit", (event) => { event.preventDefault(); performStop(async () => { await api("/api/stop", { request_id: crypto.randomUUID().replaceAll("-", ""), reason: $("stop-reason").value }); stopRequested = true; message("Stop requested. Refresh evidence to confirm the remote stop is active."); }); });
