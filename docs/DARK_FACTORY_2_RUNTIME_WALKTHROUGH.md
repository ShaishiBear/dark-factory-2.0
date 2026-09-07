# Dark Factory 2.0 — Runtime Walkthrough

## Purpose

This document is the operational reference model for how Dark Factory turns ambiguous human intent into a verified authorised change.

It defines the flow of information, authority and proof through the system.

The fundamental unit is not the task. It is the **claim**.

Tasks are execution projections created to establish, challenge or maintain claims.

```
Intent
  ↓
Questions / assumptions
  ↓
Decisions
  ↓
Claims
  ↓
Evidence
  ↓
Attestations
  ↓
Authorised effects
```

---

## 1. User intent enters through Front Door

A user request is not immediately converted into implementation work.

The Front Door creates an intent graph:

- goal;
- unknowns;
- questions;
- assumptions;
- candidate interpretations.

Classification is advisory only. A model may propose that something is a requirement, preference or constraint, but promotion to a specification-changing status requires explicit authority.

The Front Door creates clarity, not code.

---

## 2. Project Decision Graph becomes the causal memory

The project graph records why decisions exist.

Core entities:

- intent;
- question;
- assumption;
- candidate;
- recommendation;
- decision;
- implementation;
- observation;
- incident.

A recommendation is not a decision. A user suggestion is not automatically a requirement.

Decisions carry causal dependencies.

Example:

```
Assumption
    ↓
Architecture decision
    ↓
Programme item
    ↓
Implementation
```

When an assumption changes, the system follows dependency edges and recalculates only the affected reconsideration frontier.

---

## 3. Programme synthesis

The programme compiler transforms approved decisions into an executable DAG.

It determines:

- dependencies;
- ordering;
- ownership;
- parallel opportunities;
- blocked work.

The programme is a committed future, not a brainstorming space.

---

## 4. Preflight explores possible futures

Preflight exists before expensive implementation.

It generates candidate approaches and evaluates them:

```
Candidate A
  simulate
  estimate
  challenge

Candidate B
  simulate
  estimate
  challenge

Candidate C
  simulate
  estimate
  challenge
```

Preflight recommendations do not automatically become implementation decisions.

Exploration is cheap and reversible. Commitment is expensive and protected.

---

## 5. Factory handoff

The factory receives:

- approved specification;
- chosen strategy;
- implementation contract;
- required evidence;
- constraints.

It does not receive an ambiguous request such as "build this feature".

---

## 6. Orchestrator role

The orchestrator optimises movement through an approved decision space.

It answers:

- what is ready;
- what is blocked;
- what resources exist;
- what is cheapest next.

It does not decide what is true or weaken proof obligations.

---

## 7. Workers operate through capabilities

Workers receive bounded capabilities, not ambient authority.

Example:

```
read: src/mobile/**
write: src/mobile/offline/**
tools: test runner
denied: merge, credentials, production
```

Capabilities are semantic permissions, not simply access tokens.

---

## 8. Evidence follows claims

The system does not merely run tests. It proves claims.

Example:

Claim:

"Offline edits persist after restart"

Required evidence:

- implementation evidence;
- GREEN verification;
- architecture/security review where required.

Authorities create attestations bound to:

- exact subject;
- evidence identity;
- policy version;
- authority identity.

---

## 9. Merge is an authorised effect

Merge requires:

- exact head identity;
- current trust root;
- required attestations;
- valid capability.

GitHub identity is a mutation mechanism, not a source of truth.

---

## 10. Learning

After execution the factory records trajectories and outcomes.

Learning may improve:

- strategy selection;
- cost prediction;
- ordering;
- routing.

Learning may not silently weaken:

- trust rules;
- proof requirements;
- independence boundaries.

Policy changes require architecture governance.

---

## Architectural summary

Dark Factory is a causal proof system with replaceable workers.

The layers are:

```
Product intelligence
        ↓
Decision graph
        ↓
Programme compiler
        ↓
Orchestrator
        ↓
Kernel
        ↓
Capabilities and brokers
        ↓
Authorities
        ↓
Attestations
        ↓
Authorised effects
```
