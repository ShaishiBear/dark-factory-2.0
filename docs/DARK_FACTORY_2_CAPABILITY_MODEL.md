# Dark Factory 2.0 — Capability Model and Privileged Broker Semantics

**Status:** owner-approved target architecture.  
**Purpose:** make privilege explicit, narrow, revocable and independently enforceable so intelligence can grow without growing authority.

This document extends `DARK_FACTORY_2_CANONICAL_CONTRACTS.md` §25 and `DARK_FACTORY_2_KERNEL_TCB_MIGRATION.md`. It does not replace current proven credential, Git, worktree or merge controls until equivalent enforcement is demonstrated.

---

## 1. Constitutional rule

> **A component may request privilege; it must not gain privilege merely because it knows how to ask for it.**

Dark Factory must move from ambient authority:

```text
process has repo + shell + credentials
→ prompt says what it should do
```

toward explicit authority:

```text
actor requests operation
→ kernel/policy checks current evidence + subject + lease
→ narrow capability is issued
→ broker constructs and performs exact operation
→ independent observation records actual result
```

A capability is an authorisation object, not a credential string and not a prompt instruction.

---

## 2. Trust split

### Untrusted requesters

May include:

- orchestrator;
- planner;
- provider adapter;
- model worker;
- project/preflight services;
- ordinary GitHub workflow adapter.

They may choose *what they would like to happen*. They do not decide whether privileged operations are allowed.

### Trusted kernel/policy

Decides whether a requested capability is legal for the exact subject and current evidence state.

### Enforcement brokers

Hold or obtain the underlying privilege and construct fixed operations. A broker is trusted for enforcement, but should contain as little policy as possible.

Examples:

- WorkerViewBroker;
- CredentialBroker;
- GitMutationBroker;
- GitHubMutationBroker;
- MergeBroker.

### Independent observers/authorities

Re-observe consequential results where a broker defect could otherwise falsely report success.

---

## 3. Capability grammar

Capabilities should be named as stable semantic operations, not arbitrary commands.

Initial registry:

### Repository / workspace

```text
repo.read
workspace.materialize
workspace.read
workspace.write
workspace.import-diff
workspace.delete
```

### Git

```text
git.branch.create
git.branch.update
git.commit.create
git.tag.create
git.read-history
```

Do not expose generic `git.exec` as a capability.

### GitHub

```text
github.observe
github.issue.comment
github.issue.label
github.pr.create
github.pr.update
github.branch.push
github.merge.exact-head
```

`github.merge.exact-head` is a separate high-authority capability, never implied by `github.pr.update`.

### Credentials / network

```text
credential.model.invoke
credential.github.mutation
credential.external-service.use
network.egress
```

Credential capabilities should normally be consumed indirectly by a broker. Workers should receive service access, not reusable master secrets, where technically feasible.

### Authority execution

```text
authority.run
rehead.apply
qualification.consume-attestation
```

Authority execution does not grant permission to forge authority output.

---

## 4. Capability Grant v2

Extend the existing `CapabilityGrant` shape toward an operation-bound envelope:

```json
{
  "schema":"dark-factory/capability-grant",
  "schema_version":"2.0",
  "capability_id":"cap_...",
  "operation":"github.merge.exact-head",
  "actor":{"role":"merge-broker","instance_id":"broker_..."},
  "subject":{
    "repo":"ShaishiBear/dark-factory-2.0",
    "issue":184,
    "pr":134,
    "base_sha":"...",
    "head_sha":"...",
    "tree_sha":"..."
  },
  "resource":{
    "type":"pull-request",
    "id":"134"
  },
  "lease":{
    "lease_id":"lease_...",
    "generation":7
  },
  "constraints":{
    "allowed_paths":[],
    "immutable_paths":[],
    "merge_method":"squash",
    "network_destinations":["api.github.com"]
  },
  "credential_refs":["credref_github_app_installation"],
  "issued_at":"...",
  "expires_at":"...",
  "max_uses":1,
  "policy_sha256":"...",
  "evidence_closure_sha256":"..."
}
```

Trust-critical fields must not be inferred by the broker from mutable branch names or ambient process state.

---

## 5. Capability request and decision

Request:

```json
{
  "request_id":"capreq_...",
  "requested_operation":"workspace.import-diff",
  "actor":{},
  "subject":{},
  "resource":{},
  "lease_ref":{"lease_id":"lease_...","generation":4},
  "proposed_constraints":{},
  "evidence_refs":[]
}
```

The kernel/policy returns either:

```text
ISSUE(capability)
REFUSE(reason-code)
```

Never silently broaden a request. If requested scope is too wide, either issue an explicitly narrower capability whose semantics the caller accepts or refuse and require a new request.

---

## 6. Capabilities are non-amplifying

A holder cannot mint broader authority.

If delegation is ever needed, child capability must satisfy:

```text
child.operations ⊆ parent.operations
child.resources ⊆ parent.resources
child.paths ⊆ parent.paths
child.network ⊆ parent.network
child.credentials ⊆ parent.credentials
child.expiry <= parent.expiry
child.max_uses <= parent.remaining_uses
```

The kernel, not an untrusted holder, validates this relation.

Default: **no delegation**.

---

## 7. Subject binding

Every consequential capability binds the facts whose movement would make the operation unsafe.

Examples:

### Worker write

Bind:

- run ID;
- canonical starting revision;
- worker-view digest;
- allowed write paths;
- immutable/blind paths;
- lease ID + generation.

### Branch push

Bind:

- repository;
- branch name;
- expected current remote SHA where relevant;
- new commit SHA;
- operation purpose.

### PR create/update

Bind:

- repository;
- expected branch/head;
- intended base branch;
- exact issue/run identity.

### Merge

Bind at least:

- PR number;
- exact expected head SHA;
- qualified tree SHA;
- permitted merge method;
- valid MergeAuthorization/evidence closure;
- current lease generation where merge resource is leased.

If any bound fact differs at execution time: REFUSE.

---

## 8. Lease fencing is part of capability validity

For leased resources, capability validity includes `(lease_id, generation)`.

A capability issued under generation N becomes unusable once generation N+1 is current, even if:

- its wall-clock expiry has not passed;
- the old executor is still alive;
- its underlying credential still works.

Broker must re-read authoritative lease generation immediately before consequential mutation.

TTL alone is not fencing.

---

## 9. One-shot versus stage capabilities

Prefer the narrowest useful lifetime.

### One-shot

Use for:

- exact-head merge;
- final import of worker diff;
- destructive cleanup;
- protected branch/revision mutation.

`max_uses=1`; consume atomically with operation start or successful compare-and-swap according to broker design.

### Stage-bound

May be used for bounded repeated operations such as worker writes within an isolated worker view.

Stage capability still binds:

- run;
- stage;
- worker instance;
- resource;
- expiry;
- lease generation.

Do not issue project-long or repository-wide mutation capabilities merely for convenience.

---

## 10. Credentials are not capabilities

A GitHub App token, model API key or database password is an implementation mechanism.

The capability says:

> `github.pr.create` for repo X, branch Y, head Z.

The broker may internally obtain/use a short-lived GitHub App installation token to execute it.

Do not expose raw credential material in:

- project graph;
- trajectory text;
- model prompts;
- worker environment unless absolutely required;
- generic orchestration logs.

Use stable credential references in capability metadata, never secret values.

The current `DARK_FACTORY_APP_TOKEN` implementation is a transitional capability-scoped credential and should evolve toward broker-local acquisition/consumption rather than global ambient environment.

---

## 11. WorkerViewBroker

Target execution flow:

```text
canonical worktree
    ↓ verify exact revision + clean state
CapabilityGrant
    ↓
materialise restricted worker view
    - allowed reads present
    - blind/trust-root paths absent
    - only allowed writes mutable
    ↓
provider/model works in view
    ↓
broker computes exact resulting diff
    ↓
re-derive changed paths/content
    ↓
validate against capability
    ↓
import authorised diff into canonical worktree
    ↓
independent tests/authorities judge canonical result
```

Critical invariant:

> The worker must not be trusted to report what it read or changed.

The broker derives those facts from the actual filesystem boundary.

Implementation may use copy, overlay, container, mount namespace or another mechanism; benchmark before locking the mechanism. The semantic contract is independent of mechanism.

---

## 12. Filesystem policy

A workspace capability distinguishes:

- visible read paths;
- writable paths;
- immutable visible paths;
- blind/absent paths.

`read but do not change` and `cannot see` are different properties.

Glob expansion should be performed by protected deterministic code against the canonical tree. Reject ambiguous path traversal, symlink escape and case-normalisation surprises.

Before import, re-derive:

```text
added files
modified files
deleted files
renames if semantically relevant
symlink targets
file modes
```

Anything outside envelope: REFUSE whole import.

---

## 13. Network policy

Default worker network posture should be deny unless stage requires it.

A network capability specifies semantic destinations where enforceable:

```json
{
  "allowed":true,
  "destinations":[
    {"service":"openrouter","host":"openrouter.ai","ports":[443]}
  ]
}
```

Do not treat prompt instructions such as “only access X” as enforcement.

DNS rebinding, redirects and proxy escape must be considered by the eventual enforcement mechanism.

Where provider invocation occurs outside the worker sandbox, prefer keeping model credential/network power in the provider service and exposing only the model interaction channel to the worker.

---

## 14. GitMutationBroker

Broker API should be semantic, e.g.:

```text
create_commit(capability, exact_diff)
push_branch(capability, commit_sha)
```

Not:

```text
run_git(capability, argv)
```

Broker rechecks:

- current worktree/repo identity;
- actual dirty set;
- expected base;
- immutable hashes;
- lease generation;
- intended commit tree.

The orchestrator cannot make an illegal change legal by describing the dirty set incorrectly.

---

## 15. GitHubMutationBroker

Keep ordinary observation separate from mutation.

Target semantic methods:

```text
create_pr(subject, capability)
update_pr(subject, capability)
push_branch(subject, capability)
merge_exact_head(merge_authorization, capability)
```

The current dedicated GitHub App identity is the correct underlying mutation identity because it triggers normal GitHub event semantics while remaining short-lived and repository scoped.

The broker should obtain/use that identity only for the bounded operation. Reads may continue using ordinary read-only Actions identity where sufficient.

---

## 16. Merge is a special capability

Merge is the most privileged normal operation.

Target chain:

```text
exact subject
+ complete current evidence closure
+ current independent GitHub/revision observation
        ↓
MergeAuthorization
        ↓
one-shot github.merge.exact-head capability
        ↓
MergeBroker
        ↓
GitHub expected-head compare-and-swap
        ↓
independent merged-tree observation
```

Merge broker must not accept arbitrary PR/head/method values independent of the signed/hashed authorisation envelope.

A successful API response is not sufficient proof that the resulting main tree is the authorised tree; post-operation observation remains required.

---

## 17. Revocation

Capabilities become invalid when any of these occurs:

- expiry;
- lease generation changes;
- subject revision changes;
- policy version changes where bound;
- explicit stop/emergency state;
- resource is completed/superseded;
- capability is consumed;
- associated evidence closure is invalidated.

Do not rely solely on deleting an in-memory token.

The broker verifies current revocation/fencing state at use time.

---

## 18. Execution receipt

Every consequential broker operation emits an immutable receipt:

```json
{
  "schema":"dark-factory/capability-execution-receipt",
  "schema_version":"1.0",
  "receipt_id":"receipt_...",
  "capability_id":"cap_...",
  "operation":"github.merge.exact-head",
  "subject_before":{},
  "requested_effect":{},
  "observed_result":{},
  "broker_version":"...",
  "started_at":"...",
  "finished_at":"...",
  "result":"success"
}
```

Receipt is audit evidence, not proof that the operation was safe. Safety came from authorisation + enforcement + independent verification.

---

## 19. Failure semantics

Use explicit reason classes:

```text
CAPABILITY_REFUSED_POLICY
CAPABILITY_REFUSED_STALE_SUBJECT
CAPABILITY_REFUSED_STALE_LEASE
CAPABILITY_REFUSED_EXPIRED
CAPABILITY_REFUSED_SCOPE
CAPABILITY_REFUSED_STOPPED
BROKER_EXECUTION_TRANSIENT
BROKER_EXECUTION_PERMANENT
BROKER_RESULT_MISMATCH
```

Do not collapse all failures into retryable process errors.

A policy refusal is not fixed by retrying the same operation repeatedly.

---

## 20. Minimum capability principle

Before granting a capability ask deterministically:

1. What exact operation must occur?
2. What exact resource does it affect?
3. Which bytes/revision must it bind?
4. Does it require read, mutation or credential power?
5. Can the operation be performed by a broker without exposing the underlying secret/tool to the requester?
6. Can duration or uses be reduced?
7. Which current evidence/state makes it legal?
8. Which fact changing should revoke it?

This is a compiler problem, not a model judgement problem.

---

## 21. Adversarial acceptance tests

The capability architecture is not implemented until tests prove at least:

- worker cannot see a blind path even if provider asks for it;
- worker cannot write outside allowed paths;
- symlink/path traversal cannot escape workspace scope;
- provider cannot inject a broader capability;
- child capability cannot exceed parent;
- expired capability refuses;
- stale lease generation refuses;
- head-A merge capability cannot merge head B;
- capability for repo A cannot operate on repo B;
- capability for PR A cannot operate on PR B;
- GitHub mutation broker cannot silently fall back to `GITHUB_TOKEN` where App identity is required;
- model worker receives no GitHub mutation credential;
- orchestrator lying about dirty files is caught by broker re-derivation;
- emergency stop invalidates privileged mutation;
- replay of consumed one-shot capability refuses;
- broker reporting success while observed result differs causes containment/refusal.

---

## 22. Migration sequence

Do not flag-day replace current enforcement.

1. Inventory every credential and privileged operation currently reachable from `runtime.py`, provider code and Git/GitHub adapters.
2. Assign each one a semantic capability name.
3. Mark which current code path actually enforces it.
4. Separate read/observe from mutate.
5. Introduce capability request/decision objects around current paths without changing behaviour.
6. Add lease-generation binding to consequential capabilities.
7. Move GitHub App acquisition/use behind GitHubMutationBroker.
8. Introduce provider-neutral WorkerViewBroker for one low-risk stage first.
9. Prove blind/read/write enforcement adversarially.
10. Expand worker-view containment across stages.
11. Move Git commit/import mechanics behind GitMutationBroker.
12. Reduce ambient credentials and shell privileges only after equivalent broker paths are live.
13. Measure TCB/privileged surface before and after.

---

## 23. Locked conclusions

1. Capabilities are semantic authorisations, not credentials.
2. Requesters do not enforce their own permissions.
3. Privileged brokers construct fixed operations rather than accepting arbitrary shell/Git/GitHub commands.
4. Subject revision and lease generation are first-class capability bindings.
5. Merge authority is one-shot, exact-head and independently re-observed.
6. Credentials remain broker-local wherever possible.
7. Worker containment is enforced by actual visible/writable surfaces, not prompting.
8. Capability delegation is off by default and can only narrow authority.
9. Revocation is semantic/fenced, not merely token deletion.
10. The target is fewer ambient powers even as the untrusted intelligence layer becomes more capable.
