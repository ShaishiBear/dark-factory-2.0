# Dark Factory 2.0 — Learning, Experience and Calibration Architecture

**Status:** owner-approved target architecture.  
**Purpose:** let Dark Factory improve from accumulated experience without letting historical model opinions become hidden merge authority or contaminate independent judges.

---

## 1. Governing rule

> **Learning may improve what the factory tries. It must not decide what the factory proves.**

The learning system is untrusted with respect to merge authorisation.

A bad lesson may cause:

- a poor candidate;
- wasted time;
- a bad model route;
- a failed implementation;

but must not:

- fill a required authority slot;
- override a failing deterministic check;
- make stale evidence current;
- bypass an independent judge;
- alter approved product intent silently.

---

## 2. Three different stores

Do not collapse these concepts.

### Trajectory archive

Immutable factual record of attempts.

Contains:

- inputs/subject identity;
- stages/attempts;
- model/method versions;
- costs/timings;
- artifacts/results;
- terminal outcome.

### Derived analytics

Computed statistics/patterns from trajectories.

Examples:

- stage failure rate;
- cost distributions;
- model route success by task class;
- re-head success rate;
- mutation bottleneck timings.

Rebuildable; not authority.

### Engineering lessons

Curated/validated hypotheses derived from multiple observations.

Examples:

- “For this repo class, do currency check before runner provisioning.”
- “Provider X frequently times out on architecture prompts above context size Y.”

Lessons remain advisory.

---

## 3. Trajectory begins at attempt start

Create trajectory identity before substantial work begins.

Do not create trajectories only for successful PRs.

Record:

```text
aborted attempts
refused attempts
provider failures
qualification failures
re-heads
strategy rejection
successful merges
post-merge incidents
```

Otherwise learning data is survivorship-biased.

---

## 4. Immutable attempt records

A stage retry creates another stage-attempt record; it does not overwrite the failure.

Example:

```text
implement attempt 1 — failed tests — $0.82
implement attempt 2 — success — $0.51
```

Final trajectory points to both.

Historical data should be append-only except for explicit redaction/privacy procedures.

---

## 5. Experience packet

Do not feed giant raw transcripts back into future models by default.

Derive a bounded structured `ExperiencePacket`:

```json
{
  "schema":"dark-factory/experience-packet",
  "schema_version":"1.0",
  "trajectory_id":"traj_...",
  "task_class":"frontend-lifecycle",
  "repo_features":{},
  "strategy_ref":"cand_...",
  "stage_outcomes":[],
  "failure_classes":[],
  "repairs":[],
  "cost":{},
  "wall_seconds":0,
  "terminal_outcome":"qualified",
  "facts":[],
  "candidate_lessons":[]
}
```

The packet is a projection of source trajectory and must retain source references.

Raw transcript remains available for targeted forensic retrieval, not routine context stuffing.

---

## 6. Lesson lifecycle

```text
candidate
→ supported
→ approved-for-retrieval
→ challenged
→ dormant
→ retired
```

A lesson may return from challenged/dormant after new evidence.

Do not automatically promote one successful anecdote into an approved lesson.

---

## 7. Candidate lesson requirements

Candidate lesson should contain:

```text
statement
scope/task classes
supporting trajectory refs
contradicting trajectory refs
mechanism/rationale
falsifier
applicability conditions
confidence method
created-by method/version
```

A lesson without a falsifier/applicability boundary is likely to become folklore and should not be promoted.

---

## 8. Evidence versus recommendation

Keep:

```text
Observed: 14/18 attempts with X failed due to Y.
```

separate from:

```text
Lesson: prefer Z before X for this task class.
```

and separate from:

```text
Current recommendation: use Z on this issue.
```

Facts, learned generalisation and current decision are different semantic objects.

---

## 9. Lesson approval

Lesson approval is deterministic/analytic where feasible, with model synthesis allowed only as proposal.

Possible requirements by lesson class:

- minimum independent supporting observations configured by policy;
- counter-evidence search;
- historical holdout/backtest where applicable;
- no obvious data leakage from target outcome;
- stable scope definition;
- explicit falsifier;
- human/architecture approval only for high-impact policy lessons.

Do not hardcode arbitrary universal sample counts in architecture; configure them per lesson class after observing data volume.

---

## 10. Counter-evidence is first-class

Retrieval must not return only supporting examples.

Lesson object maintains:

```text
supporting trajectories
contradicting trajectories
unknown/out-of-scope trajectories
```

When contradiction crosses policy threshold, mark lesson `challenged` and reduce/stop automatic retrieval until reevaluated.

This prevents self-confirming feedback loops.

---

## 11. Historical holdout

Where enough history exists, reserve a time/trajectory holdout not used to generate the lesson.

Evaluate whether applying the lesson would have improved:

- qualification success;
- wall time;
- cost;
- repair count;
- strategy rejection rate;

without increasing safety/refusal errors.

Learning-system backtest is advisory analytics; never retrospectively rewrite original trajectory outcomes.

---

## 12. Prediction calibration

Preflight/model predictions should resolve against actual outcomes.

Track calibration separately for:

```text
first-pass qualification probability
cost estimate
wall-time estimate
repair-count estimate
strategy rejection probability
```

Use proper scoring/calibration metrics where enough data exists.

Do not display fake precise probabilities before empirical calibration supports them.

When uncalibrated, use qualitative uncertainty honestly.

---

## 13. Retrieval visibility matrix

Default target:

| Role | Learning access |
|---|---|
| Front Door question planner | limited product/process lessons where relevant |
| Programme synthesiser | yes |
| Preflight candidate generator | yes |
| Long-Horizon Architect | yes |
| Orchestrator/model router | yes, especially operational lessons |
| Builder/implementation worker | bounded task-relevant lessons |
| Repair worker | bounded failure-class lessons |
| Ordinary reviewer (non-independent) | possibly bounded |
| Independent RED/GREEN authority | **no learned verdict/context unless policy explicitly says otherwise** |
| Blinded code holdout | **no** |
| Architecture holdout | **no prior architecture verdict/lesson that breaks blindness** |
| Independent certifiers | **no** where independence requires blindness |
| Kernel / evidence closure | **no need; learning cannot alter proof** |

The exact matrix should live in protected independence/capability policy.

---

## 14. Blindness is enforced structurally

Do not rely on prompt text saying:

> “Ignore previous lessons.”

For blind roles:

- learning store absent from worker view;
- no retrieval tool/capability;
- prompts contain no lesson summaries;
- trajectory verdict fields hidden where prohibited;
- session lineage does not carry prior judge output.

Independence tests must attempt to retrieve forbidden learning and prove failure.

---

## 15. Retrieval query

A learning-enabled role requests context with explicit scope:

```json
{
  "repo":"...",
  "task_class":"frontend-lifecycle",
  "stage":"implement",
  "failure_class":null,
  "component_kinds":["frontend"],
  "max_lessons":5,
  "max_examples":3
}
```

Retriever filters by:

- status `approved-for-retrieval`;
- applicable scope;
- current policy;
- role visibility;
- contradiction/challenge state;
- recency only as an applicability signal, never proof authority.

---

## 16. Retrieval ranking

Rank by explainable factors such as:

```text
scope match
empirical support
contradiction rate
applicability freshness
mechanism similarity
```

Do not use one opaque model ranking as the only access control.

Every surfaced lesson should include a terse basis:

```text
why retrieved
support count/range
known contradiction
falsifier
```

---

## 17. Context budget

Learning must reduce rediscovery, not create another huge context burden.

Default retrieval is small and structured.

Prefer:

```text
3–5 relevant lessons
few representative examples
links/refs for deeper retrieval
```

over dumping dozens of old transcripts.

If a model needs raw history, retrieve only the exact trajectory fragment necessary for the current uncertainty.

---

## 18. Negative lessons

Store lessons about what *not* to do where evidence supports it.

Examples:

```text
"Do not launch full qualification while trust-root currency is known stale."
"A generic non-zero mutation detector result is not semantic proof."
```

Negative lesson must still have scope/falsifier; do not turn temporary incident workarounds into universal dogma.

---

## 19. Operational lessons versus software lessons

Maintain at least domains:

### factory

About Dark Factory mechanics:

- provider routing;
- qualification;
- GitHub event behaviour;
- scheduler setup waste;
- mutation detectors.

### software

About application engineering patterns:

- framework lifecycle traps;
- repo-specific test setup;
- library/API behaviour.

Architecture/product lessons may become a third domain if useful, but blindness implications are stronger and should be reviewed explicitly.

---

## 20. Policy lessons cannot silently rewrite policy

A learned statement such as:

> “This holdout rarely catches defects.”

cannot remove the holdout.

It may generate an Architecture Change Proposal with evidence.

Actual policy change follows architecture governance and protected amendment process.

Learning proposes; governance decides; kernel enforces current policy.

---

## 21. Model-routing learning

Orchestrator may use empirical routing analytics to choose cheaper/faster models for non-trusted work.

Inputs may include:

```text
task class
historical success rate
cost
wall time
retry rate
context size
provider reliability
```

Routing policy remains economic/untrusted.

Do not automatically downgrade independent required authority model/class if protected policy specifies one.

---

## 22. Preflight learning

Preflight can use lessons to:

- generate better candidate sets;
- avoid known dead ends;
- choose which uncertainty deserves a disposable probe;
- improve F0 historical predictions;
- estimate likely factory cost/failure.

But final recommendation remains `UNPROVEN` and the factory may reject it.

A learned preference cannot suppress credible alternatives when current evidence materially differs.

---

## 23. Learning from failure

Failure taxonomy is essential.

Distinguish:

```text
infrastructure transient
provider/model failure
implementation defect
strategy defect
qualification harness defect
stale input/re-head issue
policy refusal
owner/product ambiguity
```

Otherwise learning may conclude “strategy X is bad” from failures actually caused by runner 504s or a broken harness.

Only semantically attributed outcomes should train strategy lessons.

---

## 24. Data hygiene and secrets

Trajectory/learning pipeline must redact or avoid storing:

- API keys/tokens;
- private-key material;
- secret environment values;
- unnecessary personal data;
- credentials in command output.

Store stable secret names/references and redacted hashes only where necessary.

When repository becomes private, private source snippets may be retained under repository access controls, but do not assume privacy eliminates least-data principles.

---

## 25. Retention

Raw trajectories should have a deliberate retention policy long enough to support calibration/forensics.

The earlier observed seven-day artifact retention is too short for meaningful longitudinal learning; target default should be materially longer (the current architecture recommendation is around 90 days for run artifacts, subject to storage/cost measurement).

Derived compact experience packets/lessons may retain longer because they are cheaper, but must preserve source lineage while source exists.

Do not make retention a proof dependency unless policy explicitly requires historical audit availability.

---

## 26. Drift and dormancy

A lesson may become stale because the ecosystem/repo changed.

Use applicability signals:

```text
last applicable trajectory
repo architecture version
framework/toolchain version
task class
component signatures
```

After configurable number of applicable opportunities with no supporting evidence, lesson may become dormant.

Dormancy is not deletion.

If framework/architecture changes materially, proactively challenge lessons scoped to the old version.

---

## 27. Learning incidents

If a retrieved lesson is later shown to have systematically harmed decisions:

- open learning incident;
- identify affected decisions/runs;
- challenge/retire lesson;
- inspect whether retrieval ranking or promotion test failed;
- do not retroactively change historical decisions;
- feed incident into architecture governance if boundary/policy changes are needed.

---

## 28. Metrics

Track:

```text
lesson retrieval count
retrieval hit/use rate
outcome delta when lesson applied (where measurable)
contradiction rate
lesson challenge/retirement rate
prediction calibration error
cost/wall prediction error
repeat rediscovery rate
context tokens spent on historical retrieval
blindness-policy violations (must remain zero)
```

Do not optimise “lesson usage” as a goal. A system that retrieves less because it already knows the answer cheaply may be better.

---

## 29. Adversarial acceptance tests

Must prove:

- failed trajectories are retained, not only successes;
- retry does not overwrite earlier attempt;
- lesson cannot fill required evidence/authority slot;
- blind holdout cannot query learning store;
- independent certifier prompt contains no forbidden lesson/verdict leakage;
- one anecdote cannot auto-promote when policy requires broader evidence;
- contradicting evidence can challenge lesson;
- retired/challenged lesson is not retrieved as current advice;
- lesson scoped to framework/version A does not silently apply to incompatible B;
- infrastructure failure is not attributed to strategy without semantic classifier/evidence;
- secret material is redacted before trajectory persistence;
- raw transcript is not routinely injected when bounded packet suffices;
- policy-changing lesson generates ACP rather than editing protected policy directly.

---

## 30. Migration sequence

1. Ensure trajectory starts for every meaningful attempt.
2. Extend artifact retention to support longitudinal analysis after measuring storage.
3. Build deterministic experience-packet derivation from trajectories.
4. Add failure-class normalisation.
5. Resolve existing Preflight predictions against outcomes.
6. Build read-only analytics dashboards/calibration first.
7. Create candidate lesson store with support/contradiction/falsifier.
8. Add promotion/backtest policy.
9. Add role-aware retriever for one non-blind role such as Preflight.
10. Add small bounded retrieval to builder/orchestrator where measured useful.
11. Add structural deny tests for blind authorities before expanding retrieval.
12. Continuously measure whether learning reduces cost/repairs without increasing strategy failures.

---

## 31. Locked conclusions

1. Trajectories are immutable attempt history and start before success is known.
2. Analytics and lessons are derived, not authoritative evidence.
3. Lessons require scope, counter-evidence and falsifier.
4. Retrieval is bounded and role-aware; giant transcript replay is an anti-pattern.
5. Blind/independent authorities are structurally denied learning context where required.
6. Learning can improve planning, routing, candidate generation and repair, but never merge proof.
7. Predictions are resolved/calibrated against real outcomes; no fake precision.
8. Strategy lessons require semantic failure attribution, not generic process failure.
9. Learned policy criticism becomes an ACP, not a silent policy mutation.
10. The learning system should reduce rediscovery while preserving independence and explicit authority boundaries.
