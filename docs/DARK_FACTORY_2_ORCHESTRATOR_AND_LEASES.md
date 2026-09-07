# Dark Factory 2.0 — Orchestrator, Scheduling and Lease Semantics

**Status:** owner-approved target architecture.  
**Purpose:** make concurrency safe and efficient without moving proof authority into the scheduler.

This document extends `DARK_FACTORY_2_KERNEL_TCB_MIGRATION.md` §§25–26 and `DARK_FACTORY_2_CANONICAL_CONTRACTS.md` §26.

---

## 1. Governing rule

> **The orchestrator may decide what to try next. It must not decide what is proved.**

The orchestrator is intentionally untrusted with respect to merge correctness.

A scheduler defect may:

- waste money;
- choose a poor order;
- starve work;
- dispatch duplicate attempts;
- incur latency;
- choose a weak model;

but must not be able to:

- skip evidence requirements;
- make stale evidence current;
- bypass a lease fence;
- grant itself broader capability;
- merge an unqualified revision.

---

## 2. Target split

```text
PROJECT / PROGRAMME STATE
          ↓
SERIAL COORDINATOR
  - reaps stale leases
  - computes ready frontier
  - plans cheap next action
  - reserves capacity/cost
  - atomically acquires leases
  - dispatches executors
          ↓
PARALLEL RESOURCE-SCOPED EXECUTORS
  - present lease ID + generation
  - request capabilities
  - run bounded stages
  - emit results/events
          ↓
TRUSTED KERNEL / AUTHORITIES
  - validate evidence
  - authorise transitions
  - authorise privileged effects
```

The coordinator should be short-lived. Executors should not hold a global scheduler lock for the duration of model calls or qualification.

---

## 3. Operational state is not proof state

Operational states may include:

```text
queued
ready
leased
running
waiting-external
waiting-authority
retryable
blocked
completed
abandoned
```

These are scheduling facts only.

They must not duplicate or reinterpret the evidence-spine lifecycle. An executor being `completed` does not mean its work is qualified. A run being `running` does not create proof state.

---

## 4. Resource model

Every lease protects a canonical resource key.

Initial resource classes:

```text
programme-item:<item_id>
factory-run:<run_id>
pull-request:<repo>:<pr>
branch:<repo>:<branch>
trust-root-qualification:<trust_root_digest>
application-qualification:<tree_digest>
project-graph-writer:<project_id>
main-mutation:<repo>
```

Capacity resources are separate semaphores rather than ownership leases:

```text
provider:<provider>/<model>
hosted-runner:<class>
model-budget:<project/run>
external-api:<service>
```

Do not use one repository-wide lease merely because implementation is easier. Global serialisation is permitted only for genuinely global effects such as a `main` mutation where concurrent mutation would be unsafe.

---

## 5. Lease v2 authoritative semantics

Lease identity:

```text
(resource_key, lease_id, generation)
```

Properties:

- one active generation per exclusive resource;
- generation is monotonically increasing per resource;
- acquire is atomic;
- renew does not change generation;
- reacquire after expiry/reap increments generation;
- old generation never becomes valid again.

A lease may carry:

```json
{
  "lease_id":"lease_...",
  "resource":{"type":"programme-item","id":"item_..."},
  "owner":{"type":"factory-run","id":"run_...","executor_id":"exec_..."},
  "purpose":"implementation",
  "state":"active",
  "generation":7,
  "acquired_at":"...",
  "heartbeat_at":"...",
  "expires_at":"...",
  "max_runtime_at":"...",
  "handoff_ref":null
}
```

`max_runtime_at` prevents indefinite heartbeat renewal when a stage itself has a hard execution wall.

---

## 6. Atomic acquisition

Conceptually:

```text
BEGIN
read resource lease row
if active and not reapable: fail busy
new_generation = previous_generation + 1
write active lease with unique lease_id
COMMIT
```

The storage implementation may use relational transactions, conditional updates or another CAS primitive, but must prove single-owner acquisition under race.

Do not emulate atomicity with “read, then hope no one else wrote”.

---

## 7. Heartbeats

Heartbeats demonstrate liveness; they do not authorise mutation.

A heartbeat request includes:

```text
lease_id
generation
executor_id
observed stage
```

Renew only if all identity fields match the current active lease.

A heartbeat from stale generation is rejected rather than reviving the old lease.

Heartbeat cadence should be substantially shorter than TTL but not so frequent that the control plane becomes noisy. Exact durations are configuration, not architecture.

---

## 8. Expiry and reaping

A lease is **reapable** when:

- expiry is past and no protected handoff is active; or
- owner/run is definitively terminal; or
- explicit recovery authority marks owner lost.

Reaping:

```text
active → reaped
```

A later acquire increments generation.

Do not immediately delete historical lease rows; fencing/audit needs the generation history.

---

## 9. Handoff

Some work needs intentional executor replacement without losing ownership semantics.

Handoff must be explicit:

```text
owner A requests handoff
→ create handoff record bound to lease/generation
→ coordinator names owner B
→ either transfer under a protected protocol or release/reacquire as generation N+1
```

Preferred safety rule: consequential mutation authority for the new executor uses a new generation unless a concrete use case proves same-generation transfer is necessary.

---

## 10. Ready frontier

For programme items, readiness is derived from the **compiled programme DAG**, not model judgement.

An item enters ready frontier only if:

- all required predecessor items satisfy their declared completion condition;
- item is not superseded/cancelled;
- required spec/programme version is current;
- no blocking unresolved owner decision exists;
- no active conflicting lease exists;
- any strategy recommendation/handoff it requires is current;
- budget/capacity policy permits dispatch.

The synthesiser may propose dependency changes; only the deterministic compiler changes the executable DAG.

---

## 11. Scheduler planning order

Coordinator iteration:

```text
1. ingest new canonical events/results
2. reap definitely stale leases
3. recompute invalidated/stale work
4. compute ready frontier
5. plan the cheapest useful next action for each candidate
6. remove resource conflicts
7. apply stop/emergency gates
8. reserve cost/capacity
9. atomically acquire leases
10. dispatch executors
11. persist dispatch identities
12. exit
```

Crucially, **planning happens before heavyweight provisioning**.

A no-op, stale-head re-head, label change or deterministic refusal must not require Postgres, browser binaries, model-route probes and a full worker environment just to discover what action was needed.

---

## 12. Wake-driven coordinator

Do not rely on foreground polling loops.

Coordinator should be triggered by useful events where possible:

- issue/programme state change;
- executor completion;
- PR/head movement;
- authority completion;
- lease expiry timer;
- owner answer;
- new project command;
- explicit retry/recovery event.

A periodic schedule remains a recovery/backstop, not the primary responsiveness mechanism.

The observed hourly cron unreliability should therefore not be allowed to define the target architecture.

---

## 13. Action planning before runner provisioning

Every dispatch should first classify the intended action in a lightweight environment.

Example plan result:

```json
{
  "action":"REHEAD",
  "resource":"pull-request:134",
  "requires_model":false,
  "requires_database":false,
  "requires_browser":false,
  "requires_app_token":true,
  "estimated_class":"cheap"
}
```

Heavy executor provisioning follows the plan.

This should eliminate the current pattern where no-op/re-head paths download large browser/tool stacks before knowing what they will do.

---

## 14. Executor contract

Executor receives immutable dispatch envelope:

```text
dispatch_id
resource key
lease_id + generation
run/item identity
exact base/head/spec/programme refs
planned action
budget reservation
allowed capability request classes
```

Executor must refuse to start if canonical resource state no longer matches the dispatch envelope.

Executors may create sub-attempts, but may not silently switch to unrelated programme items under one lease.

---

## 15. Retry taxonomy

Retry policy must be based on failure class, not generic non-zero exit.

Initial classes:

### TRANSIENT_INFRASTRUCTURE

Examples:

- action download 504;
- runner/network transient;
- provider 5xx/rate limit where retry is allowed.

Response: bounded automatic retry with backoff/jitter and same semantic action if subject remains current.

### DETERMINISTIC_REFUSAL

Examples:

- trust root stale;
- wrong head;
- missing required evidence;
- capability scope mismatch.

Response: **do not retry identical action**. Compute the prerequisite action.

### MODEL_ATTEMPT_FAILURE

Examples:

- builder fails test within attempt budget;
- malformed output;
- model route failure.

Response: use stage retry/routing policy within bounded attempt budget.

### STRATEGY_REJECTED_BY_REALITY

Response: terminal for that candidate; return to Preflight/recommendation layer rather than infinite implementation repair.

### STALE_INPUT

Examples:

- base moved;
- approved spec superseded;
- programme version changed.

Response: cancel old executor/capabilities; recompute from new canonical state; possibly invoke ReheadAuthority.

### OWNER_DECISION_REQUIRED

Response: create one explicit question with recommendation; no polling/retry until answer/event.

### PERMANENT_PLATFORM/POLICY

Response: incident/architecture repair; do not burn model budget repeatedly.

---

## 16. Retry budget

Every retry class has independent limits.

Do not allow retries to hide systemic failure.

Record:

```text
attempt number
failure class
subject identity
cost
wall time
retry decision
policy version
```

A retry after subject movement is a new semantic attempt, not merely attempt N+1 on the old subject.

---

## 17. Re-head scheduling

Re-head is not ordinary retry.

Flow:

```text
base/head currency check
→ stale detected cheaply
→ orchestrator evaluates whether re-head is worthwhile
→ acquire PR/branch resource lease
→ invoke deterministic ReheadAuthority
→ if valid, App-backed branch update
→ authorities re-fire on new head
```

No expensive full qualification before currency is current.

Only ReheadAuthority determines whether evidence preservation/reissuance is valid.

---

## 18. Concurrency classes

### Class A — independent programme items

May execute concurrently when resource/path/dependency analysis finds no conflict.

### Class B — independent validators

Five independent model authorities should fan out once they share one immutable validation subject and cannot see each other's verdicts.

### Class C — mutation shards / disposable probes

May parallelise when isolated and aggregation proves complete disjoint coverage.

### Class D — project graph writes

Concurrent commands use optimistic expected-project-version CAS; conflicts are retried through command rebase/refresh, not last-write-wins.

### Class E — main mutation

Remain serialized around the actual protected `main` mutation unless a future platform mechanism proves stronger semantics.

---

## 19. Conflict model

Programme concurrency should not be based only on issue identity.

Potential conflict sources:

- overlapping writable path envelopes;
- same PR/branch;
- same programme item;
- same schema/migration resource;
- same exclusive external environment;
- same main mutation;
- explicit DAG dependency.

Use conservative declared resource footprints first. Later, measured conflict data may improve planning, but uncertainty must not permit unsafe shared mutation.

When two implementations can be isolated in separate worktrees but cannot both merge unchanged, they may build concurrently; integration/re-head ordering remains a later scheduling concern.

---

## 20. Budget and capacity semaphores

Economic policy belongs in the orchestrator, not the kernel.

Separate:

```text
proof permission
≠
resource/cost permission
```

A work item may be legally executable but delayed by budget/capacity.

Semaphores may include:

- max simultaneous provider/model calls;
- max expensive judge calls;
- hosted runner capacity;
- project daily spend;
- per-run spend;
- external API quota.

Use reservation then reconciliation:

```text
reserve estimated upper bound
→ run
→ record actual
→ release difference / charge overage according to policy
```

A budget exhaustion means `waiting-capacity/budget`, not failed proof.

---

## 21. Scheduling priority

Default priority should be deterministic and explainable.

Candidate factors:

1. explicit owner priority;
2. critical-path position / number of downstream items unblocked;
3. age/starvation prevention;
4. cheap prerequisite before expensive dependent work;
5. recovery of already-near-complete qualified work;
6. cost/risk class.

Do not use opaque model scoring for whether a lease may be acquired.

A model may advise prioritisation; final ordering rule should be inspectable and bounded.

---

## 22. Fairness and starvation

Use aging so a stream of new high-priority work cannot starve normal items forever.

Emergency/security/owner-critical classes may supersede ordinary fairness, but the reason must be explicit.

Do not let a permanently failing item monopolise provider or mutation capacity. Retry budgets and cooldowns apply.

---

## 23. Cancellation

Executor becomes cancel-worthy when:

- subject/spec/programme version is superseded;
- lease generation is fenced out;
- emergency stop activates;
- owner cancels project/item;
- a required predecessor is invalidated;
- another accepted result makes duplicate work unnecessary.

Cancellation signal is advisory for compute cleanup; fencing/capability revocation is the real safety boundary.

Even if a cancelled process keeps running, its stale capabilities must refuse consequential mutation.

---

## 24. Crash recovery

On coordinator restart:

1. reload canonical runs/leases/dispatches;
2. inspect active leases and heartbeats;
3. correlate with externally observable executor/workflow state;
4. reap only when safe criteria are met;
5. never assume absence of an in-memory process means mutation authority is gone;
6. fence old generations before redispatch.

On executor crash, partial unimported worker-view changes are disposable unless explicitly checkpointed under a typed handoff/recovery artifact.

---

## 25. Idempotency

Every external dispatch/mutation request needs an idempotency identity where provider/platform supports it, or an internal deduplication record where it does not.

Examples:

```text
dispatch:<resource>:<generation>:<planned-action>
pr-create:<run>:<head>
rehead:<pr>:<old-head>:<new-base>
```

Duplicate coordinator invocation must not accidentally create two active leases or two semantically duplicate PRs.

---

## 26. Event/result ingestion

Executors do not directly mutate arbitrary scheduler state.

They emit typed results:

```text
stage-completed
stage-failed
attempt-exhausted
authority-produced
subject-stale
strategy-rejected
external-operation-receipt
```

Coordinator/projector validates identity and applies canonical state updates.

Out-of-order results from stale lease generations remain historical telemetry but cannot advance current work.

---

## 27. Expensive-action rule

Before any expensive stage, planner must be able to state:

```text
uncertainty being resolved
cheaper evidence already exhausted
expected wall/cost class
subject identity
next action on PASS
next action on FAIL
```

If not, do not dispatch the expensive stage.

This is scheduler policy and audit discipline, not merge authority.

---

## 28. Observability

Track at least:

- ready-frontier size;
- queue wait;
- lease acquisition conflict rate;
- stale-generation refusals;
- heartbeat/reap counts;
- runner provisioning waste before action plan;
- retry counts by class;
- work cancelled due to stale input;
- provider/model concurrency;
- cost reservation versus actual;
- critical-path wall time;
- idle time attributable to scheduler latency.

This makes #144/#147-style latency work measurable rather than anecdotal.

---

## 29. Adversarial acceptance tests

Must prove:

- two coordinators racing acquire produce one current lease owner;
- generation N mutation refuses after N+1 acquisition;
- expired but unreaped lease cannot be stolen without atomic generation advance;
- stale executor completion cannot mark current run complete;
- duplicate coordinator wake does not double-dispatch;
- base movement cancels/redirects work before expensive validation;
- deterministic refusal is not retried identically;
- transient infrastructure error can retry without changing proof semantics;
- budget exhaustion delays but does not falsify proof state;
- global scheduler lock is not held throughout executor runtime;
- model-worker crash eventually becomes redispatchable;
- conflicting resource footprints do not execute mutable stages concurrently;
- independent validators can execute concurrently without verdict visibility;
- emergency stop prevents new privileged capabilities and fences existing applicable ones.

---

## 30. Migration sequence

1. Introduce lightweight `plan-action` before heavyweight worker provisioning.
2. Record retry/failure classes rather than generic failure.
3. Make current lease resource identity/generation explicit everywhere consequential.
4. Add atomic lease CAS tests.
5. Split serial coordinator from executor workflow while preserving one-work-item semantics.
6. Parallelise independent validators first or alongside independent issues where easiest to prove.
7. Add resource footprints/conflict detection.
8. Add provider/model/budget semaphores.
9. Move to wake-driven dispatch with periodic backstop.
10. Parallelise programme items only after fencing/capability enforcement is real.
11. Add graph-write concurrency last, with expected-version CAS.

---

## 31. Locked conclusions

1. Coordinator is short-lived; executors hold resource leases, not a global scheduler lock.
2. Lease generation is mandatory fencing for consequential mutation.
3. Operational state never substitutes for evidence state.
4. Ready frontier comes from compiled DAG + current canonical dependencies.
5. Plan the action before provisioning expensive environments.
6. Retries are failure-class-specific; deterministic refusal is not a retry loop.
7. Re-head is a first-class prerequisite action, not a full-validation failure mode.
8. Budget/capacity policy is economic orchestration, not kernel proof authority.
9. Event-driven wakeups are primary; cron is recovery/backstop.
10. Stale processes may continue computing, but they must be unable to mutate because leases/capabilities fence them out.
