# Dark Factory 2.0 — V3 Claim-Centric Architecture Migration Plan

**Status:** proposed documentation migration
**Purpose:** align existing architecture documents with the claim-centric runtime model without creating duplicate architecture sources.

## Principle

The runtime model introduces a refinement of the existing architecture:

> Dark Factory manages claims and proofs, not tasks.

Tasks, programmes and agents remain execution mechanisms. Their purpose is to establish, challenge or maintain claims.

## Migration rule

Do not create parallel replacement documents for existing architecture areas.

Update existing canonical documents where ownership already exists.

Promote only durable changes:

- invariants;
- contracts;
- lifecycle models;
- dependency rules;
- validation semantics.

Do not migrate:

- conversational explanations;
- temporary implementation status;
- terminal transcripts;
- superseded recommendations.

## Phase 1 — Highest-value document alignment

### 1. Target Architecture and Overseer

Add:

- claim-centric system model;
- distinction between claims, tasks and execution workers;
- proof lifecycle as the durable object.

### 2. Canonical Contracts

Add canonical relationships:

```
Claim
  ↓
Evidence obligation
  ↓
Authority
  ↓
Attestation
  ↓
Dependency identity
```

Define claim lifecycle:

```
UNKNOWN
 → PROPOSED
 → SUPPORTED
 → PROVEN
 → CURRENT
 → STALE
 → SUPERSEDED/REJECTED
```

### 3. Attestation Dependency Model

Strengthen:

- dependency-aware invalidation;
- minimal revalidation frontier;
- proof reuse only where identity remains valid.

### 4. Project Decision Graph

Strengthen:

- causal dependency graph;
- assumption invalidation;
- reconsideration frontier;
- avoid full-project reruns after local changes.

### 5. Front Door

Clarify:

Front Door transforms intent into a bounded claim graph.

It does not create implementation tasks directly.

### 6. Programme and Preflight

Clarify:

Preflight explores possible futures.
Programme commits one validated future.

### 7. Orchestrator and Leases

Clarify:

Orchestrator schedules movement through an approved decision space.
It does not determine truth.

## Phase 2 — Return to live qualification

Documentation migration must not delay:

```
PR #142 routing repair
        ↓
PR #134 recovery/re-head
        ↓
clean qualification baseline
        ↓
measured mutation baseline
```

## Phase 3 — First acceleration feature

After measurement:

Preferred first candidate:

```
Structured Detector Registry
        ↓
Detector-specific mutation execution
        ↓
Re-measure
```

Reason:

A mutation should fail because the responsible detector failed, not because an unrelated test happened to fail first.

## Architectural target

The long-term optimisation target is:

```
Which exact claims became stale?
Which dependencies changed?
Which proof obligations must rerun?
```

rather than:

```
Run the whole factory again.
```
