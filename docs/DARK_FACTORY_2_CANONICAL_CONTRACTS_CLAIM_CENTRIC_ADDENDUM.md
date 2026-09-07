# Dark Factory 2.0 — Claim-Centric Contract Migration Addendum

**Purpose:** define the semantic extension from object-centric contracts to claim-centric proof contracts.

This document extends `DARK_FACTORY_2_CANONICAL_CONTRACTS.md` without forcing a flag-day rewrite of existing trusted artifacts.

## Core model

Dark Factory does not ultimately manage tasks. It manages claims.

A task, programme item or worker execution exists to establish, challenge or maintain a claim.

```text
Claim
  ↓
Proof obligation
  ↓
Evidence source
  ↓
Authority
  ↓
Attestation
  ↓
Dependency identity
```

## Claim lifecycle

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

Transitions require recorded evidence or explicit authority action.

## Claim descriptor

Future canonical objects should support:

```json
{
  "claim_id":"claim_...",
  "statement":"...",
  "scope":{},
  "dependencies":[],
  "required_evidence":[],
  "authority_requirements":[],
  "status":"current"
}
```

## Migration rule

Existing contract/context/design/RED/GREEN artifacts remain authoritative. They become evidence providers and proof inputs to higher-level claims rather than being replaced.

## Invalidations

A changed dependency should invalidate only claims whose dependency closure includes that change.

Do not rebuild the entire factory when a smaller proof frontier can be identified.
