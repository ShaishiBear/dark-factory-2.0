# Dark Factory 2.0 — Claim-Centric Architecture V3

**Status:** owner-authorised semantic rewrite direction.

This document promotes the approved semantic foundation from migration addenda into a canonical architectural model. Existing implementation contracts remain authoritative until migrated through bounded changes.

## Core model

Dark Factory does not fundamentally manage tasks.

It manages claims about a desired system and the evidence required to establish, challenge, preserve, or invalidate those claims.

```text
Intent
  ↓
Claim graph
  ↓
Evidence obligations
  ↓
Authorities
  ↓
Attestations
  ↓
Current trusted state
```

Tasks, programmes, workers and agents are execution mechanisms used to establish or challenge claims.

## Claim lifecycle

Claims move through:

```text
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

A claim is current only while its dependency identity remains valid.

## Dependency-aware invalidation

The factory should not ask:

> Has anything changed, therefore should everything rerun?

It should ask:

> Which claims depended on the changed input, and what is the minimum proof frontier that must be re-established?

```text
Changed dependency
        ↓
Dependency traversal
        ↓
Affected claims
        ↓
Revalidation frontier
        ↓
Selective recomputation
```

## Runtime roles

### Front Door

Converts human intent into a bounded claim graph:

```text
intent
 → questions
 → assumptions
 → candidate claims
 → approved specification
```

### Preflight

Explores possible futures without producing trusted proof.

### Programme

Commits an executable future from approved claims.

### Orchestrator

Schedules movement through approved work. It does not decide truth.

### Kernel

Maintains deterministic transitions, identity, capabilities and proof rules.

### Authorities

Independently establish or challenge claims.

## Evidence model

Evidence is not a generic success signal.

Each claim requires:

```text
claim
 ↓
proof obligation
 ↓
authority
 ↓
evidence
 ↓
attestation
 ↓
dependency identity
```

## Mutation qualification direction

Future mutation acceleration should be detector-aware:

```text
Mutation
 ↓
Expected violated property
 ↓
Declared detector
 ↓
Detector execution
 ↓
Mutation attestation
```

A mutation is stronger evidence when the responsible detector is explicitly known.

## Migration rule

This architecture supersedes conceptual task-centric interpretations, but implementation migration remains incremental.

Do not replace proven trust artifacts without evidence that the new representation improves correctness, performance or clarity.
