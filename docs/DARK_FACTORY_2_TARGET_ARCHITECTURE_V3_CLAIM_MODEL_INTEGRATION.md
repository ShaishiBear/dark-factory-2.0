# Dark Factory 2.0 — V3 Claim Model Integration Chapter

**Purpose:** integrate the claim-centric architecture into the target architecture without deleting existing trusted design detail.

This chapter is the migration layer between the original component architecture and the V3 causal proof architecture.

---

## 1. Architectural centre of gravity

The fundamental object managed by Dark Factory is a **claim**.

A task, worker, programme item or agent execution is not the thing being protected. It is a mechanism used to establish, challenge or maintain a claim.

The lifecycle is:

```text
intent
  ↓
question
  ↓
assumption
  ↓
claim
  ↓
proof obligation
  ↓
evidence
  ↓
authority evaluation
  ↓
attestation
  ↓
current trusted state
```

This does not remove existing factory stages. It explains why those stages exist.

---

## 2. Component reinterpretation

### Front Door

Previous interpretation:

> convert user requests into requirements.

V3 interpretation:

> convert ambiguous intent into a bounded claim graph.

Front Door produces:

- questions;
- assumptions;
- candidate interpretations;
- proposed claims;
- specification candidates.

It does not produce trusted truth.

---

### Programme layer

A programme is a commitment to establish a selected group of claims.

It is not merely a task decomposition.

```text
claim set
   ↓
proof obligations
   ↓
programme DAG
   ↓
execution
```

---

### Orchestrator

The orchestrator moves work required to establish claims.

It controls:

- scheduling;
- leases;
- retries;
- budgets;
- provider routing.

It does not determine whether a claim is true.

---

### Kernel

The kernel remains the narrow trusted transition layer.

Its responsibility is not intelligence. It is controlled state transition.

Examples:

- accepting evidence bindings;
- validating authority requirements;
- checking dependency identity;
- authorising merge transitions.

---

## 3. Dependency-aware trust

A claim is current only while its evidence dependencies remain valid.

Therefore:

```text
changed dependency
        ↓
dependency traversal
        ↓
affected claims
        ↓
minimal revalidation frontier
```

The factory should not default to:

```text
change detected
 ↓
rerun everything
```

unless the dependency graph cannot prove a smaller safe frontier.

---

## 4. Mutation semantics

A mutation is not merely caught because a test failed.

Future qualification should record:

```text
mutation
 ↓
violated property
 ↓
responsible detector
 ↓
detector execution
 ↓
observed result
 ↓
mutation attestation
```

This provides the foundation for Detector Registry and later trust-root attestation.

---

## 5. Migration rule

Existing architecture documents remain valid where they describe implementation boundaries.

The V3 model changes interpretation:

- components become mechanisms;
- claims become protected objects;
- evidence becomes the currency of trust;
- attestations become the durable memory of justified belief.

The migration should preserve existing proven invariants while moving the architecture toward dependency-aware proof reuse.
