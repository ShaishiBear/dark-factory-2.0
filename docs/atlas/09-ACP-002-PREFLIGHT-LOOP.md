# ACP-002 — Preflight Self-Optimisation Loop

**Tier:** 1 (bounded architecture — Preflight sits outside the TCB and authorises nothing)
**Status:** DRAFT
**Register:** proposed as `DFE-011`
**Amends:** DFP-060, DFP-061, DFP-062, DFP-063 — from *"learn this somehow"* to a specified mechanism
**Depends on:** DFE-007 (runner-up sampling) · DFM-026 (retention 7→90 days) · a corpus of completed decisions
**Priority:** third of three optimisation proposals. See ACP-003 §8 for the sequence.
**Raised:** 2026-09-08

---

## 0. Priority and honest position

This is the **weakest** of the three optimisation proposals on the table, and it is listed third deliberately.

| | Loop | Priors | When |
|---|---|---|---|
| 1 | Cost ratchet (ACP-003) | n/a — not a search | Now |
| 2 | Product performance loops | Only where the feature has a real number | When such a feature exists |
| 3 | **Preflight tuning (this)** | **Weak — the best target** | **Month six, at the earliest** |

It has the *best* target by the constitution's search-value rule: nobody has strong priors about ideal candidate count or when a probe pays for itself, which is exactly why the corpus marked those four decisions to be learned. It has the *worst* readiness: the corpus it needs does not exist and will not for months.

Both facts should be held at once. Do not build this early because the idea is good.

---

## 1. Summary

Apply the autoresearch pattern — fixed budget, single metric, keep-or-revert ratchet — to **the Preflight algorithm itself**, not to the candidates it evaluates.

Four decisions in the Preflight directive are marked OPEN with the instruction that they should be *learned rather than specified*: when tournaments are worth running, ideal candidate count, probe value, and reasoning-model routing. The corpus says these must be learned and nowhere says how. This is how.

**The loop optimises the explorer, never the exploration.**

---

## 2. Why this is Tier 1

An optimisation loop is dangerous almost everywhere in this architecture, because a hill-climber pointed at a trust boundary discovers that deleting proof is the shortest path to a better score. The constitution names this directly: *acceleration cannot silently reduce proof*.

Preflight is the one component where it cannot happen:

- Its verdict is permanently UNPROVEN (DFP-002). Nothing it produces can qualify anything.
- It sits outside the TCB by constitutional rule (DFA-039).
- Its output is a recommendation crossing a narrow, non-authoritative handoff (DFA-027).
- **The factory is an external referee.** If the loop makes Preflight worse, the real factory rejects its candidates and the failure is recorded.

That last point is the crux. DFP-073 already states the success criterion in a form that is a validation score with an independent judge attached: *Preflight is successful if the factory rarely disproves it — but still can.*

The architecture already contains the referee. This proposal only adds the loop.

---

## 3. The mapping

| autoresearch | here |
|---|---|
| `prepare.py` — immutable data prep and metric | Frozen historical decisions with their real factory outcomes, plus the scoring function over them |
| `train.py` — the agent's sandbox | The Preflight algorithm: generation, count, pruning thresholds, dominance rules, probe thresholds, stop rules, model routing |
| `program.md` — human intent and constraints | The Preflight directive (DFP-001..081), the constitution, and this ACP |
| Five-minute wall-clock budget | Fixed **exploration budget** per replayed decision |
| `val_bpb`, lower is better | Decision quality on held-out decisions at fixed budget (§5) |
| Keep or `git revert` | Keep or discard the algorithm variant |

Karpathy's central trick is fixing the budget and optimising the metric, which makes runs comparable regardless of what the agent changed. The translation is exact: **fix the exploration budget, optimise decision quality.**

---

## 4. The frozen/mutable boundary

This is the whole safety design. Get it wrong and the loop optimises the scoreboard.

### Frozen — trust-root protected, agent cannot touch

- The historical decision corpus and every recorded factory outcome.
- The scoring function (§5) and its weights.
- The held-out split boundary (§6).
- Hard-constraint screening (DFP-009): constraints are frozen before comparison, and a variant that relaxes them is invalid, not better.
- The prohibition on a universal score (DFP-011) — the metric below is an *evaluation* measure computed after the fact, never a ranking signal handed to Preflight at decision time.
- Everything in the constitution.

### Mutable — the agent's sandbox

Candidate count and generation strategy · pruning aggressiveness and dominance thresholds · probe budget allocation · stop-rule thresholds · fidelity-ladder escalation policy · model routing and diversity policy · question decomposition strategy · retrieval scope for historical priors.

### The rule

> If a variant changes what counts as a good decision, it is invalid. It may only change how Preflight arrives at one.

Enforce this the way the factory already enforces it elsewhere: the scoring code and corpus live under trust-root protection, and the replay harness asserts their digests before and after every run. This is `prepare.py` immutability implemented properly rather than by convention.

---

## 5. The metric

Not a single scalar handed to a ranker, but a composite evaluation score computed after the outcome is known:

```
For each held-out decision, at fixed exploration budget:

  correctness      did the recommended candidate survive the real factory?
                   (from the recorded outcome — no re-running the factory)

  coverage         was the eventually-successful candidate in the generated set?
                   (requires DFE-007 runner-up sampling — see §7)

  calibration      was predicted confidence borne out?
                   Brier score against recorded outcomes.

  cost             exploration spend, as a constraint rather than a term:
                   variants exceeding budget are invalid, not penalised
```

**Coverage is weighted at least as heavily as correctness, and this is not negotiable.** A loop scored on correctness alone will drift toward narrower generation, because small sets are easier to rank correctly — and its numbers will improve the entire way down. That failure is invisible without coverage and irreversible once the algorithm has learned it.

**Calibration is scored, not just accuracy.** A Preflight that is right 70% of the time and says so is more useful than one right 80% of the time that always claims certainty, because the factory's job is to catch the other 30% and it needs to know when to look hard.

---

## 6. Held-out protocol

**Temporal split, always.** Tune on decisions 1..N; evaluate on N+1 onward. Never random split — decisions are not i.i.d., the codebase moves under them, and a random split leaks future repository state into past decisions.

**Never re-run the factory during the loop.** Outcomes come from the recorded corpus. A loop that can trigger real factory runs is a loop that can spend unbounded money and mutate the repository, and this proposal would then be Tier 2.

**Blind-judge isolation applies unchanged** (DFA-031). Nothing this loop produces reaches a holdout or certifier. The tuned Preflight is a drafting role; blind roles remain blind by capability, not by instruction.

**Anti-benchmark-cheating** (DFP-078) applies to the loop as much as to Preflight. If a variant scores well by recognising specific historical decisions rather than by deciding better, that is memorisation. Rotate the held-out boundary and watch for score collapse when it moves.

### Replay searches; shadow validates

Offline replay is the right instrument for **search** — many variants, cheap, counterfactual. It is the wrong instrument for **adoption**, because a variant that wins on frozen history can lose on live decisions where the repository has moved underneath it.

Two stages, in order:

```
1. REPLAY   many variants against the frozen corpus → one winner
2. SHADOW   the winner runs alongside production Preflight on live decisions,
            recording what it would have chosen, acting on nothing
3. ADOPT    only once shadow and production diverge in the winner's favour,
            over enough live decisions to mean something
```

This is DFG-006 — shadow and equivalence first, cutover second — which the constitution already requires for trust migrations. It applies here for the same reason it applies there: **a system that has only been validated against its own training distribution has not been validated.**

---

## 6a. The strategic tension

Recorded here because it is real, unresolved, and appears nowhere else in the corpus.

**Preflight and the acceleration programme pull against each other.**

Preflight's premise is that thinking is cheap and building is expensive, so think hard and build once. The acceleration programme's goal is to make building cheap. Taken far enough, cheap proof erodes the case for expensive prediction — at some cost per lap, running the experiment beats predicting its outcome, and this is precisely why autoresearch does not bother predicting anything.

Three consequences worth holding:

- **The value of this proposal falls as ACP-003 and the named accelerations succeed.** That is not an argument against either; it is an argument for sequencing them in the order given and re-checking this one before building it.
- **Cheap laps dissolve the coverage problem.** DFE-007's runner-up sampling exists because only the winner's outcome is ever observed. If a lap is cheap, build the runner-up — and the third. Coverage stops being an instrument to design and becomes something to buy.
- **The crossover point is measurable, not arguable.** The per-stage timings from ACP-003 are what tell you where it sits. Until they exist, neither position can be defended.

**Do not resolve this by argument.** Instrument first, and let the cost per lap decide.

---

## 7. Preconditions

Not optional, and the loop must not be built before they hold.

**DFE-007 — runner-up sampling.** The historical corpus records outcomes only for winners. Without periodically building the runner-up, coverage is unmeasurable, and §5 says coverage is the term that prevents the loop's worst failure. **This is now a precondition rather than an improvement, and its priority should rise accordingly.**

**DFM-026 — retention 7 → 90 days.** Every lap run before this lands is a training example deleted. This proposal converts a cheap chore into the precondition for a capability, and it is the single strongest argument for doing it this week.

**Corpus size.** Meaningless below roughly 50 completed consequential decisions with recorded outcomes. Realistically a month-six capability. Say so plainly rather than building the harness early and running it on noise.

---

## 8. Where the code lives and how changes land

**The harness** is a new offline package — `preflight_lab/` or similar — outside the TCB, with no capability grants, no repository mutation and no ability to invoke the factory. It reads the frozen corpus, runs Preflight variants against it, and writes a scored experiment log.

**It runs offline**, on a developer machine or a scheduled runner. It is not part of the production worker path and nothing in the factory depends on it.

**Winning variants land as one reviewed PR.** Not a merged chain of attempts. Karpathy's README jokes about a codebase in its 10,205th generation that no human can any longer read, and that joke is a warning: an accumulating chain of auto-merged commits is incompatible with the provenance requirements this system exists to enforce.

```
experiment log      evidence, retained
winning config      one PR, through the normal factory path
losing variants     discarded, but their scores retained for calibration
```

The PR is ordinary product work: contract, RED, review, conformance, merge. **The loop proposes; the factory proves.** Which is the same relationship Preflight already has to the factory, applied one level up.

---

## 9. Failure modes and stop conditions

| Failure | Detection | Response |
|---|---|---|
| Narrowing generation to make ranking easier | Coverage term falls while correctness rises | Stop. This is the designed-for failure and the loop is working as a detector when it catches it. |
| Memorising held-out decisions | Score collapses when the split boundary moves | Rotate boundary, enlarge corpus, discard the variant. |
| Overfitting to one era of the codebase | Performance decays on newer decisions | Re-tune on a rolling window; treat old decisions as decayed evidence. |
| Optimising cost by exploring nothing | Direct-mode rate rises without correctness rising | Cost is a constraint, not a term. Invalid, not merely worse. |
| Loop proposes constitutional change | Any variant touching frozen files | Reject automatically. Not a proposal; a bug. |

**Hard stop:** if two consecutive tuning rounds fail to improve held-out score, stop. The corpus is too small or the algorithm is at a local optimum, and both are reasons to wait rather than to search harder.

---

## 10. Acceptance criteria

1. The harness reproduces recorded historical Preflight decisions from the frozen corpus, deterministically.
2. Frozen-file digests are asserted before and after every run; any mismatch aborts.
3. A deliberately degraded variant — one that generates a single candidate — scores measurably **worse**, driven by the coverage term. If it does not, the metric is broken and nothing else in this proposal is trustworthy.
4. The temporal split holds: a variant tuned on 1..N does not score anomalously well on 1..N relative to N+1..M.
5. The harness cannot invoke the factory, spend provider budget beyond a declared cap, or mutate the repository. Proven by test, not by inspection.

Criterion 3 is the important one. **Build the metric, prove it detects a known-bad variant, and only then build the loop.**

---

## 11. What this does not do

- **Does not select candidates.** Candidate selection stays governed by DFP-010's pre-registered decision policy and DFP-016's Pareto frontier. This loop never sees a live decision.
- **Does not touch the factory, the kernel, or any authority.**
- **Does not become the Virtual Factory.** F4 remains future work. This tunes the search; it does not learn to predict outcomes.
- **Does not extend to the Front Door.** The same idea there would optimise against an approval-shaped metric, which is precisely the drift DFE-006 exists to prevent. Explicitly out of scope.
- **Does not run before the canary lands.** Nothing does.

---

## 12. Consequences

**If approved:** DFP-060 through DFP-063 move from OPEN to a specified mechanism with a defined precondition. DFE-007 and DFM-026 rise in priority, since both become blocking. No trust boundary changes.

**If rejected:** those four decisions stay OPEN, and the tuning happens implicitly through human adjustment — which is the current state, and works, but leaves no evidence and no calibration.

**Note on sequencing.** The inner Preflight loop — cheap evaluation, prune the dominated, escalate only where uncertain — is already a keep-or-discard ratchet under a fixed budget. A Karpathy loop was designed into Preflight without being named one. What is missing, and what this proposal adds, is the outer loop that improves the explorer.
