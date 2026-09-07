# Dark Factory 2.0 — Architecture Document Index

**Canonical architecture branch while current qualification is in flight:** `architecture/dark-factory-2-target`

This index exists so future overseers do not use chat transcripts, terminal history or private model memory as competing architecture sources.

The target documents below are owner-approved architectural direction. They do **not** waive current repository-protected gates or silently overrule already-implemented trust policy. Where implementation reality exposes a genuine conflict, surface the exact decision/evidence rather than silently drifting.

---

## Required read order

### 1. `docs/DARK_FACTORY_2_TARGET_ARCHITECTURE_AND_OVERSEER.md`

Read first.

Contains:

- overall Dark Factory 2.0 north star;
- product intelligence / orchestrator / kernel / authority split;
- specification versioning and project-decision-graph semantics;
- Front Door / programme / Preflight / learning / UI direction;
- concurrency ordering principles;
- overseer anti-waste and stabilisation discipline;
- locked architectural decisions that should not be re-derived casually.

### 2. `docs/DARK_FACTORY_2_KERNEL_TCB_MIGRATION.md`

Read before changing the current runtime/trust architecture.

Contains:

- repo-mapped TCB analysis;
- why the existing evidence spine should remain the proof lifecycle source rather than adding a duplicate state machine;
- classification/direction for `canonical.py`, `manifest.py`, `spine.py`, `independence.py`, `evidence_closure.py`, `provenance.py`, `authority.py`, `merge_verify.py`, `runtime.py`, providers, Git/GitHub/worktree code;
- capability-broker and privileged-mutation-broker target boundaries;
- re-head authority extraction;
- TCB manifest/LOC ratchet;
- coordinator/executor concurrency prerequisite;
- adversarial tests required to prove each extraction genuinely reduces trust.

### 3. `docs/DARK_FACTORY_2_AUTONOMOUS_GITHUB_IDENTITY.md`

Read before changing autonomous GitHub credentials, PR creation/update, workflow triggering or merge identity.

Contains:

- the owner-approved decision to use a dedicated repository-scoped GitHub App rather than `GITHUB_TOKEN` or a long-lived PAT for autonomous PR mutations;
- why current `GITHUB_TOKEN` PR creation cannot produce a fully unattended required-check path;
- minimum App permissions;
- token/capability scoping rules;
- why custom workflow-dispatch/status publication is not the preferred trust shape;
- the cheap production event-shape proof required before another expensive autonomous qualification;
- regression requirements that keep the App token out of model workers.

### 4. `docs/DARK_FACTORY_2_QUALIFICATION_ACCELERATION.md`

Read before changing qualification/replay/mutation scheduling.

Contains:

- measured qualification bottlenecks;
- proof reuse by dependency identity;
- EXACT_TREE / TRUST_ROOT / LIVE_WORLD replay semantics;
- `FactoryTrustRootAttestation` direction;
- detector-specific mutation qualification;
- validator fan-out;
- post-merge exact-tree proof transfer;
- performance targets and adversarial tests.

### 5. `docs/DARK_FACTORY_2_CANONICAL_CONTRACTS.md`

Read before implementing Front Door, programmes, graph state, Preflight, learning, capability grants or lease evolution.

Contains canonical target shapes/invariants for:

- approved specification;
- interview ledger;
- graph command/event/node/edge;
- question and assumption;
- candidate/evidence/prediction/probe/evaluation/recommendation;
- programme + compiled programme DAG;
- factory handoff;
- factory run and transition request/decision;
- authority attestation;
- capability grant;
- lease v2;
- trajectories/prediction outcomes/strategy outcomes/lessons;
- implementation components/observations/incidents/reconsideration;
- snapshot/live event;
- schema governance and cross-schema invariants.

These contracts are evolutionary targets. Do not flag-day rewrite current proven artifacts merely for uniformity.

### 6. `docs/DARK_FACTORY_2_FRONT_DOOR.md`

Read before implementing the user-intent / Grill-Me system.

Contains:

- dedicated Intent Auditor and resolution router;
- ask-vs-assume rules;
- ownership classes for unknowns;
- material ambiguity test;
- one-question-at-a-time priority algorithm;
- scenario/off-nominal audit;
- requirement-quality and cheap-feasibility checks;
- explicit approval/versioning semantics;
- Front Door benchmark suite and success metrics.

### 7. `docs/DARK_FACTORY_2_PROGRAMME_AND_PREFLIGHT.md`

Read before implementing programme decomposition, programme replanning or multi-option technical exploration.

Contains:

- approved-spec → proposed programme → deterministic DAG compiler contract;
- coverage and ready-frontier rules;
- Preflight trigger/mode logic;
- candidate generation/deduplication;
- hard-constraint screening;
- pre-registered decision policy;
- F0/F1/F2/F3/F4 fidelity ladder;
- Pareto/dominance and uncertainty registry;
- value-of-information / spend rules;
- long-horizon architecture judgement;
- convergence/user-escalation rules;
- UNPROVEN factory handoff;
- strategy-level failure return path;
- reconsideration and calibration benchmarks.

### 8. Current repository-protected decisions / ADRs / trust policy

These remain authoritative for the **currently implemented factory**.

Target documents define where the architecture is going. They do not grant permission to bypass present evidence gates on the way there.

### 9. `HANDOVER.md` / current programme state

Use for live execution state only.

It is not the long-term architecture source of truth.

---

## Source-of-truth hierarchy

For architecture and implementation decisions use:

1. explicit owner-approved product intent / later owner amendments;
2. these Dark Factory 2.0 architecture documents;
3. current protected repository ADRs/trust policy for implemented behaviour;
4. compiled current programme / bounded work item;
5. current verified repo/run evidence;
6. ephemeral agent plans.

If an implementation finding genuinely contradicts a locked target boundary, present:

```text
decision affected
new evidence
why current decision fails
alternatives
recommended amendment
consequences
```

Do not silently drift.

---

## What is NOT an architecture source of truth

Do not treat any of the following as canonical when the docs above cover the same subject:

- ChatGPT transcripts;
- Claude terminal transcripts;
- private Claude memory files;
- old handover snapshots;
- abandoned implementation branches;
- stale run-specific observations superseded by newer evidence.

These can be historical evidence or context, but they are not where architecture should be rediscovered.

---

## Promotion rule

Future substantial owner-approved design work should be promoted into a bounded repo document or existing ADR rather than left only in conversation history.

Do **not** dump raw conversation text into Git.

Promote:

- durable decisions;
- explicit invariants;
- canonical schemas;
- algorithms;
- dependency ordering;
- benchmarks/adversarial cases;
- measured baselines that remain useful;
- unresolved decisions intentionally deferred.

Leave out:

- repeated explanations;
- superseded recommendations;
- conversational framing;
- temporary polling/status chatter;
- stale run IDs unless they support a durable measured baseline.

---

## Reading discipline for Claude / future overseers

At session start:

1. read this index;
2. read only the architecture documents relevant to the current phase plus the north star;
3. read current protected ADRs/policy and `HANDOVER.md`;
4. verify live repo/GitHub state;
5. execute bounded work.

Do not load the entire terminal transcript as project memory.

Do not spend tens of thousands of tokens rediscovering decisions already recorded here.

If a task can be implemented by referencing an existing contract/invariant, implement and test it rather than reopening the architecture discussion.

---

## Current architecture set

The canonical target set is now intentionally small:

```text
DARK_FACTORY_2_DOC_INDEX.md
DARK_FACTORY_2_TARGET_ARCHITECTURE_AND_OVERSEER.md
DARK_FACTORY_2_KERNEL_TCB_MIGRATION.md
DARK_FACTORY_2_AUTONOMOUS_GITHUB_IDENTITY.md
DARK_FACTORY_2_QUALIFICATION_ACCELERATION.md
DARK_FACTORY_2_CANONICAL_CONTRACTS.md
DARK_FACTORY_2_FRONT_DOOR.md
DARK_FACTORY_2_PROGRAMME_AND_PREFLIGHT.md
```

Add a new top-level architecture document only when a concept becomes large enough that putting it into an existing document would make navigation worse or semantic ownership unclear.

The aim is a navigable engineering source of truth — not a second giant transcript inside Git.
