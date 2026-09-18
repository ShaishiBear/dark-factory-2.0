# Critical Evaluation of the 7 September Architecture Corpus

*An assessment of the ~300 decisions produced in parallel with Claude's implementation work, before they are treated as binding. Numbered so items can be cited individually.*

---

## Summary judgement

The corpus is a genuine acceleration and mostly sound. Its strongest components — the attestation dependency model, the capability naming discipline, the failure taxonomy, the type separation between measurement, prediction and judgement — are better than what most teams reach after a year of operating a system like this. Several were derived from observed cost and failure data rather than from taste.

Its weaknesses are structural rather than local. It arrived outside its own governance process, it locks some things that should stay open, it contains three mutually inconsistent roadmaps with no supersession record, and it over-specifies the parts that cannot be verified while under-specifying the parts that can. None of that argues for discarding it. It argues for adopting it deliberately rather than absorbing it.

---

# PART 1 — WHAT HOLDS

## 1.1 The attestation dependency model is the best decision in the corpus

> Proof is reusable because its dependencies are identical, not because it is recent.

This is content-addressed build systems applied to *proof* rather than artefacts, with the dependency classes separated explicitly (`SUBJECT_BYTES`, `TRUST_ROOT`, `POLICY`, `AUTHORITY_PROGRAM`, `TOOLCHAIN`, `EXTERNAL_STATE`, `PREDECESSOR_ATTESTATION`). It is the only decision in the batch that makes the system materially cheaper **without making it weaker**, and it converts the previously stated tension between speed and rigour into an engineering problem with a known solution shape.

It also earns extra credit for the rule that **re-head is claim-by-claim preservation and reissuance**, not a blanket carry-forward. That is precisely where a cheaper implementation would have quietly broken the trust model.

**Keep, and prioritise. This should be near the front of the implementation queue, not the back.**

## 1.2 Capability naming

Semantic operations (`github.merge.exact-head`, `workspace.import-diff`) rather than `shell.exec` or `git.exec`. And the subtler point, easy to miss and important: **credentials are implementation mechanisms, not capabilities.** Systems that conflate the two end up with "has the token" as the de facto permission model, which is what the GitHub App work has just finished dismantling in practice.

The insistence on *actual filesystem blindness instead of prompting workers not to look* is the same lesson learned once already in B.3 of the architecture document, generalised correctly.

## 1.3 The deterministic failure taxonomy

Seven classes, with the operative rule that the system must never retry a `DETERMINISTIC_REFUSAL` as though it were a 504. Cheap to implement, immediately useful, and it directly addresses observed behaviour — the canary failures were exactly this kind of misclassification, where a deterministic missing-label refusal looked like an execution failure.

`STRATEGY_REJECTED_BY_REALITY` as a distinct class is what wires Preflight's learning loop to the factory's output. Good.

## 1.4 Generation fencing, correctly stated

*A stale generation may compute but may not mutate.* That is the right formulation of the fencing-token pattern — it preserves useful work from a slow worker while making its writes impossible. Many implementations get this wrong by killing the stale worker instead.

## 1.5 Three type distinctions that will each prevent a class of bug

- **Measured facts, predictions and judgements are different types**, with the explicit prohibition on converting judgement into fake precision. `maintainability = 0.84` is a lie with a decimal point in it.
- **References are not authority.** `authority = "green-replay"` does not make a record authoritative; `network = false` does not disable network. Declared metadata versus independently enforced fact.
- **`trust_class` is explanatory, not authorising.** Having just introduced a field that names trust levels, the corpus immediately forbids that field from creating trust. That is the discipline of someone who has watched a metadata field become a permission system.

## 1.6 The escalation predicate

```
two or more candidates satisfy all hard constraints
AND the difference depends on a user-owned priority
AND available technical evidence cannot resolve that priority
```

Three conjuncts, all necessary, all checkable. This is a genuinely well-formed decision rule, which is rare in material of this kind, and it operationalises the covenant ("you decide what, the factory decides how") into something a program can evaluate. The paired instruction — ask a compressed product question, never *"which architecture do you prefer?"* — is the correct output format.

## 1.7 Value-of-information gating, with the right escape valve

*Could the result realistically change which candidate should be selected?* plus **do not ask the user to approve every £0.10 probe already inside an approved budget**. The second clause is what stops the first from turning into a permission-request generator. Both are needed; both are present.

## 1.8 Minimum-viable versions specified for the hard subsystems

Preflight V1 explicitly does not wait for learning models: deterministic trigger, 2–4 candidates, hard-constraint screening, static analysis, qualitative rollouts, manual decision policy, optional probe, Pareto comparison, recommendation. Front Door likewise has a V1/V2/V3 split. This prevents the most common failure of ambitious architectures, where nothing ships until the sophisticated version is ready.

## 1.9 Two acts of restraint worth naming

**Implementation tickets were deliberately not pre-written**, on the stated grounds that doing so manually would be performing the programme-synthesis job the system is supposed to learn. That is unusual discipline: the obvious way to "save time" would have been to emit 200 tickets.

**`main` was not moved and no PR was opened.** Seven commits went to a safe architecture branch with an explicit note that the #150 → canary → #134 sequence could proceed undisturbed. The corpus respected the operational priority it had itself identified.

---

# PART 2 — STRUCTURAL PROBLEMS

## 2.1 The corpus is self-exempting

The governance regime it defines requires Tier 2 changes — trust-boundary and system-contract changes — to travel through an Architecture Change Proposal carrying new evidence, alternatives, TCB effects, proof obligations and a migration path.

Most of these ~300 decisions **are** Tier 2. None went through that process. They could not have: the process is defined inside the same document, and the proposing agent was also the adjudicating agent. The corpus arrives outside the regime it establishes.

That is not a reason to discard it, but it is a reason not to treat "it is written in the architecture branch" as equivalent to "it was decided properly."

**Recommendation.** Make the first Tier 2 ACP be *the adoption of the corpus itself*. Its evidence section is already written: Register 2 §74 requires comparing every proposed schema to existing repository artefacts and identifying collisions before adoption. Do that work, produce the ACP, and the corpus enters the system legitimately. Anything that fails the comparison gets amended rather than silently ignored.

## 2.2 Three roadmaps, no supersession record

Within about six hours the corpus produced a 26-step dependency order, a 16-phase repo-anchored programme with DF2-nnn item IDs, and a six-phase ordering. They do not agree, and each is written as though authoritative.

This is exactly the failure diagnosed as the single largest quality risk to the agents — stale documentation as a live input — recurring inside the new material one day after it was named. An implementing agent reading the architecture branch will find three roadmaps and must guess, and the guess will not be recorded.

**Recommendation.** The doc index must carry supersession metadata, not just reading order. Every superseded document gets a header naming what replaced it and why. This is cheap, and it is the one thing the corpus is structurally guaranteed to need again, because it will be revised.

## 2.3 The lock list mixes evidence levels

Twenty items are listed as not to be reopened casually. Their support varies enormously:

- **Well supported.** "Preflight remains outside the trusted factory" rests on a cost argument the user actually made and won. "TCB size is a first-class metric" rests on a diagnosis of `runtime.py`. "Capability enforcement must be real, not prompt-based" rests on an observed failure.
- **Reasoned but untested.** "Assumptions are first-class", "canonical history is non-destructive". Plausible, coherent, no evidence yet.
- **Asserted only.** "One winning candidate enters the trusted factory at a time."

That last one should not be on the list. It is a resource-allocation policy dressed as an architectural invariant, and it may well be wrong: two candidates with disjoint blast radii are exactly the case the concurrency work exists to exploit, and serialising them is pure cost. Worse, locking it forecloses the empirical answer that the rest of the design insists on — the corpus elsewhere says when tournaments are worth running should be *learned*, not specified.

**Recommendation.** Demote it to the open list, phrased as a default rather than a law: *one candidate at a time by default; concurrent trusted candidates require demonstrated blast-radius disjointness.*

More generally, split the lock list into **constitutional** (a change requires an owner decision) and **settled-for-now** (a change requires evidence, not permission). Twenty undifferentiated locks will either be over-respected or quietly ignored.

## 2.4 The schema corpus risks being the thing it warns against

Register 2 §74 says: *do not create an enormous generic schema package that nothing uses.* Register 2 is a 76-decision specification of roughly 49 schemas, none of which currently has a consumer.

The rollout order (trajectory, attestation, capability, lease first) is correct and should be treated as binding. But documents exert gravity. Forty schemas sitting in a file called *canonical contracts* will be cited as decided, and someone will implement `IncidentSchema` before an incident exists.

**Recommendation.** Split the document. Phase 1 schemas stay in `CANONICAL_CONTRACTS.md` as normative. Phases 2–5 move to `PROPOSED_CONTRACTS.md` with a header stating that nothing in it is decided until it has a consumer. This costs one file move and removes the gravity.

## 2.5 The specification effort is inverted relative to verifiability

The Front Door directive runs to 87 decisions. The overwhelming majority govern **conversational behaviour**: anti-patterns, question ranking, jargon avoidance, burden management, tone of the final review. A handful govern the **artefact**: the approved specification, its versioning, its hash, its traceability to requirements, its immutability.

The conversational rules are the part a competent model already does reasonably well, and — critically — the part that **cannot be deterministically verified**. The artefact rules are the part that can be checked by a program and the part every downstream authority binds to.

The same imbalance appears in Preflight: 81 decisions, most about judgement quality, comparatively few about the handoff record that actually crosses the trust boundary.

**Recommendation.** For each of the two directives, extract the subset that produces a checkable artefact and promote it into the canonical contracts as enforced schema. Demote the rest to guidance in `.factory/methods/`, where the method layer already lives and where non-verifiable instruction belongs. The distinction matters because guidance that sits in an architecture document acquires the appearance of an invariant.

## 2.6 Front Door learning has no falsifier

Rules 43–45 and 77–79 want the interviewer to learn user burden, question usefulness and missed questions. Rule 31 protects blind judges from learned lessons. But the Front Door's output **is the approved specification**, which is the object every downstream judge measures against.

A Front Door that has learned *"users approve faster when I don't ask about X"* is optimising against **approval**, not against **correctness**. Approval comes from a human, not an authority, and there is no falsifier for that drift anywhere in the corpus. The self-improvement guard covers policy proposals about the factory's own mechanisms; it does not cover the intake layer learning to shape intent.

This is the most serious *unnoticed* gap in the corpus, because it is the one place where learning can degrade trust without touching a single trust boundary.

**Recommendation.** An explicit rule: interview learning may optimise **question ordering, phrasing and burden**. It may never modify **the topic-coverage map** — the set of topics that must be covered before a spec is approvable. Coverage is policy; changing it is Tier 2, with an ACP. And measure the right thing: the metric should be *defects and reconsiderations later traced to an unasked question*, not user satisfaction with the interview.

## 2.7 "Preflight can hallucinate, the factory cannot" is slightly false as stated

The factory cannot be *deceived* by Preflight, because Preflight's verdict is UNPROVEN and every claim is independently re-established. That part is sound.

But Preflight's output includes the technical direction and the framing of the hard-constraint set that the factory then builds against, and it determines which candidates are never considered at all. A systematically biased Preflight does not produce false proofs. It produces **true proofs of the wrong thing**.

The corpus half-sees this. Decision 45's adversarial benchmark proves the factory can reject a confidently-chosen candidate, and "a failed winner teaches more than an ordinary failure" is the right instinct. But both address *Preflight picking a loser*. Neither addresses *Preflight never generating the winner*. Nothing in the 81 decisions measures **candidate-space coverage**, and the candidate-count and tournament-value rules are all deferred to learning that has no data yet.

**Recommendation.** Add a coverage instrument. Periodically — on a sampled basis, budget permitting — build the runner-up as well as the winner, and score the prediction against both. This is the only way to learn whether the generated candidate set contains the good answer, as opposed to whether the ranking within a bad set was correct. Without it, Preflight's calibration data is conditioned on its own generation bias and will look excellent while being systematically narrow.

## 2.8 Cost architecture is structure without values

Five budget scopes, a probe autonomy limit, a value-of-information rule requiring `probability the result changes the decision × value of the improved decision` weighed against cost — and **no numbers anywhere**, with thresholds explicitly deferred.

The deferral is defensible in principle. In practice it means the V1 value-of-information rule reduces to a model's unverifiable guess about whether a probe is worth running, expressed as an economic calculation. That is precisely the fake precision the corpus forbids in decision 17.

**Recommendation.** Make V1 crude and explicit rather than economically shaped: a fixed probe budget per consequential question, a hard cap per issue, escalation above the cap, and log every probe with its cost and whether it changed the decision. After a hundred probes the ratio is measurable and the economic rule can be fitted to data. Shipping the sophisticated rule first produces numbers nobody can check.

## 2.9 Re-head is where the TCB quietly regrows

The split is clean on the surface: the orchestrator decides whether attempting a re-head is worthwhile; the `ReheadAuthority` decides whether evidence was genuinely preserved.

But re-head is simultaneously defined as **claim-by-claim preservation and reissuance**. To do that, the `ReheadAuthority` must understand the semantics of every claim type — what makes a GREEN claim preservable across a head move, what makes a live-world claim unpreservable, what a mutation shard depends on. That is domain expertise of exactly the kind the constitution says must not accumulate in a judge.

The expertise has moved out of `runtime.py`. It has not shrunk. And because `ReheadAuthority` sits in the trust path, it is inside the TCB by construction.

**Recommendation.** Make re-head a *derived* authority rather than a knowing one: it should not itself understand claim types, it should consult the attestation dependency model (1.1) and preserve exactly those claims whose dependency digests are unchanged. Re-head then becomes a query over the dependency graph rather than a body of claim-specific knowledge, and the TCB addition is a few dozen lines instead of a growing catalogue. The two designs are already adjacent in the corpus; they should be joined.

## 2.10 There is no revocation story

Attestation invalidation is specified thoroughly for **dependency change**: subject bytes move, the toolchain changes, policy changes, a predecessor goes stale. `AUTHORITY_PROGRAM` is correctly in the dependency class list.

But nothing specifies what happens when **an authority is found to be defective after issuing attestations**. If `AuthorityProgram` v3 has a bug, every attestation it ever issued is suspect — including those already bound to merged work. There is no revocation event type, no policy for already-merged work proven by a defective authority, no notion of a compromised attestation, and no way to express *"this proof was valid under the rules as we understood them and is now void."*

For a system whose entire proposition is trust, this is the most conspicuous absence in the corpus. It is also the scenario most likely to occur in practice: authorities are software and software has bugs.

**Recommendation.** Add three things to the canonical contracts: an `AttestationRevocation` event; an authority-defect propagation query (which merged work depended on attestations from this authority version?); and a policy decision — almost certainly owner-level, therefore Tier 3 — about whether revocation forces requalification of merged work or records a permanent qualification caveat.

A closely related gap: **nothing specifies what happens when two authorities disagree.** The independence architecture is designed so that they can, and yet the kernel's decision procedure in that case is unstated.

## 2.11 The corpus is itself a context-window problem

The stated purpose was to remove design work from the implementer's critical path. It plausibly does for schemas, capability naming and failure taxonomy.

But 300 decisions delivered as a wall of prose is also a comprehension and context problem for the implementing agent — which is the first item on the origin thread's list of 1.0 failure modes: **context-window decay**. An agent that must read six architecture documents before touching an issue is an agent whose effective context for the actual work has shrunk.

The corpus already contains the answer to this and does not apply it to itself. The **ExperiencePacket** design says: do not load 130,000 tokens of transcript; produce a structured compact packet of 3–5 relevant items, with the raw material fetched only when necessary.

**Recommendation.** Every bounded issue derived from the corpus should carry a per-issue extract — the specific locked decisions it must honour and nothing else — generated from the registers rather than referenced wholesale. This is directly aligned with directive item 48.7, which says to *reference the relevant locked decisions rather than re-solving them*. The register structure in `02-ARCHITECTURE-AND-METHODS.md` Part J exists to make this extraction mechanical.

## 2.12 A note on retro-fitting

The corpus observes that the GitHub App work Claude was implementing at the time is "the first real piece of the long-term capability architecture." That is true and it is a pleasing convergence.

It is also worth naming as a mild epistemic hazard. Retro-fitting existing work into a new framework makes the framework look more validated than it is: the App change was designed to solve an identity problem, not to instantiate a capability model, and its fit is partly coincidence. One convergence is not evidence that the capability model is right. It is evidence that the capability model is *not contradicted* by the one adjacent thing already built.

---

# PART 3 — WHAT TO DO WITH THIS

Ordered by cost-to-value, and none of it blocks the canary.

| | Action | Cost |
|---|---|---|
| **1** | Add supersession headers to the three roadmaps; declare the six-phase ordering current (2.2) | Minutes |
| **2** | Split canonical contracts into normative Phase 1 and proposed Phases 2–5 (2.4) | Minutes |
| **3** | Demote "one candidate at a time" from locked to default; split the lock list into constitutional and settled-for-now (2.3) | Minutes |
| **4** | Add the Front Door coverage-map rule and change its learning metric (2.6) | An hour |
| **5** | Replace the V1 value-of-information rule with fixed budgets plus logging (2.8) | An hour |
| **6** | Write the adoption ACP, using §74's comparison as its evidence section (2.1) | Half a day |
| **7** | Redefine `ReheadAuthority` as a query over the dependency model (2.9) | Design change, cheap now, expensive later |
| **8** | Specify attestation revocation and authority-disagreement resolution (2.10) | Design work, genuinely new |
| **9** | Add runner-up sampling to Preflight's calibration design (2.7) | Design work, cheap to specify, real compute cost to run |
| **10** | Generate per-issue decision extracts rather than wholesale references (2.11) | Tooling, once |

Items 1–3 should happen before anyone implements from the corpus, because they change what "decided" means. Items 7, 8 and 10 are the ones that get more expensive the longer they wait.

---

## A closing observation

The most impressive thing in the corpus is not any individual decision. It is that a system was specified in which **the specifying agent's own output has no authority**. Every one of these 300 decisions is, by the corpus's own rules, a proposal that must survive contact with an independent authority before it means anything.

The corpus should be held to that standard. The right posture is not "these are the architecture decisions." It is: *these are 300 well-reasoned claims, currently at status PROPOSED, awaiting the evidence that promotes or rejects them.*
