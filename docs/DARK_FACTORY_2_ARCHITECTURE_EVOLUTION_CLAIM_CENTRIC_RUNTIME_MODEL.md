# Dark Factory 2.0 — Claim-Centric Runtime Model

## Status

Proposed architecture consolidation document.

This document captures the architectural interpretation that emerges from the runtime model: Dark Factory should be understood primarily as a system for maintaining, challenging and proving claims rather than as a task execution pipeline.

It is a migration guide for aligning existing component documents. It does not replace current protected repository behaviour or trust policy.

---

# 1. Core architectural shift

The fundamental unit of Dark Factory is not a task.

It is a **claim**.

Tasks, workers and programmes are execution mechanisms created to establish, challenge or preserve claims.

Example:

```
Claim:
"The product supports offline editing."

Creates:
- specification claims
- decisions
- implementation obligations
- evidence requirements
- authority challenges
- attestations
```

The factory's purpose is therefore not simply:

```
request -> plan -> execute -> deliver
```

but:

```
intent
  -> claims
  -> decisions
  -> implementation
  -> evidence
  -> attestation
  -> current truth
```

---

# 2. Claim lifecycle

Claims move through explicit states:

```
UNKNOWN
  |
PROPOSED
  |
SUPPORTED
  |
PROVEN
  |
CURRENT
  |
STALE
  |
REJECTED / SUPERSEDED
```

A claim is current only while its dependency identity remains valid.

A changed dependency does not imply that everything must rerun. It creates a reconsideration frontier.

---

# 3. Front Door role

Front Door is not primarily a ticket generator.

Its responsibility is to convert ambiguous intent into a bounded claim graph.

Flow:

```
User intent
    |
questions
    |
assumptions
    |
candidate interpretations
    |
approved claims
```

Front Door should make uncertainty explicit before implementation begins.

---

# 4. Decision Graph role

The Project Decision Graph is the causal memory spine.

It records why the system believes something, not merely what work remains.

Important distinction:

```
Task graph:
what happens next

Decision graph:
why this future is justified
```

When an assumption changes:

```
changed input
    |
causal dependency traversal
    |
minimal reconsideration frontier
    |
affected claims reevaluated
```

---

# 5. Programme and Preflight

Programme compilation creates a committed future.

Preflight explores possible futures before commitment.

Therefore:

```
Preflight = exploration
Programme = commitment
```

Preflight should compare alternatives, expose uncertainty and estimate value of further investigation before expensive execution.

---

# 6. Orchestrator boundary

The orchestrator is a scheduler, not an authority.

It answers:

```
What is ready?
What is blocked?
What resources exist?
What is the cheapest safe next action?
```

It does not decide:

```
What is true?
Which proof standard may be weakened?
Which authority can be skipped?
```

Those remain owned by the relevant trust boundaries.

---

# 7. Evidence and attestation

Evidence exists to support claims.

An attestation binds:

```
claim
 |
evidence
 |
authority
 |
dependency identity
 |
result
```

A future optimisation goal is dependency-aware invalidation:

```
input changed
    |
identify dependent claims
    |
invalidate only affected proofs
    |
reuse unaffected evidence
```

This is the foundation for moving beyond full-factory reruns.

---

# 8. Learning boundary

Learning may improve:

- strategy selection;
- cost prediction;
- tool selection;
- ordering of investigation.

Learning may not silently change:

- authority requirements;
- security boundaries;
- proof obligations;
- merge rules;
- trust policy.

Changes to protected policy require explicit architecture governance.

---

# 9. Capability interpretation

Credentials are not capabilities.

A credential is a means of authentication.

A capability is a bounded permission to perform a semantic action.

Preferred model:

```
worker
  requests capability
       |
subject/resource/action/lease constraints
       |
controlled effect
       |
execution receipt
```

---

# 10. Migration principle

Do not rewrite the factory as a single large redesign.

The recommended progression is:

```
qualification baseline
        |
claim/dependency formalisation
        |
structured detector and evidence identity
        |
proof reuse
        |
authority fan-out
        |
trust-root optimisation
```

The architecture should earn acceleration from measured dependency information rather than assumptions.

---

# 11. Architectural objective

The long-term objective is:

> Determine which exact claims became stale because which exact inputs changed, and recompute only what is necessary while preserving independent proof.

The factory becomes a causal proof system with replaceable workers, rather than merely a collection of autonomous agents.
