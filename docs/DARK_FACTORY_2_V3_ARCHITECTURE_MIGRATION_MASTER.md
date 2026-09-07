# Dark Factory 2.0 — V3 Claim-Centric Architecture Migration Master Plan

## Purpose

This document is the execution plan for migrating the existing Dark Factory 2.0 architecture documents from a component-centric description into the approved claim-centric runtime model.

The migration does not replace proven trust mechanisms. It reorganises their meaning around the core invariant:

> Dark Factory manages claims, not tasks.

Tasks, programmes, agents and workers exist only as mechanisms for creating, challenging, validating or maintaining claims.

---

# Migration invariant

The following chain becomes the architectural backbone:

```
Intent
  ↓
Question / Unknown
  ↓
Claim
  ↓
Proof obligation
  ↓
Evidence
  ↓
Authority evaluation
  ↓
Attestation
  ↓
Current trusted state
```

A claim is current only while its dependency identity remains valid.

---

# Document migration sequence

## 1. Target Architecture and Overseer Directive

Required changes:

Replace the opening mental model:

Old:

```
System composed of Front Door, Programme, Orchestrator, Kernel and Authorities
```

New:

```
System that manages a causal network of claims through bounded authorities
```

Add the primary split:

```
Intelligence proposes claims and strategies.
The proof system authorises transitions in claim state.
```

Preserve:

- trusted kernel boundaries;
- independent authorities;
- fail-closed behaviour;
- model workers never becoming authorities.

---

## 2. Canonical Contracts

Promote Claim to a first-class contract.

Required canonical objects:

```
Claim
ClaimDependency
ProofObligation
EvidenceBinding
AuthorityEvaluation
Attestation
```

Claim lifecycle:

```
UNKNOWN
 ↓
PROPOSED
 ↓
SUPPORTED
 ↓
PROVEN
 ↓
CURRENT
 ↓
STALE
 ↓
SUPERSEDED / REJECTED
```

A task is not a canonical object. A task exists because a claim requires work.

---

## 3. Attestation Dependency Model

Move from:

```
Artifact changed → rerun checks
```

towards:

```
Dependency changed
      ↓
Affected claims identified
      ↓
Affected attestations invalidated
      ↓
Minimal revalidation frontier calculated
```

Every attestation must answer:

- what claim does this prove?
- who proved it?
- against what revision?
- using what dependency identity?
- under what independence assumptions?

---

## 4. Project Decision Graph

The graph becomes the causal memory layer.

Not:

```
Task tracker
```

But:

```
Claim dependency graph
```

Required relationships:

```
Question
  → generates Claim

Assumption
  → supports Claim

Evidence
  → supports Claim

Claim
  → requires Authority

Attestation
  → proves Claim

Dependency change
  → invalidates Claim
```

The reconsideration frontier becomes the primary optimisation mechanism.

---

## 5. Front Door

Rewrite responsibility:

Old:

```
Convert request into requirements
```

New:

```
Convert intent into a bounded claim graph
```

Front Door creates:

- questions;
- assumptions;
- candidate interpretations;
- proposed claims.

Front Door does not create trusted requirements without approval.

---

## 6. Programme and Preflight

Clarify separation:

```
Preflight explores possible futures.
Programme commits to one future.
Factory proves the committed claims.
```

Preflight output:

- predictions;
- probes;
- candidate comparisons;
- risk estimates.

Never:

- trusted evidence;
- merge authority;
- qualification.

---

## 7. Orchestrator and Leases

Clarify:

The orchestrator moves work.
It does not decide truth.

Its questions:

- what claims need proving?
- what proof work is ready?
- what resources are available?
- what can safely run concurrently?

It cannot:

- downgrade proof requirements;
- override authorities;
- mark claims proven.

---

# Acceleration consequence

The first optimisation after baseline qualification remains:

```
Structured Detector Registry
        ↓
Detector-specific mutation execution
        ↓
Measured improvement
        ↓
Trust-root attestation
```

Reason:

A proof system must know why a mutation should fail before it can safely reuse proof.

---

# Migration completion criteria

The V3 migration is complete when:

- every major architecture document references claims;
- contracts define claim/evidence/attestation relationships;
- dependency invalidation is explicit;
- decision graph represents causal impact;
- Front Door produces claim graphs;
- Preflight and Factory proof boundaries are explicit;
- acceleration work is justified by measured qualification data.

---

# Execution rule

Do not perform a flag-day rewrite of implementation.

Migrate semantics first.
Then introduce contracts.
Then migrate runtime components.
Then optimise.
