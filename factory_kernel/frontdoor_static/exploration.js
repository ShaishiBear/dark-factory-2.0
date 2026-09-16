"use strict";
let explorationState = null;
let explorationPoll = null;
async function refreshExploration() {
  clearTimeout(explorationPoll);
  $("adaptive-card").hidden = !snapshot?.exploration_available;
  if (!snapshot?.exploration_available) return;
  try {
    explorationState = await api("/api/exploration");
    if (!token) return;
    renderExploration();
    if (explorationState.runs.some((run) => run.observation === "running")) {
      explorationPoll = setTimeout(() => {
        if (token) refreshExploration(); // Read-only observation; no automatic start or retry.
      }, 5000);
    }
  } catch (error) {
    explorationState = null;
    $("adaptive-open").hidden = true;
    $("adaptive-sessions").replaceChildren();
    $("adaptive-runs").replaceChildren();
    $("adaptive-state").textContent = "Exploration observation unavailable. Refresh before issuing another command.";
  }
}
function explorationCommand(session, request) {
  if (!explorationState) throw new Error("Refresh exploration before continuing.");
  return {idempotency_key: crypto.randomUUID(), expected_project_version: explorationState.project_version,
    session_id: session, request};
}
async function explorationAction(operation, session, request) {
  const command = explorationCommand(session, request);
  try {
    await api(`/api/exploration/${operation}`, command);
    message(operation === "start" ? "Bounded investigation recorded. Refresh and stop remain available." : "Exploration decision recorded.");
  } finally {
    // A temporarily unavailable observation must not be described as a failed command.
    try { await refresh(); } catch (error) {
      message("Command response observed, but current evidence is unavailable. Refresh before another command; no request was repeated.", true);
    }
  }
}
function explorationButton(parent, label, operation, session, request) {
  const button = text("button", label); button.type = "button";
  button.addEventListener("click", () => perform(() => explorationAction(operation, session, request)));
  parent.append(button);
}
function renderExploration() {
  const state = explorationState;
  const blocked = state.runs.some((run) => run.status === "running");
  const running = state.runs.some((run) => run.observation === "running");
  $("adaptive-state").textContent = state.ready ? "Planning evidence only · UNPROVEN. No work is published by exploring." : state.reason;
  if (state.budget) $("adaptive-state").textContent += ` Reserved ${state.budget.calls}/${state.budget.limits.calls} calls and $${state.budget.usd}/$${state.budget.limits.usd}.`;
  $("adaptive-open").hidden = !state.ready || blocked;
  const policy = $("adaptive-policy"); policy.replaceChildren();
  if (state.ready) {
    const frozen = state.default_policy;
    list(policy, "Comparison priorities, in order", frozen.priorities.map((id) => frozen.criteria.find((row) => row.id === id).question));
    policy.append(text("p", `Qualitative judgments; no measured performance claims. Shared limit for this approved scope: ${frozen.budget.calls} calls, $${frozen.budget.usd}, ${frozen.budget.probe_units} probe work units. Up to ${frozen.max_candidates} alternatives and ${frozen.max_rounds} investigation rounds.`));
    if (state.budget) policy.append(text("p", `Already reserved: ${state.budget.calls} calls and $${state.budget.usd}. ${state.budget.uncertain ? "Spend is uncertain; further paid work is frozen." : "Opening another question does not reset this budget."}`, "muted"));
  }
  const sessions = $("adaptive-sessions"); sessions.replaceChildren();
  for (const session of state.sessions) {
    const card = document.createElement("section");
    card.append(text("h3", session.question), text("p", `${session.status} · round ${session.round}`, "badge"));
    list(card, "Frozen comparison priorities", session.policy.priorities.map((id) => session.policy.criteria.find((row) => row.id === id).question));
    for (const outcome of (state.factory_outcomes || []).filter((row) => row.session_id === session.id)) {
      const observed = outcome.observation;
      card.append(text("h4", "Authenticated factory refusal"),
        text("p", `PR #${observed.receipt.pr}, run ${observed.receipt.run_id}, attempt ${observed.receipt.run_attempt}: ${observed.refusal.reason_code}.`),
        text("p", "The factory reported a refusal. Its cause is unresolved; this does not reject the strategy or qualify a replacement.", "muted"));
      const details = document.createElement("details"); details.append(text("summary", "Exact revision and evidence bindings"), text("pre", JSON.stringify(outcome, null, 2))); card.append(details);
    }
    if (session.handoffs.length) {
      const form = document.createElement("form");
      form.append(text("h4", "Import a factory outcome"), text("p", "Verify a current completed run against its retained evidence. Expired evidence, changed revisions and incomplete records are refused."));
      const fields = {};
      for (const [key, title] of [["run_id", "Workflow run ID"], ["attempt", "Run attempt"], ["pr", "Pull request number"]]) {
        const label = text("label", title); const input = document.createElement("input");
        input.type = "number"; input.min = "1"; input.step = "1"; input.required = true;
        if (key === "attempt") input.value = "1";
        label.append(input); form.append(label); fields[key] = input;
      }
      const label = text("label", "Programme item"); const item = document.createElement("select");
      for (const row of session.handoffs[session.handoffs.length - 1].input.proposal.items) {
        const option = text("option", row.id); option.value = row.id; item.append(option);
      }
      label.append(item); form.append(label);
      const submit = text("button", "Verify and import outcome"); submit.type = "submit"; form.append(submit);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const request = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, Number(input.value)]));
        if (!Object.values(request).every(Number.isSafeInteger)) { message("Enter valid whole-number run and PR identities.", true); return; }
        perform(() => explorationAction("import-feedback", session.id, {...request, item_id: item.value}));
      });
      card.append(form);
    }
    for (const candidate of Object.values(session.candidates)) {
      const result = session.comparison.candidates[candidate.id];
      card.append(text("h4", candidate.mechanism), text("p", `${candidate.baseline ? "Baseline · " : ""}${result.status}`));
      for (const [criterion, value] of Object.entries(result.values)) {
        card.append(text("p", `${criterion}: ${value.assessment || `${value.low}–${value.high}`} (${value.kind}). ${value.basis}`));
      }
    }
    const recommendation = session.recommendations.at(-1);
    if (recommendation) {
      card.append(text("h4", "Recorded recommendation"), text("p", recommendation.rationale));
      list(card, "Remaining uncertainty", recommendation.remaining_uncertainty);
      card.append(text("p", `Next useful experiment: ${recommendation.next_useful_experiment}`));
    }
    if (session.handoffs.length) card.append(text("p", "A strategy handoff is recorded. Refresh evidence and use the separate publication review when it is current.", "muted"));
    const pending = Object.values(session.reservations).filter((row) => row.status === "pending");
    const handoffCurrent = session.handoff_current;
    if (!blocked && !pending.length && !handoffCurrent && !state.budget?.uncertain && ["exploring", "recommended"].includes(session.status)) {
      const form = document.createElement("form");
      const label = text("label", "Maximum reasoning steps (up to $1 reserved per call)");
      const steps = document.createElement("select");
      for (const count of [1, 2, 4, 8]) { const option = text("option", `${count} step${count === 1 ? "" : "s"} · up to $${count}`); option.value = count; steps.append(option); }
      label.append(steps); form.append(label, text("p", "The shared budget can stop this sooner. A result may require more evidence or an owner tradeoff; no automatic continuation.", "muted"));
      const submit = text("button", "Run bounded investigation"); submit.type = "submit"; form.append(submit);
      form.addEventListener("submit", (event) => { event.preventDefault(); perform(() => explorationAction("start", session.id, {max_steps: Number(steps.value), max_usd_per_call: 1})); });
      card.append(form);
    }
    if (!running) for (const reservation of pending) {
      card.append(text("p", "An earlier effect has no usable completion. Closing it keeps its full reservation charged and freezes further paid work when spend is unknown.", "muted"));
      explorationButton(card, "Close uncertain reservation without retry", "abandon", session.id,
        {reservation_id: reservation.id, reason: "Owner closed uncertain work without retry, refund or qualification."});
    }
    if (!blocked && !pending.length) explorationButton(card, "Reopen against current repository", "reopen", session.id,
      {reason: "Owner requested reconsideration against current repository context within the existing limits."});
    sessions.append(card);
  }
  const runs = $("adaptive-runs"); runs.replaceChildren();
  for (const run of state.runs.slice(-10).reverse()) {
    runs.append(text("p", `Investigation: ${run.observation}. ${run.reason || "Bounded work reserved; no completion confirmed yet."}`));
    if (state.ready && run.observation === "interrupted") explorationButton(runs,
      "Close interrupted job without restarting", "recover", run.session_id, {run_id: run.id});
  }
  setButtons();
}
$("adaptive-open").addEventListener("submit", (event) => {
  event.preventDefault();
  perform(async () => {
    const session = "question-" + crypto.randomUUID().replaceAll("-", "");
    await explorationAction("open", session, {question: $("adaptive-question").value,
      parent_session: null, policy: explorationState.default_policy});
    $("adaptive-question").value = ""; $("adaptive-policy-check").checked = false;
  });
});
