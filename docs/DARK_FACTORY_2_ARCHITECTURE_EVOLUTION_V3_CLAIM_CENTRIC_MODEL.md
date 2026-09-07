# Dark Factory 2.0 — V3 Claim-Centric Architecture Evolution

## Purpose

This document records the architectural evolution introduced after the runtime walkthrough. It does not replace existing architecture documents; it aligns them around the operational model they collectively imply.

## Core shift

Dark Factory is not fundamentally a task execution system.

The primary object moving through the system is a **claim**.

Tasks are generated as execution projections required to establish, challenge, or maintain claims.

Example:

```
Claim:
  The product supports offline editing.

Required chain:
  intent
    -> questions
    -> assumptions
    -> candidate solutions
    -> decision
    -> implementation
    -> evidence
    -> authority attestation
```

## Runtime flow

```
User intent
    -> Front Door
    -> Decision Graph
    -> Preflight exploration
    -> Programme compilation
    -> Factory execution
    -> Evidence
    -> Attestation
    -> Merge
    -> Learning
```

## Front Door

The Front Door is not a ticket generator.

It converts ambiguous human intent into a bounded graph of:

- intents;
- unknowns;
- questions;
- assumptions;
- candidate interpretations;
- proposed claims.

Classification must remain reversible. Promotion of an exploration into a requirement or hard constraint changes the specification hash and requires explicit authority.

## Decision Graph

The Decision Graph becomes the causal memory spine.

It records:

- why decisions were made;
- what assumptions they depended upon;
- what evidence supported them;
- what changes may invalidate them.

Invalidation follows recorded dependency edges rather than model judgement.

## Preflight

Preflight is a future simulation system.

It should explore competing approaches before committing factory resources.

```
Option A -> simulate -> score
Option B -> simulate -> score
Option C -> simulate -> score

chosen approach -> factory
```

Exploration is cheap. Commitment is expensive.

## Orchestrator boundary

The orchestrator schedules movement through an approved decision space.

It does not decide truth.

Responsibilities:

- scheduling;
- leases;
- retries;
- budgets;
- execution ordering.

Not responsibilities:

- silently changing requirements;
- weakening proof standards;
- overriding authorities.

## Evidence model

Evidence exists to support claims.

The lifecycle is:

```
claim
 -> proof obligation
 -> authority
 -> attestation
 -> dependency identity
```

A proof is only valid for the subject, revision, policy version and dependency set it was generated against.

## Learning boundary

Learning improves:

- strategy selection;
- estimation;
- tool choice;
- question ordering.

Learning cannot silently modify:

- trust roots;
- authority rules;
- security policy;
- merge permissions.

Those require architecture governance.

## Implementation consequence

Future implementation should prioritise:

1. claim and attestation primitives;
2. dependency tracking;
3. capability boundaries;
4. Front Door and Decision Graph runtime;
5. Preflight simulation;
6. autonomous factory execution.

The aim is not a larger agent system. The aim is a system where agents are replaceable workers inside a controlled proof-producing architecture.
