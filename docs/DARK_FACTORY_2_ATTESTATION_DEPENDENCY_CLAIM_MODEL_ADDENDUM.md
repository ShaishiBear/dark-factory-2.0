# Dark Factory 2.0 — Attestation Dependency Claim Model Addendum

**Purpose:** extend attestation architecture from artifact validation toward dependency-aware claim validation.

## Principle

An attestation is not merely evidence that a run succeeded.

It is a statement that a claim was proven under a specific dependency identity.

```text
Claim
  ↓
Evidence
  ↓
Authority judgement
  ↓
Attestation
  ↓
Dependency set
```

## Staleness model

When an input changes:

```text
Changed dependency
        ↓
Dependency graph traversal
        ↓
Affected claims only
        ↓
Revalidation frontier
```

The goal is not to avoid proof. The goal is to avoid repeating proof whose identity remains valid.

## Future attestation envelope

```json
{
  "attestation_id":"att_...",
  "claim_id":"claim_...",
  "authority":"...",
  "evidence_refs":[],
  "dependency_identity":{},
  "status":"current"
}
```

## Detector relationship

Mutation acceleration should eventually bind:

```text
Mutation
  ↓
Expected property violation
  ↓
Declared detector
  ↓
Observed detection
  ↓
Mutation attestation
```

This makes qualification explainable: the factory knows which detector proved which property.

## Migration constraint

Do not create trust-root attestation until a genuinely measured healthy qualification baseline exists on the repaired trust root.
