# Dark Factory 2.0 — Programme Synthesis and Preflight Strategy Contract

**Status:** owner-approved target design.  
**Purpose:** define how an approved specification becomes a safe executable programme, and how consequential technical strategy is explored cheaply before one explicitly UNPROVEN candidate enters the trusted factory.

This document supplements `DARK_FACTORY_2_TARGET_ARCHITECTURE_AND_OVERSEER.md` and `DARK_FACTORY_2_CANONICAL_CONTRACTS.md`.

---

# PART I — PROGRAMME SYNTHESIS

## 1. Authority hierarchy

Locked hierarchy:

```text
APPROVED SPECIFICATION
        ↓
HARD CONSTRAINTS
        ↓
PROGRAMME
        ↓
ISSUES / EXECUTION
```

The programme is disposable/mutable execution strategy.

The approved specification is not.

Dark Factory may autonomously split, reorder, replace or cancel work. It may not silently change what V1 means.

---

## 2. Propose → compile

The programme synthesiser is untrusted reasoning.

It proposes:

- features/subfeatures;
- bounded programme items;
- ownership of requirement/acceptance coverage;
- dependencies;
- parallel workstreams;
- likely technical questions requiring Preflight.

A deterministic compiler validates the proposal before issues become executable.

```text
approved spec
↓
LLM programme proposal
↓
DETERMINISTIC PROGRAMME COMPILER
↓
valid executable DAG
```

The compiler owns structural authority, not product strategy.

---

## 3. Compiler obligations

At minimum fail closed on:

- cycles;
- references to missing nodes;
- incomplete requirement coverage;
- incomplete acceptance-criterion coverage;
- duplicated ownership where the policy requires exclusivity;
- blocker references that do not resolve;
- hidden scope mutation;
- malformed IDs/schema;
- issue decomposition so broad that acceptance is no longer bounded;
- impossible sequencing implied by the proposal.

The compiler does not decide whether an architecture is good. That belongs to Preflight/architecture reasoning and later trusted qualification.

---

## 4. Programme item contract

Each executable item should be independently understandable and bind to:

- approved spec hash/version;
- owned requirement IDs;
- owned acceptance IDs;
- explicit blockers;
- upstream programme identity;
- bounded objective;
- relevant hard constraints;
- any selected Preflight recommendation/handoff if technical strategy was preselected.

Do not rely on prose-only GitHub issue bodies as authority. Machine-readable references should be independently resolvable and verified.

---

## 5. Coverage must be bidirectional

The compiler must answer:

```text
For every required acceptance criterion:
which programme item(s) own it?

For every programme item:
which approved requirement/outcome justifies it?
```

Orphaned scope is challenged.

A required acceptance criterion with no executable owner is compile failure.

---

## 6. Programme replanning

A compiled programme is versioned and may be superseded.

Dark Factory may autonomously:

- split an oversized item;
- create discovered work;
- cancel obsolete work;
- replace failed technical decomposition;
- reorder ready work;
- change the critical path;
- exploit newly available parallelism.

But every replan remains bound to the same approved spec unless the user has explicitly approved a new spec version.

---

## 7. Ready frontier

The executable ready frontier is deterministic:

```text
item is active
AND
all blockers complete
AND
no conflicting resource lease
AND
programme/spec version current
AND
required handoff/authority prerequisites exist
```

Scheduling priority may remain orchestrator policy.

Eligibility itself should not depend on free-form model intuition.

---

## 8. Issue materialisation

GitHub issues can remain the operational surface initially.

A materialised issue should carry durable references such as:

```text
Project: proj_...
Programme item: item_...
Spec: spec_...@vN sha256:...
Factory handoff: handoff_... (if applicable)
Part of: ...
Blocked by: ...
```

The factory verifies referenced objects rather than trusting copied prose.

---

# PART II — PREFLIGHT / STRATEGY LAB

## 9. Core invariant

Preflight exists to answer:

> **Which technical hypothesis deserves the cost of trusted proof?**

It does not qualify.

Every Preflight candidate is `UNPROVEN` until the real factory proves it.

The governing rule is:

> **Explore cheaply outside the trusted factory. Prove rigorously inside it.**

---

## 10. Decide whether exploration is necessary

Do not run a tournament for every issue.

Initial deterministic trigger signals may include:

- architecture boundary crossing;
- multiple modules/bounded contexts affected;
- new dependency/runtime service;
- irreversible migration;
- security-sensitive design;
- significant persistence/data-model change;
- concurrency/distributed-system implications;
- public interface/API change;
- performance-critical requirement;
- difficult rollback;
- several materially different known approaches;
- high estimated implementation/qualification cost;
- high historical failure/repair rate for analogous work.

Allow explicit force/suppress policy where authorised.

A model may add a secondary signal that multiple serious architectures exist, but model intuition alone should not be the only exploration trigger initially.

---

## 11. Exploration modes

### Mode 0 — Direct

One obvious bounded implementation; no meaningful strategy choice.

Proceed to normal factory and record why exploration was skipped.

### Mode 1 — Light

2–4 credible strategies, cheap F0/F1/F2 evaluation, normally no probe unless one cheap uncertainty can change the decision.

### Mode 2 — Deep

Consequential/expensive/irreversible/uncertain. Normally 3–5 serious strategy families, bounded at 7 active candidates unless explicit policy allows more.

Candidate count is itself a cost and should later be calibrated from outcomes.

---

## 12. Candidate generation

Generate materially different causal strategies, not cosmetic code variants.

Bad:

```text
A Redis wrapper class
B differently named Redis wrapper
C Redis helper functions
```

Good:

```text
A optimise source query/no cache
B process-local cache
C shared cache
D materialised read model
```

Where meaningful include the **minimal-change baseline** and possibly `do nothing / solve elsewhere`.

User-proposed candidates enter the same pool with equal evaluation semantics.

---

## 13. Freeze hard constraints before comparison

Compile applicable hard constraints from:

- approved specification;
- repository architecture policy;
- security policy;
- programme dependencies;
- known technical invariants.

A candidate that violates a confirmed hard constraint is rejected, not merely penalised.

Do not trade hard constraints for a better score.

---

## 14. Pre-register decision policy

Before deep evaluation, record:

- hard constraints;
- priority ordering;
- measured criteria;
- prediction targets;
- qualitative judgement criteria;
- tie-breaks;
- exploration budget;
- stop conditions.

This reduces post-hoc rationalisation.

Do not create a fake universal `candidate_score = 82.4` unless a genuinely justified scalar objective exists.

Use constraint elimination, Pareto dominance, priority ordering and explicit tie-breaks.

---

## 15. Fidelity ladder

Every candidate starts cheap.

### F0 — history / prior retrieval

Retrieve analogous trajectories and approved lessons. State similarity dimensions rather than blindly transferring old conclusions.

### F1 — reasoning rollout

Predict likely implementation shape, interfaces, tests, failure modes, reviewer objections, migration implications and repair path.

Output is judgement/prediction, not proof.

### F2 — deterministic repository analysis

Measure what can be measured:

- dependencies;
- coupling;
- blast radius;
- architecture boundaries;
- public API impact;
- callers;
- test impact;
- migration surfaces;
- rollback scope.

Do not pay a model to guess a fact a program can measure.

### F3 — disposable targeted probe

Only for an uncertainty whose answer could materially change candidate ranking.

Each probe requires:

- exact question;
- why the answer can change the decision;
- cost/wall budget;
- disposable environment;
- interpretation rule.

No open-ended experimental implementation.

### F4 — Virtual Factory prediction

Later, after sufficient trajectories, predict qualification probability, repair count, cost, duration and likely failing stage.

Do not fabricate F4 before the data exists.

---

## 16. Breadth before depth

Default search pattern:

```text
generate several strategy families
↓
cheaply screen all
↓
deepen survivors
```

Do not fully investigate the first plausible candidate before considering alternatives.

This reduces anchoring and avoids spending deep-compute on an option that should have lost at F1/F2.

---

## 17. Candidate novelty / deduplication

Before adding a new candidate, determine whether it materially changes:

- architecture;
- dependency structure;
- data flow;
- operational characteristics;
- cost/risk profile;
- migration/reversibility.

If it differs only in low-level implementation detail, treat it as a variant, not a new strategy family.

Candidate population may still change after new evidence reveals a genuinely new strategy.

---

## 18. Dominance and Pareto frontier

A dominates B only when:

1. both satisfy applicable hard constraints;
2. A is no worse on every established relevant dimension;
3. A is strictly better on at least one;
4. no unresolved material uncertainty could plausibly reverse the comparison.

If A is simpler while B is faster and both remain viable, keep both on the Pareto frontier.

Do not force a false winner when a real trade-off exists.

---

## 19. Uncertainty registry

Maintain explicit decision-changing uncertainties per candidate:

```text
uncertainty
candidate
question
importance
current confidence
possible resolution
estimated resolution cost
```

The purpose of deeper exploration is to resolve **decision-changing uncertainty**, not simply accumulate more information.

---

## 20. Value-of-information rule

Before each deeper analysis/probe ask:

> **Could the result realistically change which candidate should be selected?**

If no, do not run it.

If yes, compare the expected value of improved decision quality with the cost of acquiring the information.

Early implementation need not fake exact economic maths; it must record why the information is worth buying.

---

## 21. Cost control

Track cumulative spend by:

- candidate generation;
- model reasoning;
- static analysis compute where material;
- probes;
- tool/provider calls.

At every escalation know:

```text
spent
remaining
estimated next step
```

Run automatically under approved exploration/probe thresholds.

Ask the user only when genuinely consequential evidence is worth buying but exceeds delegated spend authority.

Store estimated vs actual cost for calibration.

---

## 22. Long-Horizon Architect

For consequential Mode 2 choices, Pareto finalists receive an untrusted long-horizon architecture judgement:

> **What future architecture does this choice create?**

Consider:

- current/target architecture;
- approved future programme items;
- migration direction;
- technical debt;
- future reuse;
- reversibility;
- coupling/lock-in.

This informs selection but never replaces the trusted architecture governor/holdout.

---

## 23. Reversibility / option value

Where uncertainty is high, explicitly evaluate:

- rollback difficulty;
- irreversible data migration;
- public API commitment;
- dependency/vendor lock-in;
- future migration cost.

When candidates are otherwise close, preserving concrete future option value may break the tie.

This is not permission to build speculative abstraction.

---

## 24. Complexity must use observable proxies

Avoid fake precision such as `complexity=0.63`.

Prefer measurements:

- files/modules touched;
- dependencies added;
- boundaries crossed;
- new public interfaces;
- persistence concepts;
- runtime services;
- migration steps.

Then separately allow an explicit qualitative judgement explaining conceptual complexity.

---

## 25. Requirement sufficiency and anti-gold-plating

A candidate that exceeds a requirement is not automatically better.

Once all viable candidates comfortably satisfy the requirement, extra performance/generalisation has declining value.

Novel abstractions must justify their complexity against current approved requirements and credible near-term programme work.

Novelty increases uncertainty and therefore increases evidence burden; it is not automatically bad.

---

## 26. Convergence / stop rules

### Clear winner

Stop when:

- hard constraints satisfied;
- competitors are rejected/dominated/materially inferior under pre-registered policy;
- no unresolved decision-changing uncertainty could reasonably reverse the ranking;
- further exploration has low expected information value;
- budget does not justify more search.

### Sufficient confidence

If one candidate is materially stronger and residual uncertainty is unlikely to reverse the decision, recommend it while preserving alternatives/uncertainty.

Absolute certainty is not required; Preflight is not qualification.

### Budget exhaustion

Return best current candidate, remaining Pareto alternatives, unresolved uncertainty and what additional spend would resolve it. Do not invent certainty.

### Genuine subjective trade-off

Escalate only when multiple technically viable candidates differ on a user-owned priority that engineering evidence cannot resolve.

Present a narrow consequence-oriented choice; do not ask the user to choose architecture jargon.

---

## 27. Recommendation contents

A recommendation is incomplete unless it contains:

- selected candidate;
- reason;
- hard-constraint status;
- measurements;
- predictions with uncertainty;
- qualitative judgements;
- explicit assumptions;
- remaining risks;
- retained alternatives;
- why alternatives lost;
- revisit conditions;
- exploration cost;
- decision policy.

Every recommendation’s material assumptions become first-class graph nodes.

---

## 28. Factory handoff

Only one primary candidate normally crosses the trust boundary at a time.

```text
recommendation
↓
FactoryHandoff
qualification_status = UNPROVEN
↓
trusted Dark Factory
```

The real factory independently creates contract/design/RED/implementation/GREEN/review evidence.

Preflight evidence may inform work but never substitutes for factory qualification.

---

## 29. Failure return path

### Implementation-level failure

Examples: ordinary test failure, lint error, repairable bug/review finding.

Stay inside normal repair/retry. Do not reopen strategy selection.

### Strategy-level terminal failure

Examples:

- trusted architecture authority proves incompatibility;
- hard constraint cannot be met;
- required dependency/capability impossible;
- bounded repair proves the underlying strategy invalid.

Emit:

`STRATEGY_REJECTED_BY_REALITY`

Then:

```text
winner → rejected-by-reality
recommendation → stale
prediction → resolve against actual failure
remaining alternatives → reconsider
new winner → fresh factory run
```

This prevents endless repair of a fundamentally wrong strategy.

---

## 30. Reconsideration after changed assumptions

When a first-class assumption is invalidated:

```text
invalidate assumption
↓
deterministic graph walk
↓
affected recommendations/questions
↓
reopen only affected decision scope
↓
retrieve retained alternatives
↓
new Preflight exploration under current spec/repo state
```

Do not ask a model to guess the affected project surface from scratch.

Old candidate evaluations may be historical evidence but must not be blindly reused against changed spec/repo conditions.

---

## 31. Hierarchical exploration and coupled choices

Do not create a combinatorial cross-product of every subdecision.

Solve hierarchically when choices are separable.

When two choices are genuinely coupled (e.g. storage strategy determines sync semantics), record the dependency and evaluate compatible combinations.

The data model should eventually allow a candidate to contain subdecisions without requiring an early general-purpose constraint solver.

---

## 32. Historical retrieval scope

When reusing history, state applicability across dimensions such as:

- same repo;
- same subsystem;
- same task class;
- same architecture policy;
- same dependency/toolchain;
- similar scale;
- recency.

A past failure is not universal law.

---

## 33. External research

Preflight may research changing external facts such as:

- library/API capabilities;
- vulnerabilities;
- performance characteristics;
- vendor constraints;
- standards/licensing.

Record source and retrieval date.

External research informs strategy; direct repo/probe evidence is preferred when it can answer the question more reliably.

---

## 34. Security / migration / operational choices

Security-sensitive decisions should trigger lower thresholds for deep exploration and explicit threat assumptions, while final security qualification remains trusted.

Migration candidates must consider forward migration, rollback, partial failure, mixed-version operation, data compatibility and recovery.

New runtime infrastructure must be evaluated for deployment complexity, observability, failure/recovery, scaling, cost, local development and testing—not only code elegance.

New dependencies require justification of benefit, maintenance/security/licensing and replacement/exit cost.

---

## 35. Preflight telemetry and self-improvement

Per consequential question record:

- candidates generated;
- candidates pruned by fidelity level/reason;
- probe count/spend;
- total exploration cost/wall;
- recommendation/confidence;
- user escalations;
- whether the real factory later qualified/rejected the winner.

Later learn:

- which task classes benefit from tournaments;
- ideal candidate count;
- which probes change decisions;
- model/provider diversity value;
- cost/repair/qualification calibration.

Do not optimise these from tiny early samples.

---

## 36. User-facing semantics

Show a consequential question with 3–5 candidate branches where useful.

Expose status such as:

`Generated → Screening → Exploring → Probing → Pareto finalist → Recommended` or `Pruned`.

Every visible state must come from actual backend events.

User-origin candidate rejection is always explained explicitly.

Do not foreground every pruned system-generated alternative, but retain it in history.

---

## 37. Minimum viable Preflight

V1 does not need a proprietary predictor.

A credible first implementation is:

```text
deterministic exploration trigger
+ 2–4 candidate generation
+ hard-constraint screening
+ repo static analysis
+ qualitative rollouts
+ pre-registered decision policy
+ optional disposable probe
+ Pareto comparison
+ recommendation
+ UNPROVEN handoff
```

V2 may add history retrieval, calibrated cost/repair/qualification prediction, adaptive candidate count and learned model routing.

V3 may add a Virtual Factory predictor/world model while preserving the trusted factory as final authority.

---

## 38. Required integration benchmarks

### Programme coverage

Approved spec → proposed programme → deterministic compiler proves complete requirement/acceptance coverage and rejects a cycle/missing acceptance owner.

### Parallel ready frontier

Two independent programme items become ready concurrently; downstream item remains blocked until both required blockers complete.

### Preflight narrowing

Four credible strategies → cheap screen removes one → deterministic analysis dominates one → two finalists → targeted probe changes ranking → winner handed to factory UNPROVEN → qualifies.

### Preflight wrong

A recommended confidently → trusted authority rejects A → no merge → prediction miss recorded → B reconsidered → B independently qualifies.

### User candidate

A/B/C generated → user adds D → D receives identical evaluation → D loses → explicit evidence-backed explanation.

### Assumption reconsideration

B implemented under assumption A → later observation invalidates A → only dependent decision reopens → retained alternative becomes competitive → fresh factory migration.

### Direct mode

Simple mechanically forced issue records why no tournament ran and enters normal factory without unnecessary exploration cost.

---

## 39. Anti-cheating / anti-waste rules

- Do not hard-code benchmark winners.
- Do not use full trusted builds as cheap candidate probes.
- Do not ask the user technical questions the system can research/probe.
- Do not deepen a candidate when the information cannot change selection.
- Do not silently expand scope with attractive optional features.
- Do not treat a recommendation as proof.

---

## 40. Final algorithm

```text
APPROVED SPEC + PROGRAMME ITEM
↓
should we explore?
↓
MODE 0 / 1 / 2
↓
generate diverse strategy families
(include minimal-change baseline)
↓
freeze hard constraints
+ pre-register decision policy
↓
F0 history + F1 rollouts + F2 deterministic analysis
↓
reject invalid / dedupe / dominance prune
↓
Pareto frontier
↓
list decision-changing uncertainties
↓
for each possible next step:
  could it change selection?
  is information value worth cost?
↓
run only useful F3 probes within budget
↓
converged?
  ├─ clear/sufficient leader → recommendation
  ├─ genuine user-owned trade-off → ask narrow question
  ├─ useful evidence exceeds spend authority → request budget
  └─ budget exhausted → best current option + uncertainty
↓
FactoryHandoff: UNPROVEN
↓
trusted Dark Factory
↓
QUALIFY or STRATEGY_REJECTED_BY_REALITY
↓
calibrate predictions and preserve history
```

Programme synthesis determines **what bounded work exists and in what dependency order**. Preflight determines **which technical strategy deserves proof**. Neither can manufacture trusted qualification.