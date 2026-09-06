# PROGRAMME.md — the approved engineering programme

This file records **what Dark Factory is being built toward and in what order**. It is governance,
not runtime: the kernel does not read it. `MISSION.md` says what the product is, `FACTORY_RULES.md`
how the factory operates, `CLAUDE.md` how code is written, and `.factory/decisions.md` what was
decided and why. This file says **what happens next, and what must be true first**.

Protected, like the other three: only the human maintenance lane may change it (D-067).

---

## 0. The end state

> A software engineering factory that can understand intent, decompose programmes, explore
> competing technical strategies, predict their consequences, rigorously prove the selected
> implementation, preserve the full decision history, learn from actual outcomes, and visibly
> maintain a living model of the software system it is building.

The organising principle: **explore cheaply outside the trusted factory, prove rigorously inside
it.** Everything upstream of the trust boundary is untrusted and may be wrong. Nothing upstream may
qualify work, relax a gate, or change user-approved intent.

---

## 1. Where the factory actually is (2026-09-06)

Verified against the repository, not assumed. `main` is `ed16952` (D-065).

**Proven to work, unattended:** triage, investigate, contract, context, architecture, RED, GREEN,
two-axis review, conformance, provenance publish, PR handoff, trusted-base validation, five blinded
judges, exact-head auto-merge, and the model-free re-head. One issue (#49) has been carried end to
end by the factory and its PR (#112) is certified and waiting.

**Not yet proven:** a single unattended issue from `factory:accepted` to merged. Issue #103 has
failed eleven builds. Every failure was a distinct factory defect, and every one is now fixed
(D-050 through D-065) or in flight (D-066). No product defect has yet been found by this process;
the factory has so far been debugging itself.

**Therefore:** the Level-4 floor is not set, and no phase below B may begin.

### Gap against the roadmap

| Roadmap capability | Today |
|---|---|
| Trajectory capture | Provenance notes bind merged heads only (11 notes against 44 runs). Run artifacts expire after 7 days. A failure before a PR leaves nothing durable. A manual archive of 40 runs exists outside the repo. |
| Learning layer | None. The decisions log (65 entries) and the overseer's defect log are its manual precursor. |
| Concurrency | One build at a time under a single worker concurrency group. Per-stage leases exist; a per-issue lease and a concurrency budget do not. |
| Front door | None. The factory expects a well-formed issue and triage rejects ambiguity. That is correct inside the boundary and no substitute for an intake. |
| Programme synthesis | Execution exists (`Part of #N`, `Blocked by: #N`, ready-frontier dispatch). Synthesis does not. |
| Preflight strategy lab | None. One design per issue. |
| Long-horizon architect | Absent. The deterministic governor and the blind architecture holdout exist and stay as they are. |
| Project decision graph | Absent. State lives in issues, pull requests, artifacts and git notes. |
| Live UI | Absent. |
| Calibration | Absent, because nothing predicts yet. Stage telemetry (turns, seconds, cost, thinking tokens, model, per-attempt records) is the substrate it will need. |

---

## 2. Phases and their dependencies

Each phase becomes bounded issues with explicit `Blocked by:` links when it is reached. A phase may
not start before its blockers close. A and B are prerequisites for everything.

```
A -> B -> C -> D -> E ------------------> S
          |                     
          +-> F -+-> K -> L -> M,N -> O -> T
                 |         |
                 |         +-> P -> Q -> R
                 +-> G -> H -> I -> J -> K
```

**A. Finish the in-flight work.** #103 merged at its exact head; #112 relabelled, re-headed,
revalidated and merged; #119 closed; a green main regression.

**B. The single-path floor.** Ratchet every observed floor together (unit count, E2E steps,
mutations), but only after the browser journey exercises the production-shaped built SPA, because a
floor set against the Vite dev server pins the wrong runtime. Record the reliability and cost
baseline from the archived corpus. This is the definition of qualified; it is not to be redefined
in order to unblock the roadmap.

**C. Durable trajectory capture.** Canonical schema, durable store, retention, indexing, hashes and
redaction. Every meaningful attempt, including failures before a PR. Passive only: nothing captured
may change a trusted decision. Volatile run artifacts stay out of the product tree.

**D. Trajectory analytics.** Read-only. Per-stage distributions, failure classes, repair counts,
cost and latency by role and model.

**E. Learning schema.** Two separate categories: *factory knowledge* (which model, which effort,
which reviewer earns its keep) and *software knowledge* (what makes this product's code fail). Each
lesson carries scope, supporting runs, contradicting runs, confidence, a falsifier, a date and a
dormancy rule. Factory knowledge comes first, because that is what the corpus is currently evidence
about.

**F. Concurrent execution.** Per-issue leases, isolated workspaces, explicit ownership of mutable
artifacts, concurrency and compute budgets, a deterministic ready frontier, duplicate-execution
protection, safe retry, independent provenance per candidate, and clear cancellation. Built once
and used by both parallel programme work and parallel candidate probes.

**G. Front door.** An untrusted interview that asks only genuine product-owner questions, resolves
everything else from the repository, recommends a default for every question, keeps a durable
ledger, and records who proposed and who confirmed each decision. Accepting a recommendation is the
user's decision; provenance records only who proposed it.

**H. Authoritative specification.** Human-readable for approval and machine-readable for the
compiler: mission, problem, users, required outcomes, explicit non-goals, hard constraints,
preferences, acceptance criteria, unresolved items, decision provenance. A deterministic compiler
validates and hashes it; triage and contract consume the canonical form, never free text.

**I and J. Programme synthesis and its compiler.** A model proposes the decomposition; a
deterministic authority validates it before a single issue is created: acyclic, dependencies
resolve, acceptance conserved by hash, tasks bounded, no silent scope change, and idempotent
materialisation keyed by programme and ticket identity. The plan may change *how* V1 is built; it
may never change *what* V1 means.

**K. Parallel programme execution.** F applied to the ready frontier.

**L, M and N. Preflight strategy lab.** Untrusted, risk-triggered, never for trivial work. A
fidelity ladder: historical prediction, reasoning rollouts, deterministic static analysis, then
disposable probes. Measured facts, predictions and judgements are labelled as such and never
blended into one invented number. The selection rule is pre-registered before results are seen.
Losing candidates are retained with their evidence and the conditions that would make them
preferable later. The long-horizon architect lives here and is untrusted; the governor still
enforces and the holdout still challenges, blind to all of it.

**O. Calibration.** Store every prediction beside the real outcome. Track whether the confidence
was right, not only whether the estimate was.

**P and Q. Decision graph and UI.** The graph must reflect real state. The interface must never
render predicted, probed or simulated as proven.

**R. Reconsideration.** New evidence reopens an earlier decision, re-evaluates the rejected options
under the new constraints, and sends any resulting change through the normal trusted path.

**S and T. Retrieval and training.** Retrieval serves drafting and mutation roles only, capped and
measured. The learning store stays blind to every judge. Training comes last.

---

## 3. Invariants this programme may not trade away

Predictions are not proof. Preflight cannot qualify work. Learning cannot certify itself. An
implementation cannot judge itself. Blinded judges stay blind, and no lesson, score or prior verdict
reaches them. Trusted claims stay bound to an exact revision and its evidence. Rejected alternatives
stay inspectable. The factory may decide technical implementation freely; it may not silently change
user-approved intent or a hard constraint. Nothing is displayed as passed, verified, proven or
qualified unless the authority that owns that word produced it.

---

## 4. How this file changes

By maintainer pull request, with a decision entry recording what changed and why. The programme plan
and its tickets are mutable. The mission, the user's approved acceptance criteria and the hard
constraints are not.
