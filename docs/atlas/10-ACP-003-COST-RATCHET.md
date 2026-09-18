# ACP-003 — The Conditional Cost Ratchet

**Tier:** 1 (bounded architecture — a new ratchet over an existing mechanism, no trust boundary changes)
**Status:** DRAFT
**Register:** proposed as `DFE-012`
**Depends on:** per-stage timing instrumentation · DFM-026 (retention 7→90)
**Relates to:** the whole of Part I, which is an optimisation programme with no optimiser
**Raised:** 2026-09-08

---

## 1. Summary

Add a third ratchet. Observed qualification wall time becomes a **ceiling**, monotonically falling, with zero slack — the exact mirror of the evidence floors.

```
evidence floors    only rise      never get worse at proving
TCB manifest       only shrinks   never get more trusted surface
cost ceiling       only falls     never get slower at proving
```

This is not an optimisation loop. It is the mechanism that makes optimisation stick, and it is available now with machinery that already exists.

---

## 2. Why a ratchet rather than a loop

The obvious response to slow qualification is an automated search: let an agent tune sharding, parallelism and caching against a wall-time metric, keep what improves, revert what doesn't.

That is the wrong first move here, for a reason the constitution now states as a rule: **search pays where priors are weak.**

Priors here are strong and already spent. The acceleration directive names the wins: proof reuse by dependency identity · trust-root qualification attestation · proof transfer by exact tree equivalence · detector-specific mutations · deduplicated detector baselines · sharded trust-root mutations · five-authority fan-out · parallel read-only test groups · concurrent blinded holdout · a global compute semaphore.

That is ten optimisations found by thinking, and they are the head of the distribution. **A search cannot find what has already been written down.** It would harvest the tail — shard counts, parallelism degrees, scheduling order, cache boundaries — and the tail is not worth searching until the head has landed.

What the named accelerations lack is not discovery. It is **enforcement**: nothing prevents the next feature from giving the gains back. That is what a ratchet is for.

---

## 3. Why this is safe when a naive cost loop is not

Every acceleration mechanism has the same failure mode: **the fastest qualification is no qualification.** A system rewarded for speed will discover that deleting a detector is the shortest path.

The factory has already solved this, and better than any loop design could. Outcome-identity is enforced by the floors, the detector set and the mutation catalogue — all trust-root protected, all adversarially tested, none of them reachable by a worker. GIT-ADV-2 exists precisely to refuse an agent that edits the acceptance test rather than the code.

So the hard problem is already done. This proposal adds a second dial to a mechanism whose safety property is established:

```
1. assert qualification outcome is identical
     same floors met · same detectors firing on the same mutations
     if not identical → the comparison is invalid, not merely worse

2. only then compare the clock
```

**Never invert those steps.** A cost ceiling evaluated before outcome-identity is a specification-gaming machine.

---

## 4. What is measured

Per-stage, not aggregate. An aggregate number hides which stage regressed and makes the ceiling unactionable.

```
build              context → design → RED → implement → GREEN
validation         static · unit · integration · E2E
independent        five model authorities, individually
mutation           application shards · trust-root shards
post-merge         harness · live-world replay
end-to-end         issue accepted → issue closed
```

Record wall time and, where meaningful, provider spend. Both go in the trajectory, not in trusted evidence — they are measurements about a run, never claims about a subject.

### Ceilings are per-stage and conditional on scope

A ceiling on total time punishes a legitimately larger change. Normalise where a sane denominator exists — mutation time per shard, authority time per authority, validation time per test group — and leave end-to-end as an observed series rather than a hard gate until enough laps exist to know its variance.

**Do not set a ceiling on a stage you have not measured for at least ten laps.** A ceiling derived from one observation is noise given the authority of an invariant.

---

## 5. How the ratchet behaves

Identical in shape to the floors, which is the point — no new concept, no new failure mode.

| | |
|---|---|
| **Set** | after ten laps, ceiling = observed p50 for that stage. Not the best-ever, which is noise; not the mean, which drifts on outliers. |
| **Tighten** | when a stage's p50 falls and holds for five laps, the ceiling follows. Zero slack. |
| **Breach** | a run exceeding a ceiling fails, the same way a floor breach fails. |
| **Raise** | possible, but requires the same justification a floor reduction would: an explicit, recorded reason. A ceiling that can be raised silently is not a ratchet. |

The floors already work this way and have held for months. Reuse the mechanism rather than inventing a parallel one — a second ratchet implementation would be a second state machine by another name.

---

## 6. What this produces beyond enforcement

**The measurement that everything else needs.** The acceleration directive says to measure inner evidence timing *before* further micro-optimisation. That measurement does not exist. The ratchet produces it as a by-product of enforcing it, which is the cheapest possible way to acquire it.

**The dataset for a later loop.** If a tail search is ever worth running (§8), it needs per-stage timings across many laps. Those come from here.

**A regression detector for the named accelerations.** When proof reuse by dependency identity lands, its benefit is visible as a ceiling drop, and its later erosion is visible as a breach. Without this, an accelerating change and its silent reversal look identical.

---

## 7. Preconditions

**Per-stage timing instrumentation.** Currently absent, or at best partial. This is the actual work in the proposal; the ratchet itself is a few lines against an existing mechanism.

**DFM-026, retention 7 → 90 days.** Ten laps of history at seven-day retention is a coin flip. This proposal raises DFM-026 from a cheap chore to a blocking precondition, and it is now the precondition for three separate capabilities.

**Ten laps per stage before any ceiling is set.** Stated as a rule so it is not quietly skipped when the first numbers look tempting.

---

## 8. What comes after, and only after

Once the head of the distribution has landed and the ceiling is enforcing it, a tail search becomes defensible: shard counts, parallelism degrees, scheduling order, cache boundaries — the parameters where nobody has a strong prior and interactions are genuinely non-intuitive.

At that point the loop is safe by construction, because **the ratchet is already the evaluator**. A variant that breaches a floor or fails outcome-identity is rejected before its time is even considered, and the search cannot reach the mechanisms that judge it.

That is the correct sequence, and it inverts the intuitive one:

```
instrument → ratchet → land the known wins → then search the tail
```

not

```
build a loop → hope it finds what you already knew
```

---

## 9. Acceptance criteria

1. Per-stage timings appear in the trajectory for every meaningful run, on every exit path including failures.
2. Outcome-identity is asserted before any time comparison, and a test proves that a run with a weakened detector set is rejected as **invalid** rather than recorded as fast.
3. A deliberately slowed stage breaches its ceiling and fails the run.
4. A ceiling cannot be raised without a recorded justification — proven by test, not convention.
5. Ceilings are absent for any stage with fewer than ten recorded laps, and their absence is explicit rather than defaulted to zero. *(This mirrors the existing treatment of `e2e_steps`, which correctly records no number rather than inventing one.)*

Criterion 2 is the load-bearing one. Build it first.

---

## 10. Consequences

**If approved:** the ratchet family is complete. The named accelerations become enforceable rather than merely intended. The measurement precondition for every later optimisation decision is acquired as a side effect. No trust boundary moves.

**If rejected:** acceleration remains a set of good intentions in a directive, and each one can be silently given back by the next feature. That is the pattern the floors were invented to prevent, and there is no reason it applies less to cost than to coverage.

**Note.** This is the cheapest high-value item currently on the table and the only one that could land this month. It requires no agent, no metric design, no historical corpus, and no new concept — only instrumentation and one more application of a mechanism that has already proven itself.
