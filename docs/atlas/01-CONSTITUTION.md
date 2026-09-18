# Dark Factory — Constitution

*The core ideology. Nothing here is provisional, and nothing here is a mechanism. Mechanisms are in `02-ARCHITECTURE-AND-METHODS.md`.*

---

## The founding reframe

Dark Factory 1.0 asks: **did the agent probably build this correctly?**

Dark Factory 2.0 asks: **what independently verifiable evidence proves this change satisfies its contract without violating anything else?**

Every mechanism in the system exists to make the second question answerable. Every mechanism that does not serve it is decoration.

---

## What the system actually is

**It is not an agent system. It is a proof-carrying decision system.**

Agents are replaceable workers. The persistent object is the chain:

```
Intent → Requirements → Decisions → Claims → Evidence → Authorised effects
```

A model saying "this looks good" has near-zero architectural significance. A model emitting *"Candidate C implements Requirement R under Constraint Set X with predicted properties P"* creates something that can enter the system and be adjudicated.

Consequences:

- **The factory builds decisions, not tasks.**
- **The system remembers why, not just what.**
- **Evidence follows claims.** A claim without an attestation bound to it is an opinion.
- **The system improves by learning from trajectories, not by rewriting itself.**

---

## The five root positions

### 1. Intelligence is not authority

Front Door, planners, Preflight, learning models and the UI may become arbitrarily sophisticated without entering the trusted computing base. A bug in the interviewer, the programme planner, the candidate generator or the virtual factory must be incapable of producing an unearned merge.

### 2. The judge must not be an expert

The kernel should know almost nothing about software. It answers one question:

> Given the current trusted state and independently verifiable evidence, is this exact requested transition authorised?

Domain knowledge belongs in Authorities that emit narrow attestations. A kernel that understands testing, or GitHub, or architecture, is a god object waiting to happen.

### 3. Exploration and commitment are separated by authority, not by intelligence

**Preflight can hallucinate. The Factory cannot.** Not because Preflight is dumber, but because it has lower authority. Exploration is cheap and reversible; commitment is expensive and protected.

### 4. The orchestrator does not think

It optimises movement. Aggressively stupid, highly efficient. This follows from (2): once authorisation lives elsewhere, scheduling has nothing left to decide. The orchestrator may decide **what to try next**; it must never decide **what is proved**.

### 5. The LLM proposes; reality judges

Every domain differs only in its evidence authorities. Software: tests and production telemetry. Physical engineering: solvers and measurements. This is the hypothesis on which the second domain rests. It is asserted rather than argued, and should be treated as a bet rather than a law.

---

## The claim lifecycle

The system manages claim confidence over time. It does not manage tasks.

```
UNKNOWN → PROPOSED → SUPPORTED → PROVEN → CURRENT → STALE → REJECTED / SUPERSEDED
```

Two corollaries do most of the practical work:

- **Proof is reusable because its dependencies are identical, not because it is recent.**
- **Learning may improve what the factory tries. It must not decide what the factory proves.**

---

## The invariants

Non-negotiable. A change to any of these is a constitutional change.

| Domain | Invariant |
|---|---|
| **Trust** | Predictions are not proof. Simulation is not proof unless trusted policy says so. Preflight cannot qualify. Learning cannot certify itself. **The implementation cannot judge itself.** |
| **Independence** | Blinded reviewers stay blind. Lessons never leak into blind judges. Protected-from-writing is not hidden-from-reading. |
| **Provenance** | Trusted claims stay bound to exact code, revision and evidence. **References are not authority** — declared metadata is not an independently enforced fact. |
| **Reversibility** | Rejected alternatives and architectural decisions remain inspectable and revivable. |
| **Cost** | Cheap models and static analysis for exploration; expensive trusted execution only for candidates worth proving. |
| **User authority** | The user may decide technical implementation but **may not silently change approved product intent or hard constraints**. Conversely, the human must always retain a compliant lane to maintain the judge. |
| **Honesty** | Never display *passed / simulated / verified / proven / qualified* unless the real authority produced that result. |
| **Diagnosis** | Never attribute a failure to an authority that did not produce it. An unexplained failure must say so. Failure reporting is held to the same standard as success reporting, and by the same rule: only the authority that actually spoke may be named. |
| **Acceleration** | Making proof cheaper is legitimate. Making it weaker is a constitutional change. |

### The four-state honesty vocabulary

> **Predicted · Probed · Simulated · Proven**

Never fake theatre. The UI is a projection of canonical state, never an authority, and it cannot invent progress or promote an exploratory choice into an approved requirement.

### The asymmetry this corrects

Every mechanism in this system is built to prove success honestly. Almost nothing is built to explain failure honestly. Evidence closure, independence, exact-head binding, mutation, the floors and the ratchets all exist to stop a false *pass*. Not one of them looks at what the system says when it stops.

That asymmetry has a measured cost. On 7 September a merge was refused, correctly, and the refusal named an authority that had already succeeded in the same run. Both working mechanisms — the detection that stopped the merge and the instrumentation that recorded the real cause — did their jobs. The human still had the wrong answer for four days, and a downstream issue collected four repeat reports built on it.

**Detection and diagnosis are different capabilities, and this system has only ever invested in the first.** A correct refusal that misidentifies its own cause is not a partial success; for everyone downstream it is a false statement made by a trusted component, and it propagates exactly as a false *pass* would.

Three consequences, which generalise past any particular defect:

- **Attribution requires evidence, like everything else.** Naming an authority in a failure is a claim about what happened. Position in a sequence, a stage variable, a filename or a code path's location is not evidence that an authority ran and spoke. A component that cannot show which authority produced a failure must report the failure unattributed.
- **`unknown` is an honest answer and must stay available.** The pressure on any classifier is to always return something specific. A system that cannot say *I do not know what refused this* will invent an attribution, and the invented one will be confident.
- **The cost of a wrong diagnosis is paid by whoever reads it next**, and it compounds while nobody re-derives it. This is the same reasoning that makes stale documentation a correctness defect rather than hygiene debt: both are false inputs to a reader who has no reason to doubt them.

The next instance of this will not be the mechanism that caused the last one. The invariant is therefore about what may be *asserted* in a failure, not about any particular classifier.

### The type distinction beneath it

**Measured facts, predictions and judgements are different types.** Never collapse them. Do not convert judgement into fake precision: `maintainability = 0.84` is a lie with a decimal point in it.

---

## How the system is allowed to change itself

```
current trusted authority
        ↓
judges the proposed new trusted authority as data
        ↓
the new authority cannot declare itself valid
```

with the three-role split:

```
Architect predicts → Governor enforces → Holdout challenges
```

For major trust migrations the default is **shadow and equivalence first, cutover second**.

### The constitutional clauses

Intelligence can grow without trust growing · candidate code does not supply its own judge · independent judges stay independent · stale or missing evidence never authorises · exact-head merge remains · capabilities are genuinely enforced · no duplicate lifecycle state machine · acceleration cannot silently reduce proof · approved user intent cannot be silently rewritten · blind judges do not receive learned experience · TCB expansion must be justified.

### The change tiers

```
Tier 0 — ordinary implementation
Tier 1 — bounded architecture
Tier 2 — trust-boundary / system-contract change
Tier 3 — owner / product / constitutional change
```

Tier 2 requires an **Architecture Change Proposal** carrying new evidence, alternatives, TCB effects, proof obligations and a migration path.

This is where escalation gets its teeth. Never ask the owner *"PostgreSQL or SQLite?"* Always ask *"we can save 40% cost by removing this independent proof obligation; do you accept the weaker guarantee?"*

---

## The self-restraint principle

Stated in the origin thread and honoured since:

> I do not want this turning into Kubernetes + Kafka + twelve microservices just because we can.

Corollaries that have earned their place: no graph database merely because the thing is a graph · no microservices merely because the architecture has components · no second lifecycle state machine · no generic schema package that nothing uses · no premature genericisation into an "everything factory" · no architecture astronautics in Preflight.

---

## The ratchet family

The system improves by making improvement **irreversible**, not by optimising. Three ratchets, one shape:

| Ratchet | Direction | Says |
|---|---|---|
| **Evidence floors** | only rise | never get worse at proving |
| **TCB manifest** | only shrinks | never get more trusted surface |
| **Cost ceiling** | only falls | never get slower at proving |

A ratchet converts an intention into an invariant. That is the whole mechanism, and it is why the floors have held for months while good intentions elsewhere drifted.

**The ratchets are the safety architecture. Search is optional.** An automated optimisation loop is a way of finding moves *within* the space a ratchet already bounds — never a substitute for the bound. Add the ratchet first; decide about the loop afterwards, and only where §"When search is worth running" says it pays.

**A cost ceiling is only safe when it is conditional.** The fastest qualification is no qualification. Time may only be compared between runs whose qualification outcome is identical — same floors met, same detectors firing on the same mutations. Outcome-identity is asserted first; the clock is consulted second. This is *acceleration cannot silently reduce proof*, made operational.

---

## When search is worth running

One rule, at three scales. It governs probes, tournaments, and any automated optimisation loop.

> **Will the evidence change what I do, by more than it costs to get?**

- **Probe scale.** Could this result realistically change which candidate is selected? If not, do not run it.
- **Tournament scale.** Are there candidates here whose ranking I cannot predict? If the answer is obvious, decide and move on.
- **Search scale.** Does this space contain wins I would not find by thinking? Where a competent engineer can name the top three improvements in five minutes, a search that rediscovers them is expensive theatre.

The discriminator at every scale is the **strength of existing priors**, not the size of the prize. Search pays where interactions are non-intuitive and human intuition is weak. It wastes money where the head of the distribution is already known — and the honest first question is always *have I already written down the answer?*

---

## Decisions not to be reopened casually

The original corpus listed twenty of these undifferentiated. They are split here (amendment DFE-003) because a list that mixes "requires an owner decision" with "requires evidence" will be either over-respected or quietly ignored.

### Constitutional — reopening requires an owner decision

1. The kernel decides permission, not product strategy.
2. Preflight remains outside the trusted factory.
3. Predictions cannot qualify.
4. Approved specification changes are versioned and explicitly approved.
5. User additions default to exploration, not scope mutation.
6. Learned lessons stay blind from independent judges.
7. The UI derives from the canonical graph and events; it is never authority.
8. Future intelligence grows primarily outside the trusted kernel.
9. Exact-head merge.

### Settled — reopening requires evidence, not permission

10. Assumptions are first-class objects.
11. Reactive invalidation follows recorded edges, not model guesswork.
12. Losing candidates are preserved.
13. User candidates are evaluated equally; rejection is explicitly explained.
14. Exploration has explicit cost.
15. Canonical project history is non-destructive.
16. Concurrency precedes collaborative graph mutation.
17. Programme synthesis proposes; a deterministic compiler authorises.
18. TCB size is a first-class architecture metric.
19. Capability enforcement must be real, not prompt-based.
20. Major adapters fail safely outside the TCB.
21. GitHub App rather than PAT for autonomous identity.

### Demoted to a default, not a law

The original list included *"one winning candidate enters the trusted factory at a time."* That is a resource-allocation policy wearing an invariant's clothes, and it forecloses the empirical answer the rest of the design demands. Restated:

> One candidate at a time **by default**. Concurrent trusted candidates are permitted where blast-radius disjointness has been demonstrated.

Whether concurrency here is ever worth it is a question for evidence, not for this list.

---

## What the user should experience

```
"I want to build/change this."
        ↓
Dark Factory understands the intent.
        ↓
It maps the problem into features and questions.
        ↓
It explores several ways to solve consequential questions.
        ↓
I can add questions or alternative directions.
        ↓
It cheaply determines the most promising strategy.
        ↓
I can see the reasoning and the cost.
        ↓
The winner visibly enters the Dark Factory.
        ↓
The trusted factory proves it.
        ↓
Successful work becomes part of the living architecture.
        ↓
The system learns what happened.
        ↓
If reality changes later, it knows which old assumptions
and alternatives should be reconsidered.
```

Underneath that experience sits:

> **a small, deterministic, paranoid kernel that refuses to confuse intelligence with authority.**

---

## The operating covenant

**You decide what. The factory decides how. It only comes back to you when "what" cannot be safely inferred.**

Escalate only for a genuine product-owner decision, a hard-constraint conflict, a consequential external action, or a choice the evidence cannot responsibly resolve. **Do not ask the human to choose technical implementation details you can investigate or test yourself.** User escalation is for product ownership, not engineering ignorance.
