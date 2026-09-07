# Dark Factory 2.0 V3 Canonical Rewrite Patch

Status: authorised semantic migration plan

This document defines the exact semantic edits to apply to the existing canonical architecture documents. It is intentionally a migration patch rather than a second competing architecture.

## 1. Target Architecture and Overseer

Replace the opening architectural framing:

Dark Factory is not primarily a collection of agents, workflows or automation components.

The primary object managed by the system is a claim.

A claim is a statement about the intended system, its behaviour, its architecture, its implementation, or its qualification state that requires appropriate evidence before it may become trusted state.

Workers, programmes, tasks and agents exist only as mechanisms for establishing, challenging, or maintaining claims.

New invariant:

> Intelligence proposes claims and evidence strategies. Trusted authorities establish whether claims become accepted state.

## 2. Canonical Contracts

Promote these as first-class contracts:

```
Claim
  -> Proof Obligation
  -> Evidence
  -> Authority Evaluation
  -> Attestation
  -> Dependency Identity
```

Required claim fields:

- claim_id
- statement
- lifecycle_state
- provenance
- dependency_set
- required_authorities
- evidence_refs
- attestation_refs

Lifecycle:

```
UNKNOWN
PROPOSED
SUPPORTED
PROVEN
CURRENT
STALE
SUPERSEDED
REJECTED
```

A stale claim is not automatically false. It is a claim whose proof dependencies are no longer sufficient.

## 3. Attestation Dependency Model

The dependency model becomes the mechanism for avoiding unnecessary rebuilds.

Instead of:

```
change detected -> rerun factory
```

use:

```
changed dependency
        |
dependency traversal
        |
affected claims
        |
minimal revalidation frontier
```

Every attestation must declare:

- what claim it supports;
- which authority produced it;
- which inputs it depends upon;
- when it becomes stale;
- whether it may be carried across re-head operations.

## 4. Project Decision Graph

The graph is a causal belief graph.

It stores:

- claims;
- assumptions;
- evidence;
- decisions;
- alternatives;
- implementations;
- invalidation paths.

The graph should answer:

"Why does the factory currently believe this?"

and:

"What exact evidence became invalid when something changed?"

## 5. Front Door

Front Door changes from requirement extraction to claim discovery.

Input:

human intent

Output:

```
intent
 -> questions
 -> assumptions
 -> candidate interpretations
 -> claims
 -> approved specification
```

Front Door remains untrusted. It creates proposed meaning; it does not create trusted truth.

## 6. Programme and Preflight

The separation becomes:

```
Preflight explores possible futures.
Programme commits a future.
Factory proves the committed future.
```

Preflight outputs predictions, probes and recommendations.

Only the trusted factory outputs proof.

## 7. Orchestrator

The orchestrator manages movement, not truth.

It controls:

- scheduling;
- leases;
- retries;
- budgets;
- provider routing.

It does not decide whether claims are true.

## 8. Qualification Acceleration

Acceleration follows evidence:

```
healthy qualification baseline
        |
structured Detector Registry
        |
detector-specific mutation execution
        |
remeasure
        |
trust-root attestation
```

A mutation should fail because the responsible detector failed, not because an unrelated test happened to fail first.
