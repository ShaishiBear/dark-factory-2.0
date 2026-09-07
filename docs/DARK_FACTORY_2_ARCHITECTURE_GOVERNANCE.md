# Dark Factory 2.0 — Architecture Governance and Self-Change Protocol

**Status:** owner-approved target architecture.  
**Purpose:** define how Dark Factory is allowed to change Dark Factory without silently eroding its own trust model or repeatedly escalating ordinary engineering decisions to the owner.

---

## 1. Governing problem

A self-improving factory has an unusual failure mode:

> The system can discover that its own architecture is inconvenient and then “solve” the inconvenience by weakening the rule that made it safe.

The architecture therefore needs an explicit amendment process.

The aim is **not** to freeze design. The aim is to make change deliberate, evidence-led and scoped.

---

## 2. Constitutional invariants

The following are treated as constitutional until explicitly amended by the owner:

1. Intelligence may grow without proportionally growing trust.
2. The subject of judgement does not supply the program that judges it.
3. Independent authorities remain structurally independent where policy requires independence/blindness.
4. Missing/stale/indeterminate required evidence cannot authorise a privileged transition.
5. Merge remains exact-subject/exact-head bound and externally enforced where possible.
6. Capability enforcement is real, not prompt-based.
7. Workers/models do not receive ambient repository/credential authority merely for convenience.
8. The evidence spine/manifest remain the proof-lifecycle source; do not create a duplicate drifting state machine.
9. Proof acceleration may remove redundant recomputation but not proof obligations without explicit architectural amendment.
10. Project/user intent cannot be silently invented by technical agents.
11. Approved specifications are versioned/immutable rather than silently rewritten.
12. Canonical history is non-destructive; supersession/invalidation is explicit.
13. Blind judges do not consume learned lessons that compromise independence.
14. TCB size/privilege is a first-class metric and material expansion requires justification.
15. A platform failure or implementation inconvenience is not, by itself, authority to weaken these rules.

---

## 3. Decision tiers

Not every architecture decision deserves owner involvement.

### Tier 0 — implementation choice

Examples:

- module/package layout;
- local helper API;
- SQL query shape;
- specific retry library;
- copy versus overlay implementation after benchmark.

May be decided autonomously if it conforms to higher-level contracts and passes normal evidence.

### Tier 1 — bounded architecture choice

Examples:

- exact lease store implementation;
- provider semaphore algorithm;
- TCB extraction sequence adjustment;
- schema storage backend;
- event transport choice.

May be decided autonomously after Preflight/architecture review if it does not change constitutional invariants, user-owned product intent or a materially privileged boundary.

### Tier 2 — trust-boundary / system-contract amendment

Examples:

- adding a new privileged capability class;
- broadening a credential's reach;
- materially increasing TCB;
- changing independence/blinding semantics;
- changing what evidence authorises merge;
- changing proof replay scope in a way that reduces replay;
- replacing an external enforcement boundary with an internal one.

Requires an explicit Architecture Change Proposal, independent architecture holdout and governor analysis. Owner escalation is required if the amendment weakens/changes a constitutional invariant, creates a meaningful risk/cost/privacy trade-off, or grants a new class of irreversible power. Purely equivalent/stronger implementation migrations may proceed autonomously if equivalence is mechanically proven and current owner policy permits it.

### Tier 3 — owner/product/constitutional decision

Examples:

- changing the mission or approved requirements;
- reducing a required safety/proof guarantee;
- accepting a known trust weakening for speed/cost;
- materially changing privacy/external-data policy;
- deciding a consequential product trade-off with multiple legitimate outcomes;
- changing a constitutional invariant.

Must be asked of the owner, one meaningful question at a time, with recommendation and consequences.

---

## 4. Default is not to ask the owner

The architecture/governance layer must distinguish:

```text
unknown fact
unknown engineering solution
unknown product preference
```

- repo fact → inspect;
- external fact → research;
- engineering uncertainty → Preflight/probe;
- product/constitutional choice → owner.

Do not escalate because a technical decision is difficult.

Escalate because **multiple materially different legitimate outcomes remain and the choice belongs to the owner**.

---

## 5. Architecture Change Proposal (ACP)

Material architecture changes use a canonical proposal:

```json
{
  "schema":"dark-factory/architecture-change-proposal",
  "schema_version":"1.0",
  "acp_id":"acp_...",
  "title":"...",
  "tier":2,
  "trigger":{
    "type":"new-evidence",
    "refs":["obs_...","inc_...","att_..."]
  },
  "affected_decision_refs":["archdec_..."],
  "affected_invariants":["capability-enforcement"],
  "problem_statement":"...",
  "current_design":"...",
  "candidate_ids":["cand_..."],
  "recommended_candidate_id":"cand_...",
  "proof_obligations":["..."],
  "migration_plan_ref":"...",
  "rollback_or_containment":"...",
  "owner_decision_required":false,
  "created_at":"..."
}
```

No material architecture change should exist only as terminal prose.

---

## 6. Required evidence before amendment

Before amending a locked architecture decision, record:

```text
1. exact decision affected
2. new evidence not considered previously
3. why current decision fails or is materially suboptimal
4. alternatives considered
5. cheapest probes/measurements performed
6. recommended amendment
7. trust/TCB impact
8. proof obligations needed to demonstrate equivalence/strength
9. migration and rollback/containment path
10. unresolved owner-owned trade-offs, if any
```

“I found implementation X awkward” is insufficient evidence.

---

## 7. Architecture candidate process

For Tier 1/2 changes:

```text
new observation/incident
      ↓
Long-Horizon Architect / architecture planner
      ↓ proposes 2–5 credible candidates when consequential
Preflight / static analysis / disposable probes
      ↓
measured facts + predictions + judgements kept separate
      ↓
recommendation (UNPROVEN)
      ↓
Architecture Governor checks constitutional/current-policy conformance
      ↓
Independent Architecture Holdout challenges assumptions/risks
      ↓
ACP accepted / rejected / owner question
```

The same actor must not be planner, governor and sole challenger.

---

## 8. Architect, Governor, Holdout

### Long-Horizon Architect

Untrusted predictor/designer.

May:

- propose target structure;
- reason about future coupling;
- compare migration paths;
- predict complexity/TCB effects.

Cannot authorise its own architecture.

### Architecture Governor

Protected/deterministic or bounded authority over explicit rules.

Checks:

- current constitutional invariants;
- dependency direction;
- forbidden authority combinations;
- TCB/capability growth declarations;
- schema/contract conformance;
- required migration evidence.

It should not invent product strategy.

### Architecture Holdout

Independent/blind challenger.

Looks for:

- hidden trust expansion;
- circular authority;
- unproved equivalence;
- omitted alternatives;
- failure modes;
- migration dead ends;
- policy words that implementation cannot actually enforce.

It does not see the architect's confidence/verdict where blindness policy forbids it.

---

## 9. Architecture Decision Record semantics

An architecture decision is immutable once accepted.

Amendment creates a new decision:

```text
ARCH-042 current
ARCH-057 SUPERSEDES ARCH-042
```

Do not edit old rationale into history as if the old choice never existed.

Decision record should include:

```text
status: proposed | accepted | superseded | rejected
scope
owner-product impact
constitutional impact
decision
alternatives
rationale
evidence refs
assumption refs
proof obligations
implementation/migration refs
supersedes / superseded_by
```

---

## 10. Assumption-driven reconsideration

Architecture decisions must list material assumptions.

Examples:

```text
GitHub App events trigger required workflows
hosted runners provide sufficient isolation primitive
mutation catalogue size remains shardable
database supports atomic lease CAS
```

When an assumption is invalidated:

- mark dependent decision `RECONSIDERATION_REQUIRED`;
- do not delete/undo it automatically;
- walk explicit dependency graph to affected implementation/programme nodes;
- suspend only actions whose safety/validity depends on that assumption;
- run architecture reconsideration using retained alternatives and new evidence.

This is where Project Decision Graph and architecture governance meet.

---

## 11. TCB ratchet

Maintain machine-readable TCB inventory with at least:

```text
component
classification
privilege
trusted LOC / approximate size
capabilities held
credentials reachable
what false authorisation a defect could cause
target classification
```

For each architecture change compute:

```text
trusted components added/removed
privileged operations added/removed
credential reach added/removed
ambient authority added/removed
trusted LOC delta
new external enforcement assumptions
```

Default preference:

```text
same guarantees + smaller TCB
>
same guarantees + same TCB
>
stronger guarantees + justified TCB growth
>
weaker guarantees (owner decision required)
```

Do not optimise trusted LOC mechanically at expense of clearer, stronger enforcement. The metric is a ratchet signal, not a game.

---

## 12. New privileged operation rule

Adding a new operation capable of:

- modifying canonical code;
- modifying protected policy;
- creating/altering merge authority;
- accessing privileged credentials;
- changing external production state;

is automatically at least Tier 2.

It requires:

- semantic capability name;
- broker/enforcement boundary;
- subject/resource binding;
- revocation/fencing semantics;
- independent observation where necessary;
- adversarial tests;
- explicit TCB inventory update.

No arbitrary shell/API capability may be smuggled in as “convenience”.

---

## 13. Proof-obligation change rule

Changes to qualification are classified by semantics, not speed.

### Safe optimisation candidate

Same proof claim, same or stronger authority, same dependency coverage, less redundant recomputation.

May proceed as Tier 1/2 depending on implementation privilege after equivalence proof.

### Proof weakening

Examples:

- removing a required authority;
- changing PASS from exact to probabilistic;
- accepting stale dependency mismatch;
- allowing builder verdict into independent slot;
- turning LIVE_WORLD replay into cached tree proof.

Tier 3 owner/constitutional decision.

The system must not rationalise a proof weakening as “performance work”.

---

## 14. Emergency stabilisation

A production/trust-root incident may require a fast bounded repair.

Emergency repair may proceed without full architecture exploration only if it:

- restores an existing invariant;
- does not weaken required proof;
- does not add broad permanent privilege;
- is narrowly scoped and reversible/containable;
- includes direct regression proof for the observed failure.

After repair, record the incident and whether broader architecture reconsideration is warranted.

The recent GitHub App identity repair is a model example: it restored the intended unattended authority path without weakening required checks.

---

## 15. Self-modification bootstrap rule

A proposed change to trust-root/governance code must not be judged solely by the proposed new version of that same code.

Required pattern:

```text
protected current authority
→ treats proposed trust-root change as data
→ determines whether change is allowed under current rules
→ candidate runs its own tests additionally
→ independent holdout/governor evidence
→ protected merge rules
```

After merge, new authority governs future changes.

This avoids “new constitution declares itself valid”.

---

## 16. Two-phase trust migration

When replacing a trusted boundary:

### Phase A — shadow/equivalence

Old authority remains authoritative.
New mechanism runs in shadow.
Compare verdicts/coverage on real corpus and adversarial cases.

### Phase B — cutover

Only after equivalence/strength evidence:

- update protected policy;
- make new mechanism authoritative;
- retain containment/rollback path;
- remove old duplicate authority after observation window or immediately if policy safely permits and equivalence is exact.

Do not remove the old proof first and hope the replacement is equivalent.

---

## 17. Architecture migration plans

Large target decisions should specify dependency order, not one giant ticket.

Each migration item should state:

```text
preconditions
new boundary introduced
old privilege removed
proof demonstrating safety
rollback/containment
TCB delta
```

Prefer migrations that create the enforcement boundary **before** moving orchestration/intelligence outside the old trusted component.

---

## 18. Architecture programme compiler

An architecture migration proposal may be generated intelligently, but a deterministic compiler should reject programmes that:

- remove an old authority before replacement proof exists;
- violate declared dependency order;
- require a capability not yet enforced;
- create circular trust dependency;
- omit required adversarial tests;
- combine independent migration steps whose failures cannot be attributed;
- silently change approved spec/product intent.

This is the architecture equivalent of programme `propose → compile`.

---

## 19. Owner question contract

When owner input is genuinely required, present:

```text
Decision needed
Why this belongs to you
Options (normally 2–4)
Material consequences/trade-offs
Recommendation
Default consequence of no decision
```

Ask one meaningful question at a time.

Do not ask for implementation details the system can research/probe.

Owner may answer `you decide`; that delegates the choice under stated constraints but does not waive constitutional invariants unless explicitly stated.

---

## 20. Architecture status classes

Use explicit states:

```text
OBSERVED
UNDER_INVESTIGATION
CANDIDATES_GENERATED
PREFLIGHT_EVALUATED
RECOMMENDED_UNPROVEN
ACP_PROPOSED
OWNER_DECISION_REQUIRED
ACCEPTED
IMPLEMENTING
SHADOW_PROVING
CUTOVER_READY
IMPLEMENTED
SUPERSEDED
REJECTED_BY_REALITY
```

These are project/architecture workflow states, not evidence-spine proof states.

---

## 21. Failure and rejected architecture

If the real factory repeatedly rejects a strategy for structural reasons, record:

```text
STRATEGY_REJECTED_BY_REALITY
```

Return to architecture/Preflight with:

- exact authority failures;
- affected assumptions;
- cost already spent;
- retained alternatives.

Do not repair an invalid architecture indefinitely at ticket level.

---

## 22. Architecture evidence retention

Retain:

- accepted/rejected ACPs;
- candidate analyses;
- probe evidence;
- holdout/governor outputs;
- assumptions;
- implementation outcomes;
- incidents that triggered reconsideration.

This history is useful for learning/calibration, but independent future holdouts must remain blind to prior verdicts where independence requires it.

---

## 23. Architecture learning

Derived lessons may inform architect/Preflight, e.g.:

```text
"GitHub event semantics differ for GITHUB_TOKEN-generated PRs"
"global worker setup creates high fixed no-op cost"
```

Lessons must include evidence/falsifier and cannot become constitutional rules automatically.

Promotion path:

```text
trajectory/incident
→ candidate lesson
→ supporting + contradicting evidence
→ backtest/historical validation
→ approved lesson
→ retrieval for non-blind planning roles
```

---

## 24. Adversarial governance tests

The governance model should refuse/flag at least:

- architecture change deleting an authority before replacement exists;
- proposal that calls a proof weakening a performance optimisation;
- new privileged capability with no broker/fencing semantics;
- model planner trying to approve its own Tier 2 change;
- new trust-root judge executed from candidate code;
- owner requirement silently rewritten by technical migration;
- superseded ADR edited in place instead of new decision;
- TCB expansion omitted from impact analysis;
- blind holdout given architect verdict/history;
- emergency repair attempting permanent proof weakening;
- changed architecture assumption failing to trigger dependent reconsideration.

---

## 25. Immediate use with current programme

Until #134/current reliability floor is settled:

- do not interrupt the live qualification chain with unrelated target-architecture implementation;
- continue promoting durable decisions to the architecture branch;
- use stabilisation mode for observed qualifier defects;
- classify follow-up issues separately;
- merge target architecture into main only when doing so will not create needless re-head/validation churn.

Architecture design may continue in parallel because it does not move `main`.

---

## 26. Locked conclusions

1. Dark Factory architecture is changeable, but amendments are explicit and evidence-led.
2. Ordinary engineering choices should remain autonomous; owner escalation is exceptional and ownership-based.
3. Architect predicts, Governor enforces, Holdout challenges.
4. Constitutional invariants cannot be silently weakened by implementation work.
5. Material trust-boundary changes require ACP + impact/equivalence evidence.
6. TCB/privilege deltas are mandatory architecture-change inputs.
7. Trust-root self-modification is judged by current protected authority, not solely by proposed new authority.
8. Large trusted migrations use shadow/equivalence before cutover.
9. Rejected implementation strategy can reopen architecture instead of producing endless repairs.
10. Architecture history is immutable/superseding, not rewritten retrospectively.
