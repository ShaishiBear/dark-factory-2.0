# Dark Factory 2.0 — Project Decision Graph Semantics

**Status:** owner-approved target architecture.  
**Purpose:** make project memory causal, non-destructive and mechanically reconsiderable so later evidence invalidates only what actually depended on it.

This document extends the graph contracts in `DARK_FACTORY_2_CANONICAL_CONTRACTS.md` and the Front Door/Programme/Preflight architecture.

---

## 1. Governing rule

> **The graph records why the project believes and chose what it chose, not merely what files or tickets currently exist.**

The graph must support three jobs:

1. durable project memory;
2. precise dependency/invalidation propagation;
3. explanation to the user/overseer of why something is current, stale or being reconsidered.

It is not itself a proof authority for code merge.

---

## 2. Canonical history versus projection

Canonical source:

```text
GraphCommand
→ validated ProjectEvent
→ append-only project event stream
```

Materialised nodes/edges, UI views, search indexes and summaries are rebuildable projections.

If projection disagrees with canonical events, projection loses.

Project mutation order is `project_version`, not timestamp.

---

## 3. Core node classes

Initial canonical semantic nodes:

```text
project
feature
question
requirement
constraint
preference
assumption
candidate
evidence
prediction
recommendation
decision
programme-item
factory-run
implementation-component
observation
incident
lesson-reference
architecture-decision
```

Do not make arbitrary user-created labels into trust-relevant node kinds. User notes may exist as annotations until promoted through an explicit command.

---

## 4. Requirement versus question versus decision

These must remain distinct.

### Requirement

Owner-approved specification obligation.

Changing it requires a new approved spec version.

### Question

Unresolved matter that may be:

- user-owned;
- engineering-owned;
- factual/researchable;
- exploration-only.

A question is not a requirement.

### Recommendation

System/preflight's current preferred candidate given evidence/assumptions.

A recommendation is not automatically a decision.

### Decision

A selection/commitment made by an authorised decision owner.

Decision provenance includes:

```text
owner-user
delegated-system
architecture-governance
deterministic-compiler
```

Technical delegated decisions do not mutate approved product intent unless explicitly promoted into a new spec.

---

## 5. Decision node

Target shape:

```json
{
  "schema":"dark-factory/decision",
  "schema_version":"1.0",
  "decision_id":"dec_...",
  "project_id":"proj_...",
  "question_id":"q_...",
  "selected_candidate_id":"cand_...",
  "recommendation_id":"rec_...",
  "decision_owner":{"type":"delegated-system","actor_id":"preflight"},
  "status":"current",
  "assumption_ids":["asm_..."],
  "constraint_ids":["constraint_..."],
  "evidence_ids":["evid_..."],
  "rationale":"...",
  "revisit_conditions":["asm_..."],
  "created_at":"..."
}
```

Statuses:

```text
current
challenged
reconsideration-required
superseded
withdrawn
```

`current` means still operative, not eternally correct.

---

## 6. Edge direction convention

For dependency edges, direction means:

> **source depends semantically on target**

Examples:

```text
recommendation --ASSUMES--> assumption
programme-item --DEPENDS_ON--> programme-item
implementation-component --IMPLEMENTS--> decision/recommendation (represented by IMPLEMENTED_AS in current registry direction as defined below)
decision --SUPPORTED_BY--> evidence
```

This convention makes invalidation traversal primarily follow **incoming dependency edges to the invalidated target**.

Document any edge whose direction differs for historical registry compatibility.

---

## 7. Edge semantics

### `PART_OF`

Structural containment only. Parent staleness does not automatically invalidate child unless another dependency edge says so.

### `DEPENDS_ON`

Strong causal dependency.

If target becomes invalid/superseded in a way that breaks the dependency, source becomes `reconsideration-required` or blocked according to node type.

### `ADDRESSES`

Maps solution/work to question/requirement. Target movement may make source obsolete, but does not automatically prove source invalid; recompute relevance.

### `CONSTRAINED_BY`

Source is valid only under target constraint/version.

Constraint supersession triggers reevaluation against new constraint.

### `ASSUMES`

Strong causal dependency on assumption.

`assumption.invalidated` deterministically makes source stale/reconsideration-required.

`assumption.challenged` marks source challenged but does not necessarily revoke it.

### `SUPPORTED_BY`

Evidence contributes support.

Loss/challenge of one support edge does **not** automatically invalidate source unless policy says that evidence is required or no sufficient support set remains.

### `CONTRADICTED_BY`

Creates challenge/reconsideration pressure. Severity depends on evidence class/policy.

### `CANDIDATE_FOR`

Exploration membership; no validity propagation by itself.

### `RECOMMENDED_FOR`

Recommendation relationship; no automatic owner approval.

### `REJECTED_FOR`

Historical rejection relation. Rejected candidate remains queryable/reconsiderable if assumptions change.

### `IMPLEMENTED_AS`

Decision/recommendation → implementation component or programme/run → component according to canonical registry direction chosen during implementation. It records realisation, not causal proof.

Implementation incident may propagate back to the implemented decision through explicit mapping, not merely because this edge exists.

### `INVALIDATES`

Explicit hard invalidation emitted by validated deterministic rule/authority/owner action.

### `SUPERSEDES`

New semantic object supersedes old one. Old object remains immutable/historical and becomes non-current.

### `RECONSIDERED_DUE_TO`

Provenance edge explaining why reconsideration occurred. It does not itself create further propagation.

---

## 8. Assumption lifecycle

```text
active
  ↓ contradictory evidence / threshold warning
challenged
  ↓ deterministic/user-confirmed invalidation
invalidated

active/challenged
  ↓ newer replacement assumption
superseded
```

Only `invalidated` triggers deterministic hard staleness through `ASSUMES`.

`challenged` triggers review priority but should not automatically blow away an entire programme.

---

## 9. Evidence lifecycle

Evidence is immutable as an observation/artifact.

Do not mutate “old evidence is false”. Instead attach status/interpretation events:

```text
accepted-current
challenged
superseded-by-newer-measurement
retracted-invalid-method
out-of-scope
```

If method itself is later found invalid, dependent `SUPPORTED_BY` relationships may be downgraded/reconsidered according to policy.

Historical evidence remains visible.

---

## 10. Recommendation lifecycle

```text
generated/current
  ↓ assumption challenged
challenged
  ↓ assumption invalidated / hard constraint changes
reconsideration-required
  ↓ reevaluation selects same candidate
current (new recommendation version/id if material reasoning changed)
  ↓ alternative wins
superseded
```

Do not edit the old recommendation rationale in place.

---

## 11. Candidate retention

Candidates are never deleted merely because they lose.

Record rejection/pruning basis:

```text
hard constraint failed
dominated under policy
probe failed
high uncertainty / insufficient value
strategy rejected by real factory
```

If the assumption/constraint causing rejection changes, candidate can re-enter exploration with new evaluation; old evaluation remains history.

This prevents repeated rediscovery and supports fast reconsideration.

---

## 12. Invalidation propagation algorithm

Input: canonical event such as `assumption-invalidated`, `constraint-superseded`, `evidence-retracted`, `decision-superseded`, `incident-confirmed`.

Algorithm:

```text
1. mark direct target state from event
2. initialise queue with target node
3. inspect active inbound causal edges
4. for each source, apply edge-specific propagation rule
5. if source state materially changes, append canonical event
6. enqueue changed source if its new state can affect dependants
7. stop at non-causal/provenance edges
8. deduplicate by (event cause, node, target state)
9. compute minimal reconsideration frontier
```

Do not mutate many rows silently in one hidden transaction without corresponding events.

---

## 13. Minimal reconsideration frontier

Do not immediately rerun every downstream implementation.

The propagation engine identifies nearest semantic decision points that can absorb the change.

Example:

```text
assumption invalidated
→ recommendation stale
→ decision reconsideration-required
→ programme items depending on decision BLOCKED_PENDING_RECONSIDERATION
```

Stop there until decision is recomputed.

If reconsideration selects same candidate under new evidence, downstream implementation may remain valid if its own dependencies are unchanged.

This avoids cascading unnecessary rebuilds.

---

## 14. Reconsideration is not rollback

`reconsideration-required` means:

> The rationale/decision must be reevaluated before dependent future action continues.

It does not automatically mean:

- revert already deployed code;
- delete implementation;
- fail current users;
- reopen all merged PRs.

Operational containment/rollback requires explicit incident/severity policy.

---

## 15. Implementation feedback loop

After factory merge, create/update `implementation-component` mappings.

Observations/incidents attach to components.

Example:

```text
incident
→ AFFECTS component
→ component IMPLEMENTED_AS decision/recommendation
→ incident evidence contradicts assumption
→ assumption invalidated
→ decision reconsideration frontier
```

Do not infer every incident directly means the original architecture decision was wrong. The causal mapping must be explicit or confidence-labelled.

---

## 16. Observation versus incident

### Observation

Measured fact that may be neutral/positive/negative.

Examples:

- latency p95;
- provider error rate;
- qualification wall time;
- user behaviour.

### Incident

A declared undesirable condition requiring action/containment.

Incident may cite observations and affected components.

An observation crossing a deterministic threshold may create incident automatically if policy defines one.

---

## 17. Spec relationship

Graph may contain ideas/questions/preferences outside approved spec.

Absolute rule:

> **Graph mutation does not silently mutate approved specification.**

When reconsideration reveals an owner-owned requirement should change:

```text
create owner question
→ owner approves amendment
→ SPEC vN+1
→ event links new spec to affected graph nodes
→ programme recompile
```

Technical decisions under delegated authority can change without spec version if product intent/acceptance remains unchanged.

---

## 18. Programme relationship

Programme proposal/compiled DAG references graph/spec objects by immutable ID/version/hash.

When relevant decision/spec node becomes stale:

- future not-started programme items may become blocked/stale;
- active work capabilities/leases are cancelled/fenced where dependency is safety-critical;
- completed work remains historical;
- compiler produces a new programme version if decomposition/dependencies change.

Do not mutate old compiled DAG in place.

---

## 19. Factory run relationship

A factory handoff binds:

```text
spec version/hash
programme item
question
decision/recommendation/candidate
assumptions
hard constraints
```

If a bound semantic input is invalidated before merge:

- handoff becomes stale;
- no new privileged capability should issue for it;
- active run may be cancelled/fenced depending on dependency severity;
- return to reconsideration/preflight rather than blindly finishing obsolete work.

After merge, later invalidation creates new project work/incident flow rather than rewriting the historical run.

---

## 20. Concurrency and commands

Graph writes use optimistic concurrency:

```text
expected_project_version
+ idempotency_key
```

If stale:

- reject command with current version and conflict context;
- requester refreshes projection;
- recompute intended command;
- retry if still semantically valid.

Never last-write-wins trust-relevant decisions.

Independent commands on different semantic areas may eventually be rebased/merged by deterministic command logic, but correctness comes before throughput.

---

## 21. Command authority

Initial command classes:

```text
add-question
record-answer
add-assumption
challenge-assumption
invalidate-assumption
add-candidate
record-evidence
publish-recommendation
make-decision
supersede-decision
add-programme-version
record-factory-run
record-implementation-component
record-observation
open-incident
resolve-incident
request-reconsideration
approve-spec-version
```

Each command has allowed actor classes.

Example:

- `approve-spec-version`: owner/user authority only unless explicit delegation contract exists;
- `record-evidence`: system/authority/import as permitted;
- `make-decision`: depends on decision ownership;
- `invalidate-assumption`: deterministic threshold authority or authorised owner/system process depending on assumption class.

---

## 22. Decision ownership classes

Every consequential question/decision declares owner class:

```text
OWNER_USER
DELEGATED_TECHNICAL
DETERMINISTIC_POLICY
EXTERNAL_FACT
```

Resolution routes:

- `OWNER_USER` → ask only if material and unresolved;
- `DELEGATED_TECHNICAL` → Preflight/architect may decide;
- `DETERMINISTIC_POLICY` → compiler/authority decides;
- `EXTERNAL_FACT` → research/measurement/probe.

This operationalises the Front Door principle: never confuse “system does not know” with “user must answer”.

---

## 23. UI semantics

UI should make semantic state visible:

- current decisions;
- challenged assumptions;
- reconsideration frontier;
- competing candidates;
- why candidate lost;
- implementation mapped to decisions;
- current programme dependencies;
- incidents affecting assumptions/decisions;
- historical superseded paths.

UI controls issue graph commands; it does not directly mutate projections/database rows.

No visual state such as a green badge may claim `proved` unless derived from canonical authority evidence for that concept.

---

## 24. Explanation queries

The graph should answer mechanically:

```text
Why are we building this?
Which requirement does this item serve?
Why did we choose candidate A over B?
Which assumptions does this decision rely on?
What becomes stale if assumption X fails?
Which implementation realises decision Y?
Why was this decision reopened?
Which previous alternative should we reconsider now?
```

These are core product capabilities, not optional analytics.

---

## 25. Graph storage abstraction

Use `ProjectStore` interface so semantic model is not coupled to one database.

Need capabilities roughly:

```text
append_events_atomically(expected_version, events)
load_events(project, from_version)
load_projection(project)
query_inbound_edges(node, active_only)
query_outbound_edges(node, active_only)
lookup_by_idempotency_key(key)
```

A relational database is likely sufficient initially. Do not choose graph database merely because the product is called a graph.

Storage choice remains Tier 1 implementation architecture and should be benchmarked against real query shapes.

---

## 26. Projection rebuilding

Must be possible to rebuild materialised state from canonical events and schema migrations.

Test periodically:

```text
empty projection
→ replay all project events
→ projection digest equals current projection digest
```

Projection corruption must not destroy canonical decision history.

---

## 27. Event schema evolution

Never reinterpret historical event payload under new semantics silently.

Use:

- schema version per event;
- deterministic upcaster/projection adapters;
- immutable raw event bytes;
- migration tests over historical corpus.

A new edge/status meaning requires explicit versioning where old events would otherwise change interpretation.

---

## 28. Adversarial acceptance tests

Must prove at least:

- adding a question does not mutate approved spec;
- user idea remains exploration until approved;
- assumption invalidation stales only active `ASSUMES` dependants;
- `challenged` assumption does not hard-invalidate dependants;
- inactive/superseded edge does not propagate;
- `RECONSIDERED_DUE_TO` does not create recursive invalidation;
- losing candidate remains queryable and can be reconsidered;
- stale graph command cannot overwrite newer decision;
- same idempotency key returns original result;
- programme item depending on stale decision cannot start new privileged work;
- completed historical factory run remains immutable after later reconsideration;
- projection rebuild yields same canonical state;
- incident cannot silently rewrite original decision rationale;
- owner-owned requirement change requires new approved spec version.

---

## 29. Migration sequence

1. Implement append-only ProjectEvent + command CAS for a minimal project.
2. Add question/assumption/candidate/recommendation/decision nodes.
3. Add explicit causal edge registry.
4. Implement deterministic assumption invalidation propagation.
5. Implement reconsideration frontier and retained alternatives.
6. Bind factory handoff to graph refs.
7. Record merged implementation components.
8. Add observations/incidents and feedback propagation.
9. Add projection rebuild/integrity tests.
10. Add concurrent user/agent command handling only after version-CAS semantics are stable.
11. Build UI as a projection over this model.

---

## 30. Locked conclusions

1. Project events are canonical; graph/UI projections are rebuildable.
2. Requirement, question, recommendation and decision are distinct objects.
3. Dependency edge direction means source depends on target for causal edges.
4. Invalidation propagates by edge-specific rules, not generic graph flooding.
5. Assumption `invalidated` is stronger than `challenged`.
6. Reconsideration stops at the minimal semantic frontier before rebuilding downstream work.
7. Losing candidates and superseded decisions remain historical/reusable.
8. Graph changes never silently mutate approved spec.
9. Factory runs/merges remain historical facts even when later evidence reopens a decision.
10. The graph's value is causal explanation and precise reconsideration, not merely visualisation.
