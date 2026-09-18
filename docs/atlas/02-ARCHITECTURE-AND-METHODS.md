# Dark Factory — Architecture and Methods

*Everything built and everything planned. Status-tagged throughout; tags reflect 7 Sep 2026.*

**[BUILT]** merged and evidenced · **[CLAIMED]** asserted, not independently evidenced · **[SPEC]** written at implementation grade, no code · **[AGREED]** settled in conversation only · **[OPEN]** deliberately undecided

---

# PART A — WHAT EXISTS

The repository is `dark-factory-2.0`, unforked from `coleam00/dark-factory-experiment`, standalone and private. Cole Medin's Archon-shaped runtime was not evolved — it was **replaced by a repository-owned Python kernel in PR #33**.

## A.1 The trusted spine **[BUILT]**

Design and governor artefacts feed exact-head impact, architecture-drift, architecture-conformance, architecture-holdout, mutation, ratchet and immunity claims. The gap named at the end of the origin thread — an approved design the coder could silently ignore — was closed structurally rather than documented away.

Components in `factory_kernel/`: `canonical.py`, `manifest.py`, `spine.py`, `independence.py`, `evidence_closure.py`, `provenance.py`, `authority.py`, plus `harness/merge_verify.py`.

## A.2 Authority and identity **[BUILT]**

- **Trust-root veto keyed to GitHub platform identity** (PR #37, merged `b2d2c51`). A PR opened by a User with OWNER, MEMBER or COLLABORATOR association may touch protected paths; a Bot author or a caller with no repository role may not. The ruleset bypass list stays empty.
- **Constitution reconciliation** (PR #38).
- **Dedicated GitHub App identity.** `credential_env.py` carries `DARK_FACTORY_APP_TOKEN` under a `github-mutation` scope with **no fallback**. Exactly three operations spend it: `push_branch`, `create_pr`, `merge_squash`. Everything else observes on `GITHUB_TOKEN`. Model workers get neither; `_worker_env` is an asserted allowlist, not an assumed one.

This is the first real piece of the long-term capability architecture (Part C.4) shipped ahead of the architecture that names it.

## A.3 Method layer **[BUILT]**

PR #55. Six pinned, protected instruction texts in `.factory/methods/` with a manifest recording source, upstream ref and roles, injected per role into bare workers. No plugin runtime; `--bare` untouched. Contents: executable diagnostic feedback loops for bugs, domain vocabulary and ADR context, deep-module discipline, a minimal-complexity ladder ("Ponytail"), vertical tracer-bullet implementation, and a two-axis review split into **Spec Review** and **Standards Review**.

## A.4 Reproduction hardening **[BUILT]**

PR #61, closing objections raised against #57: permitted test-runner **command shapes** rather than executable-name allowlisting; `npx` and arbitrary Python removed; environment allowlist; before/after Git-tree integrity check; six targeted attacks against those boundaries.

```
investigate worker → proposes test-shaped repro → kernel validates command shape
→ tiny allowlisted environment → no provider/GitHub/validation secrets
```

## A.5 Measured floors **[BUILT]**

Ratcheted with zero slack as evidence arrived: unit floor 549 → 766 → **1033**; anchors 428/9/26; static checks 5; holdout assertions 9; application mutations 9; independent mutation catches 3; security mutation catches 3. A full suite observed at 88 green / 0 red on the App branch. Periodic full-main regression is live.

**`e2e_steps` is deliberately absent.** The floor file records no number rather than inventing one, and waits for the first complete autonomous cycle.

## A.6 What has not been proven

**One complete Level-4 lap has never been observed under the current production kernel.**

`FACTORY.md` declares autonomy level 4 — a human supplies issues; the factory triages, builds, validates and merges without a human reading the product diff. The audit's verdict: *Level 4 is architecturally supported but not yet empirically earned.*

The canary (issue #49) is the instrument. It has already justified itself twice:

1. **OpenRouter routing was wrong.** Raw preflight succeeded, but Claude Code appended `/v1/messages`, producing the wrong effective endpoint. PR #51 made preflight exercise the actual pinned CLI in the same shape as the real worker.
2. **The repository lacked labels the kernel writes.** Triage accepted #49, then failed applying `priority:medium`. PR #52 derived preflight's label requirements from the kernel's own vocabulary.

The loop this establishes, treated in the record as more valuable than further architecture:

```
real execution → discover hidden assumption → diagnose exact failure class
→ narrow fix → regression test → mutation/immunity entry → retry canary
```

**[AGREED]** Every subsequent canary failure gets the same treatment. Never "that was just configuration." Always: *why was the factory able to reach an execution state for which its preflight and evidence model had no detector?*

By 7 Sep the canary had proven the App identity path. PR #149, opened by `app/shaishibear-dark-factory`, went straight to `in_progress` with no `action_required`; `pull_request_target` fired unaided; trust-root authority executed per head (`9821025c`, then `ca88ed3d`); head movement re-fired the authorities; nothing was approved by hand. For contrast, trust-root authority had never run on a `factory/*` branch across 100 sampled runs, and `unattended-merge` correctly reported *skipped* on #149 — autonomous PRs are never armed.

## A.7 The system's own scoring (5 Sep)

Core architecture, evidence/provenance, trust-root separation, architecture governance and independent review: 98. Methodology layer 96. Mutation/immunity 97. Operational self-healing 94. **Live proven Level-4 autonomy ~88.** Overall ~95. The withheld points are entirely A.6.

---

# PART B — THE AUDIT: THE SYSTEM FALSIFYING ITSELF

These are the strongest evidence in the record that the design works, because in each case a claim the system made about itself failed under inspection.

## B.1 The trust-root maintenance deadlock — the P0

`main` required pull requests, linear history and the `quick-authority` check, with no bypass actor. But `quick-authority` runs `factory_security.py --pr`, which deliberately rejects modifications to `MISSION.md`, `FACTORY_RULES.md`, `CLAUDE.md`, `factory_kernel/**`, `.factory/**`, `.github/**`, `harness/**`, `scripts/factory_*`. Meanwhile the old `FACTORY_RULES.md` instructed that constitutional changes be made by direct human commit to `main`, which the ruleset also prohibited.

**Direct human push prohibited. Human trust-root PR rejected by its own required check.** PR #36 was itself a legitimate human trust-root security correction and failed precisely there; it merged only because it landed before the ruleset tightened.

> "The judge cannot rewrite itself" must mean *autonomous product agents cannot modify the judge*. It must not mean *nobody can safely maintain the judge*.

**Disposition: [BUILT]** — resolved by keying the veto to platform identity, not by widening the bypass list.

## B.2 Documentation drift as a live correctness defect

`FACTORY.md` was correct. `FACTORY_RULES.md`, which *claimed process authority*, still described Archon workflows, `coleam00` as owner, a VPS orchestrator, `MAX_PARALLEL=4`, superseded merge sequences, and a workflow that no longer existed. Only two active workflows remained. Its separation-of-concerns section still asserted the validator must not read builder design artefacts, which the new architecture deliberately inverts.

The generalisable insight: **in an agentic system, stale documentation is not hygiene debt. It is a live input to the workers, and therefore a correctness defect.** Rated the largest quality risk to the agents themselves, above any missing feature.

**Disposition: [BUILT]** — PR #38, plus the later decision that a v2→v3 architecture transition needs a *documentation migration PR* with cross-links, so a worker cannot reach the old component descriptions without meeting the new runtime model.

## B.3 The holdout that was not demonstrably blind

`.factory/holdout/run.py` described its assertions as things the builder cannot read. But build-side agents hold `Read`, `Glob` and `Grep`, and `.factory/holdout/` was protected only from **modification**. Protected-from-writing is not hidden-from-reading.

Classified: *tamper-resistant, but not demonstrated builder-blind.* The isolated validator-side certifiers solve it correctly; the in-repo holdout should either be genuinely isolated or honestly relabelled a protected adversarial regression suite.

## B.4 Two lifecycles in one system

`factory_kernel/state.py` defines the intended lifecycle (intake → spec → tickets → frontier → context → design → architecture → test plan → RED → implement → GREEN → impact → conformance → holdout → mutation → ratchet → evidence → merge auth → merged → post-merge → complete) and `FACTORY.md` labels it authoritative. But `KernelRuntime.build_issue()` and `validate_pr()` remained a procedural sequence in `runtime.py`; production dispatch did not consume the transitions.

Not a security hole — the evidence spine still makes stage-skipping hard to merge. But two representations of one lifecycle where the design permits one. This is the finding behind the later rule "do not build a second state machine": the duplicate already exists, and the instruction is to converge, not abstain.

## B.5 The ratchet lagging its own evidence

Floor at 549 while PR #33 had already recorded 766, against a stated zero-slack rule. Strong mechanism, not kept current. Since corrected to 1033.

## B.6 The Matt Pocock finding

A stated capability was **false**: the autonomous factory runs `--bare` and never loaded the skill plugin it claimed to use.

The response is the substantive part. Do not switch off `--bare` — it is part of the isolation architecture. Reimplement the valuable content natively (A.3). Also honest in the same pass: the system's TDD is not the same discipline as Matt's; domain modelling was "mostly aspirational" and marked dormant; the deep-module ideology was implemented well while the review *independence structure* was not.

---

# PART C — THE TRUSTED CORE

## C.1 Diagnosis

`runtime.py` had merged nine systems: scheduling, resource ownership, workspace orchestration, model execution, engineering lifecycle, evidence handling, GitHub workflow, validation, merge. Everything it touches is inside the TCB by default.

**The goal is fewer kernel responsibilities, not smaller kernel files.** Ten 370-line trusted files improve maintainability and reduce the TCB by nothing.

## C.2 Target kernel **[SPEC]**

Claim-driven state-transition authority. It understands little beyond run identity, issue/programme identity, approved specification identity, revision identity, evidence dependency closure, attestation currency, independence rules, capability authorisation, allowed transitions, and merge authorisation.

```
orchestrator: proposes what should happen
kernel:       decides whether it may happen
```

Merge is the most privileged transition.

## C.3 TCB measurement **[SPEC]**

A machine-readable **TCB manifest** classifying all code; a trusted-LOC report; and a **TCB growth ratchet** — the trusted surface may only shrink. Making the trust boundary an explicitly budgeted, monotonically improving quantity is the mechanism that keeps the constitution honest over years rather than weeks.

## C.4 Capability model **[SPEC]** (`DARK_FACTORY_2_CAPABILITY_MODEL.md`, commit `ffa6623`)

> **A component may request privilege; it does not gain privilege merely because it knows how to ask for it.**

```
agent/orchestrator requests action
        ↓
kernel checks evidence + subject + lease
        ↓
narrow capability issued
        ↓
privileged broker performs fixed operation
        ↓
independent observation checks result
```

Capabilities are **semantic operations**, not generic escape hatches:

```
workspace.import-diff
git.commit.create
github.pr.create
github.branch.push
github.merge.exact-head
credential.model.invoke
```

Never `shell.exec`, never `git.exec`, never "here's a token, behave yourself."

Locked with it: capability binding to exact subject and revision · lease generation fencing · one-shot merge capabilities · no privilege amplification or delegation by default · **credentials are implementation mechanisms, not capabilities** · `WorkerViewBroker`, `GitMutationBroker`, `GitHubMutationBroker`, `MergeBroker` · actual filesystem blindness rather than prompting workers not to look · independent observation after every consequential mutation.

## C.5 Four warnings against cosmetic security

- Do not call providers untrusted while they still enforce filesystem permissions.
- Do not call GitHub adapters untrusted while they hold unrestricted merge capability.
- **Do not confuse isolation with trust reduction.** Moving code out of a file is not moving it out of the TCB.
- Prefer a **worker view** over prompt-based filesystem trust; restrict command *shapes*; allowlist environments; assert the repository is unchanged after a repro run. *(**[BUILT]**, PR #61.)*

And the separation rule: **do not allow an untrusted adapter to both perform a consequential action and provide the only trusted observation proving that action safe.**

## C.6 Re-head as its own authority **[SPEC]** — *amended DFE-008*

The orchestrator may decide whether attempting a re-head is worthwhile; a `ReheadAuthority` decides whether evidence was genuinely preserved. Re-head is **claim-by-claim preservation and reissuance**, never a blanket "old evidence is still good."

**Amendment.** As originally written, claim-by-claim reissuance requires the `ReheadAuthority` to understand the semantics of every claim type — what makes a GREEN claim preservable across a head move, what makes a live-world claim unpreservable, what a mutation shard depends on. That is domain expertise accumulating inside a trusted judge, which is the exact thing R4 forbids. The expertise would have moved out of `runtime.py` without shrinking, and `ReheadAuthority` sits in the trust path by construction.

Corrected design: **re-head derives from the attestation dependency model (I.1) rather than knowing claim types.** It preserves exactly those claims whose dependency digests are unchanged across the head move, and reissues nothing else. Re-head becomes a query over the dependency graph — a few dozen lines — instead of a growing catalogue of claim-specific knowledge.

The two designs were already adjacent in the corpus. This joins them. **This is the most likely place the TCB quietly regrows, and the correction is cheap now and expensive later.**

## C.7 Adversarial benchmarks before feature work **[SPEC]**

Kernel tests (K-ADV-1..5) · capability and sandbox tests (CAP-ADV-1..4) · git tests (GIT-ADV-1..3) · merge tests (M-ADV-1..3) · lease tests (L-ADV-1..4). Named archetypes: the **evil orchestrator**, **evil provider** and **evil GitHub adapter** — components that lie about what they observed.

---

# PART D — HOW THE FACTORY CHANGES ITSELF

**[SPEC]** `DARK_FACTORY_2_ARCHITECTURE_GOVERNANCE.md`, commit `d2eeda7`. The constitution, the four tiers and the self-modification rule are in `01-CONSTITUTION.md`; this section covers the machinery.

## D.1 The Architecture Governor **[BUILT]**

A fresh authority separate from ticket designer and coder, backed by persistent `.factory/architecture.json` holding principles, active migrations and debt/hotspots, with verdicts bound to exact contract, context, design and policy hashes.

Structural rules: *regresses + proceed* is impossible · prefactor or decompose requires real structural work · scope cannot override an architectural veto · decomposition preserves required prefactors as dependency-ordered child tickets so the same conflict cannot recur.

## D.2 Three architecture roles **[SPEC]**

```
Architect predicts → Governor enforces → Holdout challenges
```

The Long-Horizon Architect reasons about multi-quarter structure and may participate in Preflight. It has no authority; its predictions are calibrated against outcomes like any other prediction.

## D.3 Architecture Change Proposals

Tier 2 changes require an ACP carrying new evidence, alternatives, TCB effects, proof obligations and a migration path. Shadow and equivalence first, cutover second.

The escalation format when a locked boundary turns out to be wrong:

```
decision affected
new evidence
why the existing decision fails
alternatives
recommended amendment
consequences
```

**Do not silently drift the architecture.**

---

# PART E — THE INTELLIGENCE LAYER

All **[SPEC]**, all deliberately outside the TCB, and all specified at implementation grade rather than sketched. Full registers in Part J.

## E.1 Front Door / "Grill Me" (87 decisions)

The triggering observation: a sophisticated autonomous backend had no tolerable front end for the human deciding *what* to build. The interview is the only place the system may ask questions; everything below the **autonomy boundary** is autonomous.

**Core behaviours:**

- **The default is not to ask.** Ambiguity is detected through *interpretation divergence*: generate competing readings, ask only where they produce materially different behaviour. Ambiguity must be *material*.
- One question at a time, ranked by decision value, never with manufactured scores.
- **Nine ownership classes for unknowns** — repository fact · external factual question · engineering decision · user-owned product decision · user preference with a reasonable default · optional unrequested capability · inherited constraint · feasibility uncertainty · contradiction in user intent. **Only three are legitimately the user's.**
- **Research before asking** anything researchable, bounded.
- **Scenario synthesis mandatory before completion.** Off-nominal scenarios expose hidden requirements.
- Detect solutions masquerading as requirements · optional features default *out* · existing behaviour is a strong default.
- Preserve original user wording · avoid jargon · support delegation and deferral.
- Append-only **interview ledger** · provisional intent model · internal topic-coverage map · full replayability.
- Traceability mandatory; acceptance criteria are behaviour anchors and must not over-specify design.
- Approval is **explicit and immutable**; no silent mutation afterwards; amendments are delta-oriented; **the Front Door must not create GitHub work directly.**
- Spec approval does not mean feasibility is proved, and known likely infeasibility is surfaced anyway.

**Seven anti-patterns:** questionnaire mode · consultant upsell · solution anchoring · confirmation bias · treating the user as an engineer · endless completeness · silent defaults.

**Learning:** measure question usefulness **and missed questions** — a question that should have been asked is as learnable as one that was.

> **Amendment DFE-006 — the coverage map is policy, not a learnable parameter.** The Front Door's output *is* the approved specification, the object every downstream judge measures against. An interviewer that has learned *"users approve faster when I don't ask about X"* is optimising for **approval**, not **correctness** — and approval comes from a human, not an authority. Nothing in the corpus falsifies that drift: the self-improvement guard covers policy proposals about the factory's mechanisms, not the intake layer learning to shape intent. This is the one place where learning can degrade trust without touching a trust boundary.
>
> Two corrections. **Scope:** interview learning may optimise question *ordering, phrasing and burden*. It may never modify the **topic-coverage map** — the set of topics that must be covered before a spec is approvable. Coverage is policy; changing it is Tier 2 and needs an ACP. **Metric:** measure *defects and reconsiderations later traced to an unasked question*, never user satisfaction with the interview.

**Benchmark:** 14 cases (A–N) covering already-complete specs, hidden ambiguity, researchable facts, contradiction, delegation, mind-changing, probable infeasibility, low-impact ambiguity.

## E.2 Programme synthesis

Diagnosis: *Dark Factory has dependency execution but not programme synthesis.* It honours `Part of #12` and `Blocked by: #7` and works a ready frontier, but a human creates the children. Triage rules already blessed "children created by decomposition" — a prepared seam with no decomposer behind it.

Design: **propose → deterministically compile.** An untrusted synthesiser proposes features, subfeatures, tickets and dependencies; a deterministic compiler checks coverage, cycles, prerequisites, duplication, bounded scope and authority before anything becomes executable. Then a bounded issue materialiser, a ready frontier over the compiled DAG, and replanning, cancellation and discovered-work semantics.

Hierarchy, locked:

```
APPROVED SPECIFICATION → HARD CONSTRAINTS → PROGRAMME
```

**Programme state is not product intent.** The plan may change; the approved specification may not.

## E.3 Preflight / Strategy Lab (81 decisions)

Originally an in-factory "Design Tournament." The user's counter-proposal — run options analysis *outside* the factory to lower cost and simulate how the project would go — was adopted, and is the better design because losing candidates no longer require duplicated trusted contract, context and review stages.

**Purpose:** spend cheap intelligence to avoid spending expensive trusted execution on poor approaches. **Preflight may never qualify.** Its verdict is always UNPROVEN.

**The fidelity ladder**, escalated only while the answer stays uncertain:

| | |
|---|---|
| **F0** | historical prior — what happened in analogous previous work |
| **F1** | reasoning rollout — project the likely implementation and failure path |
| **F2** | deterministic repository analysis — dependencies, coupling, blast radius, architecture boundaries, impacted tests, manifest changes |
| **F3** | disposable targeted probe |
| **F4** | learned Virtual Factory prediction (later) |

**Machinery:** three exploration modes (Direct / Light / Deep) · always include the baseline candidate · freeze hard constraints *before* comparison · **pre-register the decision policy** · refuse to invent a universal score · cheap invalidity screening first · dominance pruning · preserve the Pareto frontier · uncertainty registry · model diversity · reversibility as an explicit criterion · complexity via observable proxies · candidate *graph* not flat list · scoped historical retrieval · reason codes on every rejection · never prune on model preference alone · detect coupled choices.

**The value-of-information rule:**

> Could the result realistically change which candidate should be selected?

If no, do not run it. If yes, weigh `probability the result changes the decision × value of the improved decision` against `cost of obtaining the information`. Perfect economics is not required, but the system must record *why this information is worth buying*.

**Probe threshold:** run automatically only if estimated cost is within the probe autonomy limit AND exploration budget remains AND the probe could alter the decision. **Do not ask the user to approve every £0.10 probe already inside an approved budget.**

> **Amendment DFE-005 — V1 does not implement the economics.** The value-of-information rule requires `probability the result changes the decision × value of the improved decision`, with no calibration data anywhere and thresholds explicitly deferred. Shipping it as written reduces to a model's unverifiable guess dressed as a calculation, which is the fake precision DFA-017 forbids.
>
> V1 is crude and explicit instead: a **fixed probe budget per consequential question**, a **hard cap per issue**, escalation above the cap, and a log of every probe with its cost and whether it changed the decision. Fit the economic rule to that data after a hundred probes. The V-o-I *question* — could this realistically change the selection — stays; only its arithmetic is deferred.

> **Amendment DFE-007 — measure candidate-space coverage, not just ranking.** DFA-045's adversarial benchmark proves the factory can overrule a confidently-chosen candidate. Neither it nor anything else in the 81 rules detects the failure that matters more: **Preflight never generating the winner at all.** A systematically narrow generator does not produce false proofs; it produces true proofs of the wrong thing, and its calibration data — conditioned on its own generation bias — will look excellent throughout.
>
> Add sampled **runner-up construction**: periodically, budget permitting, build the second-placed candidate as well as the winner and score the prediction against both. This is the only instrument that distinguishes *the ranking was right* from *the good answer was in the set*.

**The escalation predicate** — three conjuncts, all required:

```
two or more candidates satisfy all hard constraints
AND the difference depends on a user-owned priority
AND available technical evidence cannot resolve that priority
```

And then ask a compressed product question, never *"which architecture do you prefer?"*

**Four stop rules:** clear winner · sufficient confidence · budget exhaustion · genuine subjective trade-off. **Search exhaustion is explicitly not required.**

**Failure semantics:** *implementation-level failure* (test failure, lint, ordinary defect, repairable review issue) stays inside the normal factory repair path and does **not** reopen candidate selection. *Strategy-level terminal failure* returns to Preflight, marks the prediction wrong, eliminates the candidate and selects the next. **A failed winner teaches more than an ordinary failure.** Rejected candidates remain revivable.

**Success criterion:** *Preflight usually sends strong candidates + the real factory remains capable of rejecting them + rejections improve future prediction.* Stated as: **Preflight is successful if the factory rarely disproves it — but still can.**

**Minimum viable V1** (do not wait for learning models): deterministic trigger + 2–4 candidates + hard-constraint screening + repo static analysis + qualitative rollouts + manual decision policy + optional disposable probe + Pareto comparison + recommendation.

## E.4 The Project Decision Graph (`DARK_FACTORY_2_PROJECT_DECISION_GRAPH_SEMANTICS.md`, commit `bc133cf`)

The **memory spine**: the project's causal brain.

```
GraphCommand → validated ProjectEvent → append-only project history → rebuildable projections / UI
```

Node families kept explicitly distinct: Requirement · Question · Candidate · Recommendation · Decision · Implementation · Observation · Incident · Assumption · Evidence.

**A recommendation is not a decision. A user suggestion is not a requirement. A delegated technical decision does not silently amend the approved specification.**

**The invalidation algorithm** is the payoff. When an assumption becomes *challenged*, dependent decisions are flagged. When it becomes *invalidated*:

```
assumption invalidated → recommendation stale → decision reconsideration-required
→ dependent future programme items blocked
```

Propagation stops at the **minimal reconsideration frontier**. It does not rebuild the product. If reconsideration selects the same architecture under the new evidence, unaffected downstream implementation stays valid. This is the project-level equivalent of the proof-dependency system in Part I.

Also: assumptions are first-class · recommendations carry explicit assumptions · propagation follows *recorded* edges, never model guesswork ("what do you think this might affect?" is forbidden where edges can answer it) · rejected alternatives durable · **no graph database merely because this is a graph**; relational is the likely default.

## E.5 User-steerable exploration

Two verbs, **Add Question** and **Add Direction**.

- User candidates receive **equal evaluation but asymmetric reporting**: no scoring privilege, no scoring penalty, but if pruned the system must explain why.
- **User steering does not command implementation.** "Use D" means strong preference within exploration, not bypass Preflight and the factory — unless D is an approved product constraint.
- **Every added exploration has a price**, surfaced as such.
- Classification is the dangerous step: deciding whether a user's input is exploration, preference, possible requirement or hard constraint is exactly where approved intent could be silently rewritten.

---

# PART F — THE CANONICAL DATA MODEL

**[SPEC]** `DARK_FACTORY_2_CANONICAL_CONTRACTS.md` — 76 numbered decisions. The part that would actually be implemented first.

## F.1 Foundations

Compatibility rule · canonical JSON rules · **ID strategy** (opaque, globally unique, stable, type-prefixed: `proj_ spec_ req_ q_ asm_ cand_ rec_ ev_ evid_ prog_ item_ run_ lease_ att_ cap_ inc_ lesson_`; never reuse an ID for a different semantic object; a superseding object gets a new ID) · common provenance object · reference object · **hashing rule** (never include a top-level `sha256` in the bytes that hash produces; hash the canonical object without self-hash and store it as envelope metadata) · time and ordering (wall clock is for humans and durations, not concurrency order) · unknown-field handling · deletion semantics · privacy boundaries · schema evolution rules · event-type registry · status vocabulary rule.

## F.2 The two decisions that carry the most weight

**Trust class is explanatory, not authorising.** A `trust_class` field may read `exploratory | measured-untrusted | trusted-authority | derived`, but *do not make a string field itself create authority*. Authority derives from the enforcement path.

**References are not authority:**

```
authority = "green-replay"   does not make it authoritative
spec_sha256 = X              does not prove it references the approved spec
network = false              does not disable network
```

Always distinguish **declared metadata** from **independently enforced fact**. This must remain central to implementation review.

## F.3 The object set

Approved Specification (requirement IDs, promotion rule, immutable approval) · Front Door interview ledger · graph command · canonical graph event · node materialised envelope · edge · question · user-added question event · assumption · assumption invalidation event · candidate · user-added direction · evidence · prediction · disposable probe · candidate evaluation record · recommendation · recommendation decision rule · rejection record · programme · compiled programme DAG · factory handoff and its binding to the current issue system · factory run · kernel state-transition request · authority attestation · authority independence metadata · capability grant · Lease v2 semantics and acquisition result · exploration budget · user cost approval command · trajectory · trajectory stage attempt · prediction outcome · strategy failure · implementation component · observation · incident · reconsideration · engineering lesson · lesson retrieval packet · external source evidence · materialised project snapshot · live UI event envelope · frontend action semantics.

Plus: where schemas live · code generation from schemas · schema tests · cross-schema invariant tests · schema ownership · do not over-normalise text · **security principle: references are not authority**.

## F.4 Rollout order — treat as binding

```
Phase 1  foundation     trajectory, authority-attestation, capability-grant, lease evolution
Phase 2  front door     project, interview-entry, approved-spec, programme,
                        compiled-programme, factory-handoff
Phase 3  preflight      question, assumption, candidate, evidence, prediction, probe,
                        evaluation, recommendation, rejection, budget
Phase 4  graph          graph-command, project-event, node/edge materialisation, snapshot
Phase 5  learning       observation, incident, reconsideration, prediction-outcome,
                        engineering-lesson, lesson-packet
```

Before adopting any of it: compare every proposed schema to existing repo artefacts, identify collisions and redundant concepts, preserve current trusted artifact formats where they already solve the problem, record the decisions, implement a small shared vocabulary/ID/reference convention, then schema-by-schema as the programme requires. **Do not create an enormous generic schema package that nothing uses.**

> **Amendment DFE-004 — split the document, because documents exert gravity.** The corpus warns against a generic schema package nothing uses, then specifies roughly 49 schemas none of which has a consumer. The rollout order above is correct; the risk is that forty further schemas sitting in a file called *canonical contracts* get cited as decided, and someone implements `IncidentSchema` before an incident exists.
>
> Phase 1 schemas stay in `CANONICAL_CONTRACTS.md` as **normative**. Phases 2–5 move to `PROPOSED_CONTRACTS.md`, headed with a statement that nothing in it is decided until it has a consumer. One file move; removes the gravity.

## F.5 Acceptance benchmark for the data model

The model succeeds when the full historical chain — vague intent → interview history → approved spec → programme → question → candidates → probes → recommendation → decision → factory run → attestations → merge → implementation → later observation → assumption invalidation → targeted reconsideration → revived candidate — can be represented **without destructive mutation**.

The end state:

> **A rich append-only engineering memory surrounding a small evidence-driven authorisation kernel.**

The four choices judged most valuable: append-only project events + first-class assumptions + immutable spec versions + a narrow UNPROVEN factory handoff.

---

# PART G — LEARNING

**[SPEC]** `DARK_FACTORY_2_LEARNING_AND_EXPERIENCE.md`, commit `dcd815f`.

> **Learning may improve what the factory tries. It must not decide what the factory proves.**

## G.1 Three layers

```
immutable trajectories → derived analytics → candidate engineering lessons
→ counter-evidence / backtest → approved retrieval lessons
```

Layer 1 is raw and never rewritten. Layer 2 lessons carry scope, supporting evidence, contradicting evidence, applicability, **a falsifier**, and a confidence method. Layer 3 is learned predictive models: cost, repair, qualification, model routing, probe value, adaptive candidate count, and eventually the Virtual Factory.

Two loops that must not be conflated: *product engineering learning* (what to build) and *factory engineering learning* (how to build).

## G.2 The precondition **[SPEC]**

Extend run-artifact retention from **7 days to 90**. Cheapest item in the record; everything else here depends on it, because you cannot learn from evidence you have deleted. Siblings: create a trajectory for every meaningful dispatch, build and validation attempt; append every repeated worker attempt; test that artificial failures at early stages still leave useful trajectory evidence. Trajectory data and trusted evidence remain distinct objects.

## G.3 The ExperiencePacket

Instead of loading 130,000 tokens of old terminal transcript:

```
trajectory → structured compact packet → 3–5 highly relevant lessons/examples
```

with raw transcript fetched only when genuinely necessary.

## G.4 The blindness boundary — structural, not requested

```
Programme / Preflight       learning allowed
Builder                     bounded learning allowed
Orchestrator                learning allowed
Long-Horizon Architect      learning allowed

Code Holdout                NO
Architecture Holdout        NO incompatible prior verdicts
Independent certifiers      NO where blindness required
Kernel                      no need for learning
```

Enforced via capabilities and worker view, never via *"Claude, please ignore what you remember."*

## G.5 The self-improvement guard

A lesson like *"model X is better for architecture reviews"* may influence routing. A lesson like *"architecture reviews are unnecessary"* may **not** become a lesson at all — it is a policy proposal, and must travel:

```
Observation → Lesson hypothesis → Backtest → Architecture Change Proposal
→ Governor review → Policy change
```

Without this, the factory eventually learns that the easiest way to succeed is to remove the tests.

## G.6 Calibration

Every Preflight prediction retains candidate, predicted outcome, predicted probability, predicted cost and predicted repairs, and is scored against the real factory. The interesting target is not where the system is wrong but **where it is systematically overconfident**.

## G.7 Sequencing

Raw trajectories first · build the learner offline and read-only · **factory knowledge before broad software knowledge** · no learning before trajectories are durable. And the honest note: the code is not the long pole, **the dataset is**.

---

# PART H — THE INTERFACE

**[SPEC]** A three-zone live graph, projected from real event data.

- **Zone A — Exploration:** questions, candidates, assumptions, probes.
- **Zone B — Dark Factory:** the trusted run, evidence, attestations.
- **Zone C — Implemented Infrastructure:** the persistent architecture graph that outlives individual runs.

Seven views: decomposition · question tree · solution tournament · handoff animation · execution timeline · infrastructure crystallisation · incident rewind and alternative-path.

**Hard rules:** the UI is projection, never authority — it cannot invent activity or promote an exploratory choice into an approved requirement. Trust must be visible via the four-state vocabulary. Direct graph steering (*+ Add question*, *+ Explore another direction*) shows the user node immediately with provenance, then proceeds through real backend transitions.

**Sequencing:** no graph rendering before backend event semantics exist. The event transport (WebSocket vs SSE) is explicitly **[OPEN]** and must not be part of the core architecture; define an abstract event-stream contract requiring ordering where needed, reconnect/resume, event identity and idempotent client application.

---

# PART I — MAKING PROOF CHEAPER WITHOUT MAKING IT WEAKER

## I.1 The attestation dependency model **[SPEC]**

`DARK_FACTORY_2_ATTESTATION_DEPENDENCY_MODEL.md`, commit `04eb0b6`. Described in the record as the most important performance architecture work, and the strongest single decision in the corpus.

> **Proof is reusable because its dependencies are identical, not because it is recent.**

Every blocking proof becomes:

```
Claim + Authority + Subject + Dependencies + Policy + Program/Toolchain Identity → Attestation
```

Dependency classes, explicitly separated:

```
SUBJECT_BYTES · TRUST_ROOT · POLICY · AUTHORITY_PROGRAM
TOOLCHAIN · EXTERNAL_STATE · PREDECESSOR_ATTESTATION
```

Staleness becomes deterministic:

```
something changes → recompute affected dependency digests
→ which attestations depended on them? → only those become STALE
→ propagate through composed proof → run only missing/stale authorities
```

So the factory can eventually say:

```
GREEN               current
Code holdout        stale: product tree changed
Factory mutations   current
Architecture        current
Live-world E2E      must replay
```

instead of *"main moved — spend an hour proving everything again."*

Specified attestation types: `FactoryTrustRootAttestation`, `ApplicationMutationAttestation`, mutation shards, generic `AuthorityAttestation`, `ReheadAttestation`, `MergeTreeEquivalenceAttestation`, post-merge LIVE_WORLD evidence.

### The gap — *amendment DFE-009* **[OPEN]**

Invalidation is specified thoroughly for **dependency change**: subject bytes move, the toolchain changes, policy changes, a predecessor goes stale. `AUTHORITY_PROGRAM` is correctly in the dependency class list, so the machinery exists.

Nothing specifies what happens when **an authority is found defective after issuing attestations**. If `AuthorityProgram` v3 has a bug, every attestation it ever issued is suspect, including those bound to already-merged work. There is no revocation event, no propagation query, and no policy for merged work proven by a defective authority.

For a system whose entire proposition is trust, this is the most conspicuous absence in the corpus, and it is the scenario most likely to occur in practice, because authorities are software. Three things are needed:

1. An `AttestationRevocation` event type.
2. An authority-defect propagation query: *which merged work depended on attestations from this authority version?*
3. A policy — almost certainly Tier 3 — on whether revocation forces requalification of merged work or records a permanent qualification caveat.

A closely related absence: **nothing specifies what happens when two authorities disagree.** The independence architecture is designed so that they can, and the kernel's decision procedure in that case is unstated.

## I.2 The acceleration directive

- **Proof reuse by dependency identity** — classify claims by *replay scope*.
- A **trust-root qualification attestation**, qualified once per trust-root revision.
- **Proof transfer by exact tree equivalence** across a verified merge: post-merge must not reprove tree-pure claims but *must* replay live-world claims.
- Keep the final mutation claim head-bound.
- **Detector-specific mutations**: a mutation names the detector required to kill it; "some unrelated test happened to fail" does not count. Stronger *and* faster. Deduplicate detector baselines; shard trust-root mutations. *(Partially **[BUILT]** — four injected defects each caught by exactly one detector on its own AssertionError.)*
- Fan out the five model authorities; parallelise read-only test groups; run the blinded application holdout concurrently where safe; add a global compute semaphore.
- **Do not cache independent RED/GREEN on the first fresh validation.**
- Measure inner evidence timing *before* further micro-optimisation; leave cheap duplication alone initially.

## I.3 Orchestrator and concurrency **[SPEC]**

`DARK_FACTORY_2_ORCHESTRATOR_AND_LEASES.md`, commit `357aca5`.

> **The orchestrator may decide what to try next. It must not decide what is proved.**

```
              SERIAL, SHORT-LIVED COORDINATOR
                         │
         ┌───────────────┼────────────────┐
         ▼               ▼                ▼
     executor A       executor B      validator fanout
     lease A/gen4     lease B/gen9    immutable subject
         └───────────────┬────────────────┘
                         ▼
              KERNEL + AUTHORITIES
```

Decisions: resource-scoped leases rather than one global factory lock · atomic lease acquisition · monotonically increasing **generation fencing**, where a stale generation **may compute but may not mutate** · short-lived coordinator, long-running parallel executors · the compiled programme DAG determines the ready frontier · `plan-action` happens **before** expensive runner, browser and database provisioning · event/wake-driven scheduling with cron only as a recovery backstop · separate capacity semaphores for model, provider and runner budgets · crash and stale-owner recovery · four concurrency levels A–D.

**The deterministic failure taxonomy** — small, cheap, and high-value:

```
TRANSIENT_INFRASTRUCTURE
DETERMINISTIC_REFUSAL
MODEL_ATTEMPT_FAILURE
STRATEGY_REJECTED_BY_REALITY
STALE_INPUT
OWNER_DECISION_REQUIRED
PERMANENT_PLATFORM/POLICY
```

The system must **never retry a deterministic refusal as though it were a 504**.

Discipline attached to all concurrency work: compare wall time, cost **and** qualification result before and after, not wall time alone.

## I.4 Cost **[SPEC]**

Hierarchical and first-class: project budget → programme budget → issue budget → exploration budget → probe budget. Cost estimates must themselves be calibrated. Actual threshold values are **[OPEN]**.

---

# PART J — THE DIRECTIVE CORPUS OF 7 SEPTEMBER

On 7 Sep between 12:05 and 18:37, in parallel with Claude's work on #134/#149/#150, roughly **300 numbered decisions** were produced across six documents and committed to `architecture/dark-factory-2-target`. **`main` was not moved and no PR was opened**, deliberately, so the merge canary sequence could continue undisturbed.

The stated purpose: remove design work from the implementer's critical path by pre-deciding the irreversible questions — spec versioning, programme authority, Preflight/factory handoff, failure return paths, concurrency semantics, assumption invalidation, graph history, user steering, cost gating, learning isolation, UI state and TCB boundaries.

Equally deliberate: **individual implementation tickets were not pre-written**, on the grounds that doing so manually would be performing the programme-synthesis job the system is meant to learn.

## J.1 The commits

| Commit | Document |
|---|---|
| `ffa6623` | Capability model |
| `357aca5` | Orchestrator and leases |
| `04eb0b6` | Attestation dependencies |
| `d2eeda7` | Architecture governance |
| `bc133cf` | Project Decision Graph semantics |
| `dcd815f` | Learning and experience |
| `cf49319` | Rebuilt architecture index |
| `56e1497` | `DARK_FACTORY_2_ARCHITECTURE_EVOLUTION_V3_CLAIM_CENTRIC_MODEL.md` |

## J.2 Register 1 — Target architecture directive (49 decisions)

1 five-layer system split · 2 the kernel's one job · 3 formalise a TCB manifest · 4 separate orchestration from authorisation · 5 authorities emit narrow attestations · 6 separate action adapters from trusted observations · 7 capability-based execution · 8 canonical history is append-only · 9 explicit identity and version context on durable objects · 10 specification versioning is authoritative · 11 user input defaults to exploration · 12 the Front Door produces one authoritative specification · 13 programme synthesis is propose → compile · 14 programme state is not product intent · 15 Preflight is a separate trust domain · 16 fidelity ladder · 17 measured facts, predictions and judgements are different types · 18 recommendations require explicit assumptions · 19 reactive impact follows graph edges · 20 rejected alternatives remain durable · 21 user candidates: equal evaluation, asymmetric reporting · 22 user steering does not command implementation · 23 leases + optimistic graph versioning · 24 command → event → materialised state · 25 core graph ontology · 26 the graph connects ideas to code · 27 factory handoff is narrow and non-authoritative · 28 what happens when the chosen strategy fails · 29 cost is hierarchical and first-class · 30 learning has three layers · 31 learning never enters blind judges · 32 prediction must be calibrated · 33 live UI is projection, not authority · 34 the UI supports direct graph steering · 35 implemented architecture is a persistent graph layer · 36 future incidents feed the same graph · 37 the UI event transport is replaceable · 38 storage technology deliberately undecided · 39 Front Door, Preflight and Learning are never in the TCB · 40 exploration may use cheaper models · 41 avoid premature distributed architecture · 42 avoid premature genericisation across domains · 43 migration order · 44 one integration benchmark before the UI · 45 an adversarial benchmark · 46 questions not to reopen casually · 47 decisions left open for implementation evidence · 48 how to use this directive · 49 desired result.

**Decision 42** is the generic-primitive hypothesis, deferred rather than built: `Explore → Gather Evidence → Prune → Escalate Fidelity → Select → Prove → Learn`, to be extracted only after a second domain proves the commonality.

**Decision 43** is a 26-step dependency order: trajectory capture → TCB inventory → state-transition kernel boundary → typed authority/evidence envelopes → orchestrator extraction → adapter extraction → independent observation → capability model → requalification → concurrency/leases/budgets → Front Door → immutable spec versioning → programme synthesiser → DAG compiler → parallel execution → Preflight candidate model → evaluation ladder → Long-Horizon Architect → decision graph → first-class assumptions → safe graph mutation → prediction/calibration → live UI → incident-driven reconsideration → evidence-backed retrieval → Virtual Factory. *(Superseded — see Part K.1.)*

**Decision 44 — the integration benchmark.** The architecture is credible when this runs on real state and evidence:

```
"I want feature X." → Front Door asks necessary questions → user approves SPEC v1
→ synthesiser creates validated DAG → one consequential question produces 3 candidates
→ Preflight evaluates → user adds candidate D → cost shown and auto-approved
→ D evaluated fairly → candidate B selected → B crosses the trust boundary
→ real factory runs → B qualifies and merges → implementation appears in the architecture graph
→ later observation invalidates one of B's assumptions → only the dependent decision reopens
→ candidate D is reconsidered → fresh Preflight selects D → new migration goes through the real factory
```

**Decision 45 — the adversarial benchmark**, which proves the trust boundary is genuine:

```
Preflight confidently chooses A → real architecture authority rejects A → no merge occurs
→ failure evidence returns to Preflight → prediction marked wrong → A eliminated
→ B selected → B independently proves itself
```

## J.3 Register 2 — Canonical contracts (76 decisions)

Covered in Part F. Structure: 1–5 foundations and references · 6–7 approved spec and interview ledger · 8–11 graph command, event, node envelope, edge · 12–17 question, user question, assumption, invalidation, candidate, user direction · 18–24 evidence, prediction, probe, evaluation, recommendation, decision rule, rejection · 25–29 programme, compiled DAG, factory handoff, issue binding, factory run · 30–37 kernel transition request, attestation, independence metadata, capability grant, Lease v2, acquisition result, exploration budget, cost approval · 38–48 trajectory, stage attempt, prediction outcome, strategy failure, implementation component, observation, incident, reconsideration, lesson, lesson packet, external source · 49–57 evolution rules, unknown fields, hashing, time and ordering, deletion, privacy, event registry, status vocabulary, trust class · 58–62 snapshot, UI envelope, frontend actions, worked examples · 63–65 further worked examples · 66–72 where schemas live, codegen, schema tests, invariant tests, ownership, text normalisation, **references are not authority** · 73–76 rollout order, pre-adoption comparison, acceptance benchmark, final principle.

## J.4 Register 3 — Preflight algorithm (81 decisions)

1 core principle · 2 Preflight may never qualify · 3 determine whether exploration is needed · 4 exploration modes · 5 candidate generation · 6 candidate count · 7 always include the baseline · 8 user-proposed candidates · 9 freeze hard constraints before comparison · 10 pre-register the decision policy · 11 no fake universal score · 12 fidelity ladder (F0–F4) · 13 candidate state machine · 14 cheap invalidity screening first · 15 dominance pruning · 16 preserve the Pareto frontier · 17 uncertainty registry · 18 value-of-information rule · 19 probe execution threshold · 20 cost control · 21 calibrated cost estimates · 22 dynamic candidate population · 23 candidate novelty test · 24 breadth vs depth · 25 model diversity · 26 Long-Horizon Architect participation · 27 reversibility as explicit criterion · 28 complexity via observable proxies · 29 expected engineering cost · 30–33 the four stop rules · 34 do not escalate technical uncertainty prematurely · 35 user preference hierarchy · 36 requirement sufficiency · 37 diminishing returns · 38 search exhaustion is not required · 39 recommendation contents · 40 recommendations cannot hide uncertainty · 41 handoff rule · 42–43 implementation-level and strategy-level failure · 44 a failed winner teaches more · 45 reviving rejected candidates · 46 re-evaluation after assumption invalidation · 47 question decomposition · 48 hierarchical exploration · 49 detect coupled choices · 50 candidate graph not flat list · 51 scoped historical retrieval · 52 external research · 53 security-sensitive candidates · 54 migration-sensitive candidates · 55 operational candidates · 56 dependency addition · 57 rejection reason codes · 58 do not prune on model preference · 59 exploration telemetry · 60–63 learn when tournaments are valuable, ideal candidate count, probe value, model routing · 64–65 user-facing presentation and candidate animation states · 66 decision completeness · 67–68 direct-mode record and failed direct-mode detection · 69 avoid architecture astronautics · 70 novel approaches need higher evidence · 71 architecture optionality · 72 default convergence policy · 73 the success criterion · 74–76 minimum viable, V2, V3 · 77 canonical benchmark · 78 anti-benchmark cheating · 79 what remains open · 80 final algorithm · 81 architectural invariant.

## J.5 Register 4 — Front Door algorithm (87 decisions)

1 core mission · 2 separate intent detection from interviewing · 3 unknowns have different owners · 4 the default is not to ask · 5 interpretation divergence · 6 ambiguity must be material · 7 one question at a time · 8 rank by decision value · 9 no fake question scores · 10 ask questions that distinguish behaviours · 11–12 recommendations accompany questions but are not decisions · 13 avoid jargon · 14 preserve original wording · 15–16 delegation and deferral · 17 append-only ledger · 18 provisional intent model · 19 topic coverage map · 20–21 mandatory scenario synthesis, off-nominal scenarios · 22 product behaviour vs implementation · 23 solution masquerading as requirement · 24 optional features default out · 25 existing behaviour is a strong default · 26–27 research before asking, bounded · 28–29 feasibility sanity check, infeasibility as a question only when necessary · 30 requirement quality audit · 31–32 acceptance criteria as behaviour anchors, not design · 33 traceability mandatory · 34 contradiction audit · 35 counterfactual specs for question generation · 36–38 minimal sufficient question, avoid false binaries, don't burden with edge cases · 39–41 security/privacy, performance, scale questioning · 42–43 adaptive depth, learn user burden · 44–45 question-value telemetry and missed questions · 46–47 explicit assumptions, product vs engineering assumptions · 48 identify decision authority · 49–51 draft-spec readiness, zero-question path, final review is not another interrogation · 52–55 explicit approval, validate first, approval ≠ feasibility, surface likely infeasibility · 56–58 no silent mutation, delta amendments, post-approval steering stays exploration · 59 no direct GitHub work · 60 replayable · 61 trust boundary · 62 cost behaviour · 63–69 the seven anti-patterns · 70 questions should expose consequences · 71 conflict resolution format · 72 edge cases may produce a default rather than a question · 73 no fake quantitative precision from users · 74–76 derived technical requirements, scenario-derived requirements, system-derived safeguards · 77–79 interview learning dataset, question usefulness, missed questions · 80–81 benchmark suite and success metrics · 82 the desired behavioural balance · 83–85 minimum viable, V2, V3 · 86 final algorithm · 87 architectural invariant.

## J.6 Register 5 — Repository migration directive (Parts I–XV)

The one directive written against the actual repository rather than the target.

- **Part I** — the most important repo-specific correction: **do not build a second state machine.** The existing evidence spine and manifest remain the source of proof and lifecycle semantics; orchestration may have separate operational states (queued/leased/running).
- **Part II** — what already exists: `canonical.py`, `manifest.py`, `spine.py`, `independence.py`, `evidence_closure.py`, `provenance.py`, `authority.py`, `harness/merge_verify.py`.
- **Part III** — the TCB problem: `runtime.py` is nine systems merged.
- **Part IV** — target package and responsibility map.
- **Part V** — module-by-module migration: `providers.py` · prefer a worker view over prompt-based filesystem trust · `worker_policy.py` · `credential_env.py` · `github_cli.py` · exact-head merge · `git_authority.py` · RED/guard semantics become typed evidence · `worktree.py` · `trusted_programs.py` · `static_gate.py`.
- **Part VI** — re-head becomes its own authority.
- **Part VII** — the claim-driven kernel API; merge is the most privileged transition.
- **Part VIII** — the immediate low-risk speed win: **trajectory retention**, with finalisation on every exit path, and trajectory kept distinct from trusted evidence.
- **Part IX** — concurrency: coordinator, executor, workflow dispatch, Lease v2, persistence, concurrency levels, validation fan-out, compute semaphore.
- **Part X** — the adversarial benchmark families (C.7).
- **Part XI** — decision graph implementation: abstract store contract, event application, materialised tables, the assumption-invalidation query, specification and graph history. **No graph database by default.**
- **Part XII** — live UI architecture: main screen, visible trust, node detail panel, user steering, cost UI, timeline. No rendering before event semantics.
- **Part XIII** — learning implementation: raw store first, offline read-only learner, factory knowledge before software knowledge, structurally enforced blind-judge isolation.
- **Part XIV** — a 16-phase concrete programme order, Phase 0 being *finish present qualification* with the floor rule **no architecture rewrite before this floor**. *(Superseded — see Part K.1.)*
- **Part XV** — end-to-end benchmarks 1–4: fully specified request · genuine product ambiguity · programme · Preflight.

## J.7 Register 6 — The v3 claim-centric reflection

The final pass, and the one that reframes everything above:

- Dark Factory is **claim-centric, not task-centric**.
- The Front Door produces intent, question, assumption and claim graphs rather than tickets.
- The Decision Graph becomes the causal memory spine.
- Preflight becomes a simulation layer before commitment.
- The orchestrator becomes a scheduler, not a decision-maker.
- Evidence becomes claim proof; attestations bind proof to revision and dependencies.
- Learning improves strategy but cannot alter trust boundaries.

Plus the **claim lifecycle** as the missing concept, the twelve-stage runtime walkthrough (Front Door → Decision Graph → programme synthesis → Preflight → handoff → execution → capability-controlled work → evidence → attestation → merge → after merge → learning), five principles, and the revised six-phase implementation ordering.

## J.8 What was deliberately left open in the corpus

PostgreSQL versus another ProjectStore · graph database versus relational tables (relational the likely default) · copy versus overlay/container/mount for `WorkerViewBroker` · event and queue technology · heartbeat and lease TTL values · provider concurrency limits · number of examples needed to approve each lesson class · exact models for architect, build and review roles · UI framework · private-repository migration method · exact candidate ranking algorithm · exact cost threshold defaults · exact Python package layout · event-schema serialisation.

All classified **Tier 0/1**: benchmark and probe them; do not escalate to the owner unless evidence uncovers a real owner trade-off.

---

# PART K — SEQUENCING, DONE, AND SCHEDULE

## K.1 Three orderings, one survivor

The record contains three migration orderings produced within about six hours. In supersession order:

| | |
|---|---|
| **26-step dependency order** (Register 1, §43) | The abstract dependency chain. Still useful as a dependency reference. |
| **16-phase programme** (Register 5, Part XIV) | Repo-anchored with DF2-nnn item IDs and a hard floor: no architecture rewrite until the qualification queue is clear and a single path demonstrably operates at current main. |
| **Six-phase ordering** (Register 6) — **current** | `Phase 0` prove the current factory can observe itself · `Phase 1` create the authority substrate · `Phase 2` remove dangerous ambient authority · `Phase 3` split orchestration · `Phase 4` introduce the intelligence layer · `Phase 5` optimisation. |

The foundation build order inside that: **claim model → attestation model → dependency graph → capability model → leases → brokers.**

**Amendment DFE-002 applied.** Nothing in the corpus itself records this supersession, so an unaided reader of the architecture branch finds three roadmaps and guesses. Each superseded document must carry a header naming its replacement:

```
STATUS: SUPERSEDED by DARK_FACTORY_2_ARCHITECTURE_EVOLUTION_V3 (six-phase ordering).
Retained as a dependency reference only. Do not plan from this document.
```

One rule survives supersession independently and is **not** replaced by the six-phase ordering: *no architecture rewrite before the qualification queue is clear and a single path demonstrably operates at current main.*

## K.2 The strategic instruction that overrides all of it

> **Stop general factory architecture work until the canary either completes or exposes another concrete blocker.**

The architecture has enough machinery. More abstraction now risks optimising something that has not yet completed one real lap.

## K.3 Definition of done for Core 2.0

One ordinary product issue traverses triage → clean-main build → RED acceptance proof → implementation → independent reviews → architecture conformance → PR → trusted-base validation → blinded holdouts → production-shaped browser E2E → automatic exact-head merge → full post-merge harness → automatic issue closure, with **zero manual merge, zero manual test intervention, zero manual PR shepherding**. Then ratchet the observed floors and stop adding architecture for a while.

The moment `E2E_PASSED steps=N` appears on the canonical journey, `e2e_steps: N` goes into the floor with zero slack.

## K.4 Schedule

Stage estimates, which **overlap and must not be summed**: qualification floor 1–2 weeks · trajectory capture and TCB inventory 1–2 weeks · kernel minimisation, authority separation and capability work 3–6 weeks · concurrency and leases 1–3 weeks · Front Door 1–2 weeks · programme synthesis 2–4 weeks · Preflight 2–5 weeks · long-horizon architecture 1–3 weeks · decision graph 2–4 weeks · user steering 1–2 weeks · live UI 2–4 weeks · reconsideration and calibration 3–8 weeks then ongoing.

Milestones: **~6–8 weeks** to a noticeably different factory (grill → approved spec → programme decomposition → autonomous multi-issue execution). **~3–4 months** to a credible integrated version with an early project graph. **~6–9 months** to something worth calling V1. **12+ months** before the learning and data moat matters.

The named risk: **not coding, but qualification failures exposing new classes of trust problem** — and those are not wasted days, they are how the factory becomes trustworthy.

Settled dependency chain:

```
reliable single path → small trustworthy kernel → concurrency
→ front door + programmes → preflight → decision graph → UI → learning
```

## K.5 Documentation as a first-class migration

Because B.2 makes documentation a live input, the v2→v3 transition is planned as a **documentation migration PR**, not incremental edits: update the doc index reading order, update the six target documents, add cross-links so a worker cannot reach the old component descriptions without meeting the new runtime model.

Canonical reading order: target architecture → runtime walkthrough → canonical contracts → capability model → orchestrator and leases → attestation dependencies → Project Decision Graph → learning architecture → qualification acceleration → kernel migration.

## K.6 How to use the directive corpus

1. Compare the target architecture against current repository reality.
2. Identify genuine contradictions.
3. Record the major decisions through the repo's existing decision/ADR mechanism with the next valid IDs.
4. Do not overwrite protected files concurrently.
5. Turn the migration order into a dependency-aware set of bounded programme items.
6. Do not implement later flashy features before prerequisites.
7. For each issue, **reference the relevant locked decisions rather than re-solving them**.
8. Use Dark Factory itself to implement the changes whenever it is capable enough.

The next genuinely high-value architecture task is no longer designing more concepts. It is **compiling this target architecture into a dependency-ordered implementation programme against the actual current repository**, so that once #134 is through there is a concrete sequence of bounded migrations rather than an ad hoc choice of next refactor.

---

# PART L — ADOPTION

## L.1 The corpus is not yet decided

Everything in Parts C through J arrived **outside the governance regime it defines**. Most of it is Tier 2 — trust-boundary and system-contract change — and none of it travelled through an Architecture Change Proposal, because the ACP process is defined inside the same corpus and the proposing agent was also the adjudicating agent.

That is not grounds to discard it. It is grounds not to treat *"it is written on the architecture branch"* as equivalent to *"it was decided properly."*

The formal position: **400 decisions, of which 243 sit at PROPOSED with no evidence behind them.** See `register/DECISION_REGISTER.md`.

## L.2 The adoption path

1. **Verify statuses against the repository.** Every `BUILT` and `PARTIAL` claim here is transcript-derived and stale. Correct them first; a register that overstates what exists violates the honesty rule it enforces.
2. **Apply the three cheap amendments** — DFE-002 supersession headers, DFE-003 lock-list split, DFE-004 schema document split. These change what "decided" means and must land before anyone implements from the corpus.
3. **Run the DFC-074 comparison** — every proposed schema against existing repository artefacts, identifying collisions, redundancy, and formats that already solve the problem.
4. **File the adoption ACP** (`05-ADOPTION-ACP.md`), using step 3 as its evidence section.
5. **Then, and only then**, compile the dependency-ordered programme (K.6).

None of this blocks the canary, and none of it should start before the canary lands.

## L.3 Amendment log

Ten amendments from the 2026-09-08 evaluation. Six are folded into the text above at the point they apply; four are structural or new work.

| ID | Amendment | Where |
|---|---|---|
| DFE-001 | Adopt the corpus via a Tier 2 ACP | L.2, and `05-ADOPTION-ACP.md` |
| DFE-002 | Supersession headers; six-phase ordering declared current | K.1 |
| DFE-003 | Lock list split into constitutional and settled; one item demoted | `01-CONSTITUTION.md` |
| DFE-004 | Canonical contracts split into normative and proposed | F.4 |
| DFE-005 | V1 probe economics replaced with fixed budgets plus logging | E.3 |
| DFE-006 | Interview learning may not modify the topic coverage map | E.1 |
| DFE-007 | Runner-up sampling for Preflight coverage | E.3 |
| DFE-008 | `ReheadAuthority` becomes a dependency query | C.6 |
| DFE-009 | Attestation revocation and authority disagreement | I.1 |
| DFE-010 | Per-issue decision extracts | `register/extract.py` |

## L.4 What is still missing from the corpus

Named honestly, because a reader should know the shape of the hole:

- **Falsifiers.** Most decisions carry no statement of what would prove them wrong. The register has a `falsifier` field; it is mostly empty.
- **Authority revocation and disagreement** (DFE-009). Genuinely new design work.
- **A tested claim substrate.** The claim-centric model (J.7) is a reframing, not an implementation. `06-SPIKE-SHADOW-CLAIMS.md` is the two-day experiment that tells you whether it can be introduced incrementally.
- **Any evidence at all** for 243 of the 400 decisions.

---

# PART M — THE OPTIMISATION ARCHITECTURE

Added 2026-09-08. The corpus has no section on this, and the absence is the finding.

## M.1 The diagnosis

**Dark Factory has a monotone safety architecture and no optimisation architecture.**

Every mechanism in it says *never get worse*: floors that only rise with zero slack, a TCB manifest that only shrinks, immutable approved specs, append-only history, rejected alternatives preserved. Nothing anywhere says *get better on a number*.

This is not a flaw. It is what makes the system trustworthy, and it is the correct order in which to build the two halves. But it means the system currently has **no gradient**. It improves only when a human designs an improvement, and it has no mechanism for making that improvement stick.

## M.2 The ratchet family, completed

| Ratchet | Direction | Guarantees | Status |
|---|---|---|---|
| **Evidence floors** | only rise | never worse at proving | **[BUILT]** |
| **TCB manifest** | only shrinks | never more trusted surface | **[SPEC]** — DFA-003 |
| **Cost ceiling** | only falls | never slower at proving | **[SPEC]** — ACP-003, DFE-012 |

One shape, three directions. A ratchet converts an intention into an invariant, which is why the floors have held for months while intentions elsewhere drifted.

**The cost ceiling must be conditional.** Outcome-identity — same floors met, same detectors firing on the same mutations — is asserted first; the clock is consulted second. Inverted, it becomes a machine for discovering that the fastest qualification is no qualification. The constitution already forbids this in one line: *acceleration cannot silently reduce proof*.

## M.3 When automated search is worth running

The safety conditions for an optimisation loop — measurable metric, fast feedback, code-based solution space, immutable evaluator, clean revert — are necessary and say nothing about whether a loop is *worth* running. The discriminating question is different:

> **Does the search space contain wins you would not find by thinking?**

Search pays where interactions are non-intuitive and human priors are weak. It wastes money wherever a competent engineer can name the top three improvements in five minutes.

This is the value-of-information rule (E.3) at a larger scale. Probe scale: could this result change which candidate is selected? Search scale: does this space contain wins I could not reason to? Same rule, same discriminator — the strength of existing priors, not the size of the prize.

**The factory has already solved the hard safety half** of the loop pattern, and better than the pattern's originators did. Where autoresearch makes its evaluator immutable by convention, the factory does it with trust-root protection, RED-hash binding, detector-specific mutations and an adversarial test (GIT-ADV-2) whose whole content is refusing a worker that edits the acceptance test rather than the code. Clean revert is worktrees plus exact-head merge. What the factory lacks is the cheap, fast metric — its lap is hours, not five minutes.

## M.4 The three candidate loops, ranked by the M.3 test

| | Loop | Metric | Priors | Verdict |
|---|---|---|---|---|
| **1** | Qualification acceleration | wall time, conditional on identical outcome | **Strong, already spent** | Head of the distribution is written down in I.2. **Ratchet it, do not search it.** Search the tail later. |
| **2** | Product performance | whatever real number the feature has | Varies by feature | Only where a feature genuinely has a scalar — latency, bundle size, allocations. Not "code quality," which is not scalar and whose proxies get gamed. |
| **3** | Preflight tuning | decision quality on held-out history | **Weak — the best target** | ACP-002. Needs ~50 completed decisions with recorded outcomes. Month six. |

The intuitive ranking is the reverse of the correct one. The loop with the best target has the worst readiness; the loop with the most obvious payoff has nothing left to discover.

## M.5 The unrecorded tension

**Preflight and the acceleration programme pull against each other.** Preflight exists because building is expensive; the acceleration programme exists to make building cheap. At some cost per lap, running the experiment beats predicting its outcome — which is exactly why autoresearch predicts nothing.

Consequences: the value of Preflight *falls* as acceleration succeeds · cheap laps dissolve the coverage problem, because you can simply build the runner-up · and the crossover point is measurable rather than arguable, from the per-stage timings ACP-003 produces.

Do not resolve this by argument. Instrument, and let the cost per lap decide.

## M.6 Sequence

```
now        instrument: per-stage timing, trajectory on every exit path,
           retention 7 → 90 days
           (every lap run without this is a permanently lost sample)

next       cost ratchet (ACP-003) — ceilings from observed p50 after ten laps

then       land the named accelerations, dependency model first (I.1)
           each one now visible as a ceiling drop, and its erosion as a breach

after      search the tail: shard counts, parallelism, scheduling order,
           against the ratchet, once the head is harvested

month 6+   Preflight tuning (ACP-002), replay to search, shadow to validate
```

Only the first item is urgent, and its urgency has nothing to do with loops. **Retention at seven days is deleting the dataset for all three of these simultaneously**, and that is true whether or not any of them is ever built.
