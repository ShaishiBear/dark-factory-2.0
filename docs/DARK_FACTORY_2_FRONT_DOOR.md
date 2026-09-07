# Dark Factory 2.0 — Front Door / Grill-Me Contract

**Status:** owner-approved target design.  
**Purpose:** transform vague human intent into an explicit, testable, user-approved specification without turning the user into the engineer and without silently inventing product intent.

This document supplements `DARK_FACTORY_2_TARGET_ARCHITECTURE_AND_OVERSEER.md`.

---

## 1. Core invariant

The Front Door resolves **product uncertainty**.

It does not resolve technical strategy and it never qualifies software.

Two complementary rules are locked:

> **Do not confuse “the system does not yet know” with “the user must answer.”**

> **Do not confuse “the system can imagine a plausible answer” with “the user intended that answer.”**

---

## 2. Architecture

Use a dedicated intent-audit loop rather than letting the conversational interviewer decide when it has chatted enough.

```text
CURRENT INTENT STATE
        ↓
INTENT AUDITOR
        ↓
unresolved uncertainty
        ↓
RESOLUTION ROUTER
   ┌────┼───────────────┐
   ↓    ↓               ↓
 repo  research      user-owned
 fact  external fact   decision
   ↓    ↓               ↓
   └────┴──────┬────────┘
               ↓
         updated intent
```

The interviewer is only one resolver.

---

## 3. Unknown ownership

Every unresolved point must first be classified by who can and should resolve it.

### Repository fact

Example: “Does the product already have organisations?”

Resolve by reading the repo. Do not ask the user.

### External factual question

Example: “Does provider X currently support Y?”

Research authoritative documentation. Do not ask the user.

### Engineering/design decision

Examples: WebSockets vs SSE; database shape; cache strategy.

Defer to engineering/Preflight. Do not ask the user merely because the system does not know yet.

### User-owned product decision

Examples: simultaneous editing semantics; sharing policy; deletion policy; whether offline capability is required.

Ask when materially unresolved.

### User preference with a reasonable reversible default

Recommend the current/product-consistent default and avoid asking unless the answer materially changes accepted behaviour.

### Optional unrequested feature

Default out of V1 or keep as exploration-only. Do not upsell scope.

### Inherited policy/constraint

Existing security, architecture, legal or repository policy is not a user preference. Apply it or surface a conflict.

### Feasibility uncertainty

Investigate first. Ask the user only if the technical fact forces a genuine product trade-off.

### Contradiction in user intent

Do not silently choose one answer. Surface the conflict and ask one narrow resolution question.

---

## 4. Default is not to ask

A proposed question requires positive justification.

Before asking, determine:

- what materially different outcomes are plausible;
- whether repo inspection/research can resolve it;
- whether different answers alter observable behaviour, acceptance, scope, permissions, privacy, irreversible data semantics or significant cost;
- whether the answer truly belongs to the user.

If the answers do not justify a question, do not ask it.

---

## 5. Material ambiguity test

For consequential ambiguous language, internally generate a few plausible interpretations and compare them.

Example:

```text
“Users can share projects”
A. invite named users
B. public link
C. organisation membership
D. export a copy
```

If interpretations do not create materially different observable product behaviour, clarification is unnecessary.

If they do, resolve via repo/research where possible; otherwise ask the smallest user-owned question that distinguishes them.

Do not hunt academic ambiguity that has no engineering consequence.

---

## 6. One meaningful question at a time

Normal interaction is one decision question per turn.

Do not send a questionnaire.

Question priority is approximately:

1. contradiction blocking coherent intent;
2. fundamental outcome/scope decision;
3. decision affecting large downstream portions of the spec;
4. user-owned permissions/privacy/security policy;
5. irreversible or expensive product decision;
6. core workflow behaviour;
7. material off-nominal/failure behaviour;
8. secondary preference;
9. cosmetic reversible preference.

Within a tier, prefer the question that resolves the most downstream uncertainty for the least user burden.

Do not invent fake numerical question-value scores before calibration exists.

---

## 7. Ask in product language

Questions should distinguish user-visible behaviours, not implementation choices.

Bad:

> “Eventual or strong consistency?”

Good:

> “If two people edit the same item at nearly the same time, must everyone immediately see exactly the same result, or is a short delay acceptable?”

When a sensible default exists, state the consequence, recommend the default, and ask for confirmation.

A recommendation is not approval.

---

## 8. Preserve wording and provenance

For every material user answer preserve:

```text
original wording
normalised interpretation
proposed classification
provenance
```

Do not throw away what the user actually said.

The interview ledger is append-only. If the user changes their mind, append a superseding answer; do not rewrite history.

---

## 9. Delegation and deferral

If the user explicitly says “use whatever you recommend”, record explicit delegation and use approved/default policy.

Silence is not delegation.

If the user says “I don’t know”:

- optional item → defer/default out of V1;
- technical item → defer to Preflight/engineering;
- fundamental product decision → remain blocking until explicitly resolved or delegated.

---

## 10. Provisional intent model

After every meaningful answer maintain a draft model containing, where relevant:

- problem/outcome;
- stakeholders/actors;
- required workflows;
- permissions/policy;
- data semantics;
- constraints;
- preferences;
- non-goals;
- assumptions;
- acceptance criteria;
- open product questions;
- deferred technical uncertainties.

This remains draft state until explicit approval.

---

## 11. Topic coverage is an audit, not a questionnaire

Internally audit relevant areas:

- problem/outcome;
- actors;
- happy-path workflows;
- data ownership/sensitivity/lifecycle;
- permissions/sharing/privacy/deletion;
- external interfaces;
- operating context/devices/offline/geography;
- scale/performance where consequential;
- off-nominal/failure behaviour;
- compatibility/migration;
- rollout/rollback/support expectations;
- success/acceptance;
- non-goals.

Do not ask one question per category.

Use existing product behaviour, repository facts and engineering defaults where appropriate.

---

## 12. Scenario audit before readiness

Before declaring a draft ready, synthesise representative scenarios where applicable:

- normal success;
- boundary condition;
- permission boundary;
- invalid input;
- dependency failure;
- concurrent/conflicting operation;
- recovery/rollback.

Scenario analysis is used to discover **material missing product intent**, not to make the user specify every error case.

If an edge case can be resolved safely by established product/engineering policy, do not ask.

If it exposes a genuine product policy decision, ask.

---

## 13. Separate outcome from proposed solution

If the user says:

> “Use Redis so sessions are fast.”

extract:

```text
desired outcome: responsive sessions
proposed solution: Redis
```

Ask whether Redis itself is mandatory only if that distinction is material.

Technical solutions should normally remain downstream of the approved product requirement.

---

## 14. Requirement quality audit

Before approval, inspect for ambiguity/unverifiability, including undefined terms such as:

`fast`, `easy`, `intuitive`, `secure`, `seamless`, `robust`, `reasonable`, `normally`, `where appropriate`, `best`, `minimal`.

The test is not whether natural language is imperfect. The test is:

> Could two competent implementers produce materially different accepted behaviours from this wording?

Every required product behaviour should have observable acceptance semantics and trace to an outcome.

Do not over-specify implementation in acceptance criteria.

---

## 15. Cheap feasibility screen

Before approval, perform a bounded screen for:

- obvious repository/platform contradiction;
- known impossible requirement;
- known protected-policy conflict;
- known external-service impossibility;
- mutually inconsistent requirements.

This is not full technical qualification.

Plausible technical uncertainty is recorded for Preflight.

Known infeasibility that forces a product trade-off is surfaced to the user before approval.

---

## 16. Readiness contract

The Intent Auditor may declare `READY_FOR_REVIEW` only when:

1. core problem/outcome is clear;
2. material actors are known;
3. required user-visible behaviour is sufficiently defined;
4. hard product constraints are explicit;
5. scope/non-goals are explicit enough;
6. no known contradiction remains;
7. every requirement traces to an outcome;
8. required behaviour has observable acceptance semantics;
9. no blocking user-owned ambiguity remains;
10. unresolved technical questions are explicitly deferred downstream;
11. optional unresolved ideas remain exploration/out-of-scope;
12. material assumptions are explicit;
13. scenario audit is complete;
14. cheap feasibility screen found no known fatal contradiction.

There is no minimum question count.

A complete input may require zero questions.

---

## 17. Approval contract

Present a concise review:

```text
WHAT WE ARE BUILDING
WHO IT IS FOR
REQUIRED BEHAVIOUR
IMPORTANT PRODUCT DECISIONS
HARD CONSTRAINTS
NOT IN V1
ASSUMPTIONS
TECHNICAL QUESTIONS DARK FACTORY WILL RESOLVE
HOW SUCCESS WILL BE TESTED
```

Approval must be explicit.

On approval:

```text
draft
→ deterministic structural validation
→ immutable approved spec version
→ canonical hash
```

Approval means “this represents what the user wants”, not “engineering feasibility is proven”.

Changes after approval create a new spec version/hash and preserve the old one.

User-added ideas after approval still default to exploration until explicitly promoted and approved.

---

## 18. Front Door must not directly create implementation work

Output:

```text
APPROVED SPECIFICATION
```

Then the programme synthesiser proposes executable decomposition.

Front Door may read/research/reason/ask/compile draft intent.

It may not:

- modify product code;
- merge;
- qualify;
- change trust policy;
- silently approve requirements;
- silently promote exploration into hard scope.

---

## 19. Cost discipline

Normal intake research stays bounded.

If resolving a technical uncertainty requires an architecture tournament, benchmark, prototype or significant compute, defer it to Preflight rather than turning the Grill into expensive hidden engineering.

---

## 20. Telemetry for later learning

Record enough history to learn the ask/assume policy later:

- ambiguity detected;
- interpretations considered;
- question asked or skipped;
- resolution source;
- answer/delegation;
- spec nodes changed;
- later spec amendment;
- downstream failure caused by missed intent;
- question later judged unnecessary.

Do not train aggressively from tiny early data.

---

## 21. Required benchmark suite

### Complete input

Expected: zero unnecessary questions → review/approval.

### One hidden product ambiguity

“Add sharing” with private-account repo context.

Expected: inspect repo → ask only sharing-policy question → no implementation questions.

### Technical difficulty, clear product intent

Expected: no user engineering interrogation → uncertainties deferred to Preflight.

### Vague high-level feature

“Add collaboration.”

Expected: iterative one-question-at-a-time clarification, not a giant form.

### Off-nominal hidden requirement

Expected: targeted failure-policy question only if product-owned/material.

### Optional feature temptation

Expected: no scope upsell.

### Solution masquerading as requirement

“Use Redis so sessions are fast.”

Expected: separate outcome from implementation.

### Researchable fact

Expected: research it, do not ask the user.

### Contradiction

Expected: explain conflict and ask one resolution question.

### Delegation

Expected: delegation explicitly recorded; final spec still explicitly approved.

### User changes mind

Expected: append superseding ledger entry; preserve history.

### Probable infeasibility

Expected: research first, then ask only the necessary product trade-off.

### Low-impact ambiguity

Expected: adopt established/default convention and avoid wasting a turn.

---

## 22. Success metrics

Track jointly:

- questions per intake;
- user turns;
- time to approved spec;
- percentage requiring zero questions;
- later spec amendments;
- amendments caused by missed ambiguity;
- unnecessary questions;
- technical questions incorrectly asked of user;
- requirements lacking acceptance;
- contradictions reaching programme stage.

Do not optimise only for fewer questions.

---

## 23. Final algorithm

```text
USER INTENT
↓
record original wording
↓
build provisional intent
↓
inspect repo / cheap factual context
↓
Intent Auditor
↓
generate plausible interpretations
↓
material difference?
  ├─ no → resolve/default
  └─ yes
       ↓
     who owns answer?
       ├─ repo → inspect
       ├─ external fact → research
       ├─ engineering → defer to Preflight
       └─ user-owned
             ↓
        rank against other questions
             ↓
        ask one highest-value question
             ↓
        append answer/provenance
             ↓
        update intent + contradiction check
             ↓
        repeat
↓
scenario audit
↓
requirement-quality audit
↓
cheap feasibility screen
↓
blocking user ambiguity?
  ├─ yes → continue interview
  └─ no
       ↓
     compile human + machine draft
       ↓
     structural validation
       ↓
     explicit user approval
       ↓
     immutable spec version + hash
       ↓
     programme synthesis
```

The Front Door is successful when it achieves **minimum sufficient shared understanding** with minimum unnecessary user burden.