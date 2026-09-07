# Dark Factory 2.0 — Repo-Mapped Kernel / TCB Migration Plan

**Status:** owner-approved target migration; verify against current HEAD before implementation.  
**Purpose:** shrink the real Trusted Computing Base by moving orchestration, adapters and economic policy out of trusted authority while preserving or strengthening fail-closed proof.

This document supplements `DARK_FACTORY_2_TARGET_ARCHITECTURE_AND_OVERSEER.md` and the current repository-protected decisions.

---

## 1. Most important correction: do not build a second lifecycle state machine

Dark Factory already has a declarative evidence dependency graph in `.factory/evidence-spine.json`, a run manifest, independence bindings and merge verification.

The target kernel should derive authorised actions from that evidence policy rather than creating a second independently maintained lifecycle enum that can drift.

Conceptually:

```text
EVIDENCE POLICY
      ↓
claim dependency DAG
      ↓
run manifest + attestations
      ↓
available authorised actions
```

Orchestration state such as `queued`, `leased`, `running`, `repairing`, `waiting` remains separate because it is operational state, not proof state.

---

## 2. TCB test

For every component/function ask:

> **Could a defect here falsely authorise an invalid merge or privileged mutation?**

If yes, it is trusted or part of the enforcement boundary.

If a component can only waste time, spend money, select a poor implementation or fail a run, it should normally be untrusted.

Moving code into a different file does not shrink TCB.

For every extraction record:

```text
BEFORE: why did correctness here matter to authorisation?
AFTER: what independently prevents a defect here from falsely authorising?
```

If there is no convincing AFTER answer, the TCB did not shrink.

---

## 3. Target logical split

```text
factory_project/
    Front Door
    approved specs
    programme
    decision graph

factory_preflight/
    candidate exploration
    probes
    recommendations
    calibration

factory_orchestrator/
    scheduling
    retries
    provider/model routing
    workflow coordination
    worktree lifecycle requests
    GitHub presentation
    cost control
    telemetry

factory_execution/
    capability broker
    worker sandbox/view
    privileged mutation brokers

factory_kernel/
    canonical identity
    evidence policy
    manifest
    authority registry
    attestation verification
    capability authorisation
    merge authorisation

factory_authorities/ or existing protected harness/scripts
    RED/GREEN
    architecture
    security
    holdouts
    provenance
    re-head
    merge verification
```

These are responsibility boundaries, not necessarily processes or microservices.

---

# CURRENT REPO COMPONENTS

## 4. `factory_kernel/canonical.py`

Current responsibility: canonical JSON and SHA-256 primitives.

Target classification: **KEEP IN TCB.**

Do not inflate it with orchestration or provider concerns.

---

## 5. `factory_kernel/manifest.py`

Current concepts already align strongly with the target architecture:

- artifact references;
- certifications;
- claims;
- exact-head subject identity;
- deterministic and independent certifications.

Target classification: **KEEP AND GENERALISE CAREFULLY.**

Prefer evolving this toward the generic typed attestation/claim model rather than creating a competing evidence graph.

---

## 6. `factory_kernel/spine.py`

Current responsibility includes protected evidence requirements, claim dependencies, required authority levels, exact-head obligations and closure assessment.

Target classification: **CORE TCB.**

This should become one of the smallest long-lived kernel surfaces.

Long-term question:

> Given policy + manifest + valid attestations, which claims/actions are authorised?

---

## 7. `factory_kernel/independence.py`

Current principle — independence is structural, not a label — is a locked strength.

It tracks authority, visibility and binding constraints and prevents builder self-certification.

Target classification: **CORE TCB / AUTHORITY REGISTRY.**

Do not weaken explicit `sees` / `binds` semantics.

---

## 8. `factory_kernel/evidence_closure.py`

Current classification: trusted today, but too procedurally knowledgeable long-term.

Migration direction:

```text
claim-specific procedural closure knowledge
        ↓
typed claim/attestation schemas
+ declarative dependency policy
+ generic verification
```

Do not rewrite before tests make every current binding explicit.

Target outcome: smaller generic closure engine plus narrow independent authorities.

---

## 9. `factory_kernel/provenance.py`

Current exact commit/issue/artifact hashing is strong and should remain.

Separate two concerns:

```text
provenance = exactly what qualifies this revision?
trajectory = what happened during this attempt?
```

Trajectory storage never replaces exact-revision provenance.

Git notes may remain a transport until a better one is proven; do not confuse transport with semantic requirement.

---

## 10. `factory_kernel/authority.py`

`CommandEvidence` is a useful deterministic authority primitive.

Target direction: extend only as needed to bind subject revision, policy identity, tool/environment version and evidence digest.

Do not put model reasoning into the low-level deterministic command evidence primitive.

---

## 11. `harness/merge_verify.py`

Current architecture already approximates the final narrow privileged authority:

```text
complete evidence
+ current PR/base/head observation
+ ancestry/tree identity
→ merge authorisation
```

Then post-merge tree identity verifies the result.

Target classification: **KEEP AS NARROW TRUSTED AUTHORITY.**

Move toward:

```text
revision observer
→ merge verifier
→ MergeAuthorization
→ merge capability broker
```

The kernel should not duplicate detailed Git reasoning if a narrow trusted authority can attest it.

---

# RUNTIME EXTRACTION

## 12. `runtime.py` is currently several systems

It currently contains or coordinates:

- scheduler/dispatch priority;
- claims/leases/heartbeats;
- worktree creation and branch lifecycle;
- model invocation and budgets;
- investigation/contract/context/architecture/RED/implementation/review/repair/GREEN;
- carry/provenance/re-head mechanics;
- GitHub labels/comments/PRs;
- validation/holdouts/certifiers;
- merge/post-merge containment.

This is the structural TCB problem.

Do not start by splitting `runtime.py` into multiple trusted files.

Extract authority only after an independent fail-closed boundary exists.

---

## 13. Providers

Current provider code likely combines:

- model/provider mechanics;
- streaming/retries/session IDs;
- token/cost accounting;
- worker containment/permission rendering.

It is therefore not yet honest to call the entire provider layer untrusted.

Target split:

### Untrusted provider adapter

- API mechanics;
- provider retries;
- stream parsing;
- session management;
- model choice/routing;
- token/cost accounting.

### Trusted capability enforcement

- visible filesystem;
- writable filesystem;
- credentials;
- network destinations;
- tool surface;
- Git permission.

Target flow:

```text
CapabilityGrant
→ CapabilityBroker / WorkerSandbox
→ Provider
```

A provider bug should yield malformed/missing work or a failed run, not an authorisation bypass.

---

## 14. Worker view / sandbox target

Investigate a provider-neutral worker view:

```text
canonical worktree
→ broker creates bounded worker view
→ only allowed reads visible
→ only allowed writes mutable
→ trust-root/holdout paths absent
→ provider edits worker view
→ broker computes exact diff
→ validates capability
→ imports authorised mutation into canonical worktree
```

Workers should not receive unrestricted shell/filesystem/network access and merely be instructed not to misuse it.

Benchmark copying/materialisation cost before committing to this exact implementation.

---

## 15. Split worker capability policy from economic policy

Trusted capability policy:

- read/write paths;
- blind paths;
- trust-root protection;
- allowed tools;
- Git/network/credential privileges.

Untrusted/economic routing policy:

- provider/model;
- effort;
- turn/thinking budget;
- dollar budget;
- latency preference;
- retry routing.

A bad model-routing choice may waste money. It must not authorise a merge.

---

## 16. Credentials

Credential scoping is security relevant.

Target: credentials are issued through capability enforcement, not chosen by a provider adapter.

Orchestrator requests; policy/broker decides.

Workers get the minimum credential set required for their authorised operation.

---

# GITHUB / GIT PRIVILEGE

## 17. GitHub adapter

Most GitHub workflow behaviour can become untrusted:

- labels;
- comments;
- listing;
- PR presentation;
- ordinary metadata.

But a component holding unrestricted merge capability is not meaningfully untrusted.

Target:

```text
orchestrator
→ ordinary GitHubWorkflowAdapter

kernel
→ MergeAuthorization
→ trusted GitHub Mutation Broker
→ exact merge operation
```

The merge broker constructs the fixed operation itself; it does not accept arbitrary `gh` arguments from orchestration.

Retain expected-head compare-and-swap semantics.

---

## 18. Independent observation

Do not let the same untrusted actor both perform a consequential operation and be the only trusted observer proving it safe.

For merge/revision-sensitive paths:

```text
mutation adapter performs/request action
independent revision observer reports actual state
kernel/verifier compares observed state to authorised subject
```

Apply the same principle where practical to other high-value operations.

---

## 19. Git mutation authority

Current Git authority mixes policy and execution.

Target split:

```text
GitMutationPolicy
→ authorised mutation envelope
→ GitMutationBroker
→ exact staging/commit/import
```

A capability may bind:

- worktree identity;
- base head;
- allowed paths;
- immutable path hashes;
- operation type;
- expiry;
- current lease generation.

Broker re-derives actual dirty paths. Never trust orchestration’s declaration of what changed.

RED/guard immutability semantics should migrate into typed evidence, not disappear.

---

## 20. Worktrees

Separate orchestration from trusted fact.

Orchestration may choose/create/clean workspace locations.

Trusted facts include:

- exact subject revision;
- clean starting state;
- blind paths genuinely absent;
- capability-visible surface.

These should eventually be rechecked/attested by the capability boundary before a worker starts.

---

## 21. Trusted programmes

Preserve the rule:

> **The subject of judgement must never supply the program that judges it.**

Trusted authorities execute from protected authority revision while treating candidate revision as data.

This remains a core independence invariant.

---

# RE-HEAD

## 22. Re-head should be a narrow evidence-preserving authority

Re-head currently contains specialised knowledge about:

- previous provenance;
- RED/guard files;
- test-author commit shape;
- rebased history;
- immutable acceptance hashes;
- reissued RED/GREEN;
- architecture evidence;
- republished provenance.

That is not generic orchestration.

Target `ReheadAuthority` input:

```text
old exact-head provenance
old base/head
new base
rebased candidate history
current policy
```

Output:

```text
REHEAD_ATTESTATION
old subject
new subject
RED re-established
acceptance immutability preserved
affected evidence reissued
```

Orchestrator decides whether re-head is worth attempting. Authority decides whether the transformed subject legitimately preserves/re-establishes proof.

---

# CLAIM-DRIVEN KERNEL

## 23. Conceptual kernel API

```text
authorize(
  subject,
  requested_action,
  evidence_policy,
  manifest,
  attestations,
  capability
)
→ allow/refuse
```

Possible requested actions include worker execution, mutation import, commit, authority execution, PR publication, re-head and merge.

Do not encode historical workflow trivia into the kernel if evidence prerequisites can express it declaratively.

Example:

```text
RUN_IMPLEMENTATION requires:
  contract
  context
  design
  architecture-governor
  RED proof
```

Missing or stale requirement means REFUSE.

Merge remains the most privileged action and should reduce to exact subject + complete required evidence + current observation + one-time merge capability.

---

# TCB INVENTORY / RATCHET

## 24. Machine-readable TCB manifest

Before major extraction classify modules/functions into:

- trusted core;
- trusted authority;
- enforcement broker;
- untrusted orchestrator;
- untrusted adapter;
- observability/telemetry;
- tests/harness.

For each record:

- responsibility;
- approximate LOC;
- privilege;
- input/output;
- failure consequence;
- why it needs trust;
- target location.

Measure trusted LOC.

Material TCB growth is an architecture event requiring justification.

---

# CONCURRENCY PREREQUISITE

## 25. Coordinator / executor split

The current global workflow serialisation means real programme concurrency requires separating work selection from long-running execution.

Target:

```text
SERIAL COORDINATOR
→ acquire/fence resources
→ dispatch resource-scoped executors
→ exit

PARALLEL EXECUTORS
→ present lease ID + generation
→ operate only on their resource
```

Coordinator responsibilities:

- reap stale leases;
- compute ready frontier;
- check capacity/budget;
- acquire resource claims;
- dispatch executors.

Executors should not hold a global scheduler lock while they run.

Introduce concurrency in stages:

1. parallel independent issues/programme items;
2. parallel independent validators;
3. parallel Preflight candidates/probes;
4. concurrent user/agent graph writes.

---

## 26. Lease fencing

Current lease semantics are a useful base. Add resource identity, owner, expiry and `generation`.

Every consequential leased mutation presents current `lease_id + generation`.

If generation N worker wakes after N+1 acquired the resource, REFUSE even if the old process is still alive.

TTL without generation fencing is insufficient for stale-owner safety.

---

# MIGRATION PROGRAMME

## 27. Dependency order

Do not flag-day rewrite.

Recommended order:

### P0 — reliability floor

Finish current qualification/canary chain and preserve a measured single-path baseline.

### P1 — durable trajectories

Capture every meaningful attempt, including early failures.

### P2 — TCB inventory

Classify current code and measure trusted surface.

### P3 — claim-driven authorisation interface

Prototype `allowed_actions()` / deterministic authorisation over current spine + manifest rather than adding a new lifecycle.

### P4 — typed attestations

Normalise current certificates/command evidence behind narrow subject-bound attestations.

### P5 — capability policy split

Separate model/economic routing from security capability policy.

### P6 — capability enforcement

Credential broker and provider-neutral worker containment.

### P7 — Git/GitHub privileged brokers

Split ordinary adapters from privileged mutation/merge operations and add independent observation.

### P8 — re-head authority extraction

Move evidence-preserving re-head knowledge out of orchestration.

### P9 — orchestrator extraction

Only once privileged boundaries exist, move scheduler/build/validation/retry/status/model-routing logic out of `KernelRuntime`.

### P10 — requalification

Run original corpus plus adversarial boundary tests and measure actual TCB reduction.

### P11 — concurrency

Coordinator/executor workflows, lease generations and compute semaphores.

Only after this foundation should Front Door/programme/Preflight/graph features add substantial new runtime complexity.

---

## 28. Required adversarial tests per extraction

### Kernel

- missing claim cannot jump to merge;
- attestation for head A cannot authorise head B;
- old policy attestation cannot replay after policy changes;
- wrong subject hash refuses;
- builder-produced verdict cannot fill independent slot.

### Provider/capability

- provider permission-rendering bug cannot expose trust root;
- worker cannot modify protected paths;
- worker cannot inspect blind holdout paths;
- provider cannot smuggle privileged credentials.

### Git

- file outside envelope refuses;
- RED-hashed acceptance mutation refuses;
- orchestrator lying about dirty set is caught by re-derivation.

### Merge

- adapter lies about head → independent observer prevents authorisation;
- head moves after authorisation → external expected-head CAS refuses;
- merged tree differs → post-merge containment/incident.

### Lease

- expired owner mutation refuses;
- stale generation refuses;
- concurrent coordinator acquisition remains single-owner;
- crashed executor eventually becomes redispatchable.

---

## 29. Definition of successful extraction

For every extraction, the PR must state:

```text
what responsibility moved?
why is the extracted component now allowed to be wrong?
what prevents its bug from falsely authorising?
which adversarial test proves that?
how did trusted LOC change?
```

If the answer is merely “the code is in another module”, reject the architectural claim.

---

## 30. Target end state

Dark Factory may eventually contain a large amount of intelligent, replaceable, fallible software:

- Front Door;
- programme planning;
- Preflight;
- model routing;
- scheduling;
- learning;
- UI;
- cost optimisation;
- replanning.

Underneath it sits a small paranoid authority surface asking:

> **Show me the exact evidence, exact authority, exact policy, exact capability and exact revision that permit this operation.**

The size of the intelligent system is not the problem. The amount of code whose correctness is required to prevent false authorisation is the problem.