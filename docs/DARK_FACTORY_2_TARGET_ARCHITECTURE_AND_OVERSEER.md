# Dark Factory 2.0 — Target Architecture and Overseer Operating Directive

**Status:** architectural north star / owner-approved direction  
**Purpose:** stop architectural rediscovery, prevent expensive qualification loops, and give every future overseer/agent one durable source of truth.  
**Important:** this document defines target architecture and operating constraints. It does **not** itself waive current qualification gates, authorize merges, or override repository evidence.

---

## 0. Authority and use

When working on Dark Factory 2.0, use this priority order:

1. The user's approved product intent and explicit owner decisions.
2. This target-architecture directive.
3. Repository-protected architectural decisions / ADRs and current trust policy.
4. Current programme/dependency graph and bounded work items.
5. Disposable implementation tickets and agent plans.

If implementation evidence shows that a specific implementation detail here is infeasible or inferior, preserve the architectural invariant and choose a better implementation. Do not casually overturn a locked boundary. If a locked boundary itself appears wrong, stop and present:

- decision affected;
- new evidence;
- why the current decision fails;
- alternatives;
- recommended amendment;
- consequences.

Do not silently drift the architecture.

Do **not** repeatedly debate the locked architecture from first principles. Create bounded implementation work that references this document and the relevant ADRs.

---

# PART I — SYSTEM ARCHITECTURE

## 1. Fundamental split

Dark Factory 2.0 is not one giant autonomous agent. It is a system with distinct authority levels:

```text
┌─────────────────────────────────────────────┐
│ PRODUCT / USER INTELLIGENCE                 │
│                                             │
│ Front Door / Grill                          │
│ Programme Synthesiser                       │
│ Preflight Strategy Lab                      │
│ Long-Horizon Architect                      │
│ Experience / Learning Layer                 │
│ Project Decision Graph + UI                 │
└──────────────────────┬──────────────────────┘
                       │ proposals
                       ▼
┌─────────────────────────────────────────────┐
│ ORCHESTRATOR                                │
│                                             │
│ scheduling / retries / leases               │
│ concurrency / provider routing              │
│ worktrees / cost control                    │
│ issue / PR workflow                         │
└──────────────────────┬──────────────────────┘
                       │ requested actions
                       ▼
┌─────────────────────────────────────────────┐
│ SMALL TRUSTED KERNEL                        │
│                                             │
│ state transitions                           │
│ identity / hashes                           │
│ capability authorisation                    │
│ evidence requirements                       │
│ authority requirements                      │
│ exact-revision merge authorisation          │
└──────────────────────┬──────────────────────┘
                       │ proof
                       ▼
┌─────────────────────────────────────────────┐
│ INDEPENDENT AUTHORITIES / EVIDENCE          │
│                                             │
│ deterministic gates                         │
│ RED / GREEN proof                           │
│ architecture governor                       │
│ independent holdouts / certifiers           │
│ mutation / immunity                         │
│ post-merge verification                     │
└─────────────────────────────────────────────┘
```

Core rule:

> **Intelligence may propose. Only the trusted proof system may authorise.**

The kernel should become smaller, more deterministic and easier to reason about over time. Creative planning, learning, retrieval, exploration and long-horizon reasoning belong outside it.

---

## 2. Existing trust model must survive

Preserve these existing invariants:

- deterministic trust boundaries;
- immutable / reproducible evidence;
- TDD RED before GREEN;
- independent review axes;
- architecture governance;
- mutation / immunity testing;
- exact-head validation and merge;
- post-merge verification;
- fail closed when required proof is absent;
- blinded authorities do not receive contaminating prior verdicts or learned lessons;
- model workers never become merge authorities.

Do not “speed up” the factory by deleting proof. Speed it up by avoiding unnecessary work, ordering cheap checks before expensive checks, running independent work concurrently, and reusing only evidence whose identity and validity are provable.

---

## 3. Front Door / “Grill Me”

Before normal factory execution, an interactive intake layer should convert vague human intent into an authoritative approved specification.

Flow:

```text
idea / request
  → interactive grill
  → researched clarification
  → proposed defaults
  → authoritative approved spec
  → programme synthesis
```

Rules:

- ask one meaningful owner question at a time;
- ask only genuine product-owner questions that repository research cannot resolve;
- technical questions should be researched automatically where possible;
- recommend sensible defaults rather than dumping choices on the user;
- separate hard requirements, preferences, V1 scope and optional ideas;
- unresolved optional ideas default **out**;
- user can revise prior answers;
- every decision has provenance;
- the final spec has a human-readable form and machine-readable validated form;
- the user approves the spec before programme execution.

Front Door is untrusted. It may inspect, research, clarify and propose. It cannot code, merge, self-approve or qualify.

---

## 4. Programme synthesis

After spec approval:

```text
approved spec
  → programme synthesiser
  → candidate decomposition
  → deterministic DAG compiler
  → bounded child work items
```

The LLM proposes decomposition. A narrow deterministic compiler validates:

- no cycles;
- dependencies resolve;
- no lost requirements / acceptance criteria;
- bounded child scope;
- parent/blocker coherence;
- no hidden scope changes;
- no accidental duplicates;
- no impossible sequencing;
- no excessive authority.

Hierarchy:

```text
MISSION / approved spec / hard constraints
    >
programme plan
    >
disposable tickets
```

The programme may change **how** V1 is built. It cannot silently change **what V1 means**.

Later, programme health may support:
- blocker detection;
- discovered work;
- obsolete-task cancellation;
- decomposition revision;
- critical-path changes;
- safe parallel ready workstreams.

---

## 5. Concurrent execution / leases

Safe concurrency is a prerequisite for programme-wide parallelism and for multiple agents/users mutating project state.

Required:
- per-work-item leases;
- isolated worktrees/workspaces;
- deterministic ready frontier;
- ownership of mutable artifacts;
- no duplicate execution;
- concurrency and compute/model budgets;
- safe retry/recovery;
- independent provenance;
- no shared mutable trust artifacts between simultaneous candidates;
- cancellation/pruning.

Concurrency infrastructure comes **before** a reactive multi-user Project Decision Graph.

---

## 6. Preflight Strategy Lab

Consequential solution exploration should happen **outside** the expensive trusted factory.

Core principle:

> **Explore cheaply outside the trusted factory. Prove rigorously inside it.**

Flow:

```text
issue / programme item
  → Preflight Strategy Lab
  → candidate synthesis
  → cheap prediction / simulation / static analysis / disposable probes
  → prune
  → recommend
  ───────── TRUST BOUNDARY ─────────
  → normal Dark Factory
  → trusted proof
```

Preflight may:
- explore;
- predict;
- compare;
- use approved historical lessons;
- run disposable spikes/probes;
- estimate cost/time/risk;
- recommend.

Preflight may **not**:
- qualify;
- claim trusted tests passed;
- produce merge authority;
- waive architecture;
- change approved requirements;
- manufacture trusted provenance claims.

The trusted factory independently reconstructs the contract/context/design/RED/GREEN/reviews/conformance and may reject Preflight's recommendation.

### Multi-fidelity candidate ladder

**Fidelity 0 — historical prediction**  
Estimate from repo topology + trajectories:
- modules/files likely touched;
- boundary crossings;
- dependency additions;
- repair count;
- qualification risk;
- inference cost;
- wall time.

**Fidelity 1 — cheap reasoning rollouts**  
Predict likely:
- implementation path;
- interface impact;
- tests;
- failures;
- reviewer objections;
- repair steps.

Predictions are not facts.

**Fidelity 2 — deterministic static analysis**  
Measure:
- imports/dependencies;
- boundary crossings;
- coupling;
- manifest changes;
- test impact;
- architecture constraints.

**Fidelity 3 — disposable sandbox probes**  
Examples:
- compile an API seam;
- benchmark a library;
- verify a migration;
- type/API compatibility;
- concurrency micro-test.

No production PR and no trusted proof.

**Later — Virtual Factory model**  
Train a model to predict:
- likely gate failures;
- repairs;
- cost;
- duration;
- qualification probability.

Calibrate predictions against actual Dark Factory outcomes.

---

## 7. Candidate selection discipline

Do not invent precise-looking scores for subjective qualities.

Separate:
- **measured facts** — file count, diff size, benchmark, dependency growth, test runtime;
- **predictions** — repair burden, likely cost, regression probability, with uncertainty;
- **judgments** — simplicity/maintainability, clearly labelled.

Where possible, pre-register the candidate-selection rule or tie-break before final results are known.

Maintain a Pareto set when alternatives represent genuine trade-offs.

Trigger multi-option Preflight selectively:
- architecture changes;
- high uncertainty;
- multiple credible alternatives;
- migrations / irreversible choices;
- security or performance sensitivity;
- cross-cutting boundaries;
- large dependency additions.

Do not run a tournament for a typo or trivial bug.

---

## 8. Preserve losing candidates

Serious rejected alternatives are engineering evidence.

Retain:
- problem;
- repo/base state;
- candidate description;
- why it was credible;
- assumptions;
- probes/evidence;
- predictions;
- pruning reason;
- conditions under which it could become preferable.

Do not mark losers “qualified.” Preserve enough evidence to remain falsifiable.

---

## 9. Long-horizon architecture

Three architecture roles remain distinct:

### 9.1 Long-Horizon Architect — untrusted
Lives in Preflight. Asks:

> Which candidate best moves the codebase toward where it is intentionally going?

May see:
- current architecture;
- target architecture;
- planned migrations;
- technical debt;
- future programme work;
- historical lessons;
- reversibility.

Creative and predictive.

### 9.2 Architecture Governor — trusted/deterministic
Asks:

> Does the actual design/implementation obey current architecture policy?

A recommendation cannot override policy.

### 9.3 Architecture Holdout — independent/blind
Asks:

> Did the normal path miss an architecture problem?

Must not see:
- Preflight verdicts;
- historical lessons;
- candidate scores;
- other judge verdicts.

Short version:

> Long-horizon architect **predicts**. Governor **enforces**. Holdout **challenges**.

Represent architecture as:

```text
CURRENT ARCHITECTURE
   ↓ approved migrations/path
TARGET ARCHITECTURE
```

---

## 10. Durable trajectory capture

Before serious self-learning, capture **every meaningful attempt**, especially failures.

Where available record:
- run ID;
- issue / programme item;
- base/head;
- stage;
- model/provider;
- method/prompt/model versions;
- thinking/effort config;
- turns;
- wall time;
- inference cost;
- tool usage;
- contract/context/design/test plan;
- RED/GREEN evidence;
- implementation diff;
- reviewer/architecture findings;
- repairs;
- failure/termination reason;
- final outcome;
- merge/post-merge outcome.

Successful PR provenance already provides a seed, but failure paths must stop disappearing.

Trajectory evidence should be immutable enough to support later analysis and calibration.

---

## 11. Software Engineering Experience Layer

There are two learning domains:

### Software knowledge
Patterns about:
- coding;
- architecture;
- tests;
- migrations;
- dependencies;
- concurrency;
- failure/repair patterns.

### Factory knowledge
Patterns about:
- models/providers;
- methods/prompts;
- context selection;
- turn budgets;
- review strategy;
- failure probabilities;
- repair effectiveness;
- cost/latency.

Pipeline:

```text
immutable trajectories
  → analytics
  → candidate lessons
  → evidence + counter-evidence
  → historical holdout/backtest
  → approved knowledge
  → limited retrieval for drafting/mutation roles
```

Each lesson needs:
- scope;
- supporting runs;
- contradictory runs;
- confidence;
- falsifier;
- creation date;
- last-applicable evidence;
- expiry/dormancy rule.

### Critical independence rule

**Lessons never reach blinded judges.**

The lesson store belongs on the blind-path list. Retrieval is for drafting/mutation roles such as:
- investigation;
- design;
- test authoring;
- implementation;
- repair.

Retrieval consumes context/turn budget, so cap it to a small relevant set and measure whether it helps.

The learner may propose changes to methods/policy, but may not silently self-modify protected methods/trust boundaries. Proposed changes go through normal Dark Factory.

Factory-knowledge learning should pay first because the early corpus mostly reflects behaviour of this factory/repo. Generic software-engineering lessons require a larger corpus.

---

## 12. Calibration

Pair Preflight predictions with real outcomes:

- predicted cost vs actual;
- predicted duration vs actual;
- expected repairs vs actual;
- predicted gate failures vs actual;
- predicted qualification probability vs result.

Learn where the system is:
- overconfident;
- underconfident;
- overvalues abstraction;
- underestimates specific subsystems/dependencies.

---

## 13. Project Decision Graph

The graph is durable project reasoning state, not dashboard theatre.

### Zone A — Exploration
Nodes may include:
- project;
- feature/subfeature;
- requirement;
- constraint;
- question/subquestion;
- candidate;
- prediction;
- simulation;
- probe;
- evidence;
- recommendation;
- rejection.

Statuses may include:
- unexplored;
- evaluating;
- probing;
- promising;
- dominated;
- rejected;
- recommended.

### Zone B — Dark Factory
Visibly separate trust zone:
- triage;
- contract;
- context;
- architecture;
- RED;
- implementation;
- GREEN;
- review;
- repair;
- conformance;
- merge.

Statuses:
- queued;
- running;
- passed;
- failed;
- repairing;
- blocked.

### Zone C — Implemented Infrastructure
After merge, persist the result as:
- modules/services;
- interfaces;
- datastores;
- dependencies;
- infrastructure decisions;
- evidence;
- links back to feature/question/candidate.

Visually distinguish:
- **Predicted**
- **Probed**
- **Simulated**
- **Proven**

Potential edges:
- part-of;
- depends-on;
- answers;
- constrained-by;
- candidate-for;
- supported-by;
- rejected-because;
- recommended-because;
- implemented-as;
- caused-by;
- replaced-by;
- reconsidered-due-to.

---

## 14. User steering of the graph

The user may:
- **Add Question** — “you missed this; explore it.”
- **Add Direction** — add a candidate to an existing question.

Locked rules:

### 14.1 User additions default to exploration
The system must not silently classify a user-added question as a hard requirement.

It may propose classification:
- preference;
- possible requirement;
- hard constraint.

Promotion to approved requirement/hard constraint requires explicit user approval. Only then does authoritative spec identity change.

### 14.2 Equal evaluation, unequal reporting
A user-proposed candidate receives no score privilege or penalty.

If it is pruned/rejected, report why to the user. System-generated dominated candidates may disappear from the foreground but remain historical evidence.

### 14.3 Propagation follows recorded assumptions
Every recommendation must record its assumptions as first-class graph state.

Decision record should include:
- question;
- candidates;
- measurements;
- predictions;
- assumptions;
- constraints;
- winner;
- reason;
- rejected alternatives;
- revisit conditions;
- estimated exploration cost;
- actual exploration cost;
- provenance.

New information invalidates/challenges an assumption → deterministic dependency walk → only affected decisions reopen.

Do not rely on a model guessing “what might be affected” after the fact.

### 14.4 Every extra exploration has a visible price
Before extra exploration:
- estimate incremental cost;
- show it;
- auto-run below the owner's autonomous-spend threshold;
- require approval above it;
- record estimate vs actual.

### 14.5 Dependency ordering
Correct dependency:

```text
concurrency / leases
  → Project Decision Graph
  → assumption/dependency model
  → safe user/agent graph mutation
  → reactive targeted exploration
  → UI controls / animations
```

The graph is not merely a UI feature.

### 14.6 Trust remains unchanged
“Use option D” means explore/choose D as the candidate path. It is not permission to skip qualification.

---

## 15. Future architectural reconsideration

Preserve historical decisions, assumptions and rejected alternatives so future change can target the right reasoning.

Flow:

```text
incident / new requirement / scale change / dependency change / security or performance evidence
  → invalidate assumption(s)
  → reopen dependent historical decisions
  → reconsider old alternatives + new candidates
  → selected migration/change
  → normal trusted factory
```

This is architectural memory, not merely Git history.

---

# PART II — MIGRATION / IMPLEMENTATION ORDER

## 16. Recommended sequence

Do not build the flashy UI first.

Approximate order:

A. Finish the current #103-dependent qualification and establish a reliable single-path floor.  
B. Establish a qualification/reliability/cost baseline.  
C. Durable trajectory capture.  
D. Trajectory analytics.  
E. Learning/lesson schema.  
F. Safe concurrency + leases.  
G. Front Door / Grill Me.  
H. Authoritative approved spec.  
I. Programme synthesiser.  
J. Deterministic programme DAG compiler.  
K. Parallel programme execution.  
L. Preflight Strategy Lab.  
M. Long-Horizon Architect.  
N. Candidate evidence/probes.  
O. Prediction-vs-real calibration.  
P. Project Decision Graph.  
Q. Live graph UI.  
R. Reconsideration/reactive reopening.  
S. Mature experience retrieval.  
T. Later proprietary model training.

Implementation order may be refined by measured dependencies, but do not invert prerequisite relationships.

---

# PART III — OVERSEER OPERATING DIRECTIVE

## 17. Current problem: qualification is debugging itself expensively

The present overseer has found real defects, but the execution loop is inefficient:

```text
full expensive validation
  → late infrastructure/harness failure
  → ad hoc maintainer patch
  → full expensive validation again
  → next harness failure
```

This is not an acceptable steady-state engineering loop.

Until the qualification machinery itself is proven stable, switch to **STABILISATION MODE**.

---

## 18. Stabilisation Mode

### 18.1 Goal

Make the qualifier trustworthy and cheap enough that a full qualification run is an informative event, not the primary debugging instrument.

### 18.2 Freeze expensive full validation during active harness repair

Do **not** repeatedly launch full #134 validation while a newly discovered harness/authority defect is still being repaired.

Before another full run, the exact repair must satisfy the deterministic preconditions below.

### 18.3 One repair, one explicit hypothesis

For every maintainer repair:

1. Name the exact observed failure.
2. Name the root cause hypothesis.
3. Write a regression test that fails for that cause.
4. Prove baseline-green in the **same execution shape/environment** that the production gate uses.
5. Inject the specific bad change and prove the intended detector becomes red for the **expected reason**, not merely because the process returned non-zero.
6. Rehearse the next control transition where practical.
7. Only then create/merge the repair PR.
8. Only after the repair is live may full validation be considered again.

A setup error, import failure, missing file, timeout or unrelated RuntimeError is **not** proof that a mutation was caught.

### 18.4 Do not use full catalogue runs as a debugger

If a mutation defect names its detector:
- run the responsible detector first;
- verify it runs in the copied environment;
- verify clean baseline green;
- verify mutated state red with reason discrimination.

Only run the broad catalogue when the targeted protocol is green or when the defect has no mapping.

### 18.5 Cheap checks first

Order gates by expected cost and ability to invalidate downstream work.

Examples:
- identity/currency/stale-base checks;
- static anchor/copy-set/manifest checks;
- parser/schema compatibility;
- deterministic unit/rehearsal checks;
- only then independent model judges;
- only then expensive broad mutation/e2e work where required.

Never spend model-judge money on a subject a sub-second deterministic check can already reject.

### 18.6 Independent judges should fan out

Independent holdouts/certifiers judging the same immutable subject should run concurrently where independence/blinding can be preserved.

Elapsed time should approach the maximum judge duration, not the sum.

### 18.7 Plan before heavyweight provisioning

The worker should determine the next typed action before provisioning databases, browser stacks and model routes that the action cannot use.

Frequent scheduling becomes practical only when no-op/control/recovery pulses are cheap.

### 18.8 Avoid scheduler dead time

Once no-op pulses are cheap and leases are sound, reduce polling latency or use event-triggered continuation. Do not allow a harmless state transition to sit idle for an hour.

### 18.9 Do not mutate `main` underneath a long validation unnecessarily

During qualification:
- stage maintainer fixes on branches;
- merge only when the currently valuable run has completed or when the new fix makes that run provably obsolete;
- if a run is obsolete, cancel/ignore it rather than wait for an answer that can no longer be used.

### 18.10 Stop using one giant terminal session as project memory

At the beginning of an overseer session:
1. read this file;
2. read the live handover;
3. fetch current GitHub state;
4. compare, then act.

Do not re-derive the architecture.

When context becomes large or the task phase changes, write a concise durable handover and start a fresh session. A 100k+ token “spelunking” session is a warning sign, not a badge of thoroughness.

### 18.11 Spend budgets

For each proposed action classify it:

```text
control-plane / deterministic
cheap model
full judge set
targeted mutation
full mutation
browser/e2e
```

Before launching expensive work, record:
- why it is necessary now;
- what uncertainty it resolves;
- cheaper evidence already exhausted;
- expected cost/wall-time class;
- what next action follows each possible outcome.

If those cannot be stated, do not launch the expensive action.

### 18.12 No passive polling as work

Do not consume an overseer session repeatedly issuing long `until ... sleep` loops.

Use background monitoring or event-driven observation, and perform bounded useful work in parallel only when it cannot move `main` or invalidate the run being observed.

### 18.13 Limit opportunistic scope

While repairing one blocker, do not broaden into unrelated factory redesign because nearby code looks imperfect.

Record discovered issues, but keep the current repair bounded unless the newly discovered issue is a direct prerequisite.

---

## 19. Required pre-full-validation checklist

Before launching another expensive full validation while the factory is in stabilisation:

- [ ] Current `main` identity is known.
- [ ] Candidate PR head/base are current.
- [ ] No known trust-root/harness defect is unresolved.
- [ ] Static/anchor/copy-set integrity passes on the exact runner shape.
- [ ] Clean focused baseline is green in the same copied environment used by mutation verification.
- [ ] Every newly changed mutation detector has a targeted baseline-green → injected-red proof.
- [ ] Injected-red proof discriminates the expected detector failure from unrelated exceptions.
- [ ] Relevant parser/wrapper/call-surface compatibility is rehearsed.
- [ ] Relevant control transition (e.g. re-head eligibility) is rehearsed where possible.
- [ ] No pending maintainer merge is expected to invalidate the run immediately.
- [ ] Full run is the cheapest remaining way to resolve the next uncertainty.

If any box is false, fix that first.

---

## 20. Current speed work already identified

Keep these concepts separate so causal evidence stays readable:

1. **Frequent dispatch** — reduce idle latency once pulses are cheap.
2. **Detector-specific mutation fast path** — mapped defect → responsible detector first.
3. **Parallel independent validation authorities** — same immutable subject, fan out.
4. **Action-specific provisioning** — plan first, provision only what the action needs.

Do not combine all four into one trust-root PR.

---

# PART IV — OPEN IMPLEMENTATION CHOICES

## 21. Intentionally not locked yet

Do not prematurely lock:
- exact database/storage technology;
- graph library;
- SSE vs WebSocket;
- exact process/service decomposition;
- exact Python package layout;
- specific provider models;
- detailed UI framework choices;
- exact lease storage implementation;
- exact event-schema serialisation;
- exact candidate ranking algorithm;
- exact cost threshold defaults.

Investigate and prove these when their implementation phase arrives.

---

# PART V — DESIRED USER EXPERIENCE

## 22. North-star experience

```text
“I want to build/change this.”
        ↓
Dark Factory understands the intent.
        ↓
It maps the problem into features/questions.
        ↓
It explores several credible ways to solve consequential questions.
        ↓
I can add questions or alternative directions.
        ↓
It cheaply determines the most promising strategy.
        ↓
I can see reasoning, uncertainty and cost.
        ↓
The winner visibly enters the Dark Factory.
        ↓
The trusted factory proves it.
        ↓
Successful work becomes part of the living architecture.
        ↓
The system learns what happened.
        ↓
If reality changes later, it knows which old assumptions
and alternatives should be reconsidered.
```

Underneath that experience sits:

> **a small, deterministic, paranoid kernel that refuses to confuse intelligence with authority.**

---

## 23. Instruction to future overseers

At session start, state only:

1. current verified repo/PR/run state;
2. current blocker;
3. cheapest evidence needed to resolve it;
4. action being taken;
5. what expensive work is explicitly **not** being run yet.

Do not narrate hundreds of exploratory shell commands. Do not rebuild architecture from memory. Do not launch expensive qualification merely because it is the next historical step.

The target is not “maximum activity.”

The target is:

> **minimum trusted work required to move the project one irreversible step forward.**
