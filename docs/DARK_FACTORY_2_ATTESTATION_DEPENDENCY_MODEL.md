# Dark Factory 2.0 — Attestation Dependency and Staleness Model

**Status:** owner-approved target architecture.  
**Purpose:** make proof reuse exact, composable and explainable by defining precisely when evidence remains valid and when it becomes stale.

This document extends `DARK_FACTORY_2_QUALIFICATION_ACCELERATION.md` and `DARK_FACTORY_2_CANONICAL_CONTRACTS.md` §24.

---

## 1. Governing rule

> **Proof is reusable because its dependencies are identical, not because it is recent.**

Dark Factory should never ask:

```text
"Did we run this recently?"
```

when the real question is:

```text
"Are every one of this claim's semantic inputs still identical?"
```

No TTL cache may substitute for dependency identity.

---

## 2. Claim, authority, subject and dependencies

Every blocking proof is represented conceptually as:

```text
Claim
+ Authority
+ Subject
+ Dependency Set
+ Policy
+ Program/Toolchain Identity
→ Attestation
```

An attestation is valid for a requested claim only if:

1. claim type/version matches;
2. authority identity/version satisfies policy;
3. subject binding matches;
4. every declared dependency digest matches;
5. authority/program/toolchain requirements match;
6. result satisfies required verdict;
7. independence constraints match;
8. no live-world requirement demands fresh replay.

---

## 3. Dependency classes

Initial dependency classes:

### SUBJECT_BYTES

Exact candidate tree or declared subset/content digest.

Examples:

- application source;
- acceptance tests;
- changed-file set;
- lockfiles.

### TRUST_ROOT

Protected factory/security/authority bytes and policy surfaces.

Examples:

- kernel;
- security scripts;
- evidence spine;
- detector registry;
- mutation catalogue;
- protected workflows where semantically relevant.

### POLICY

Declarative rules that define validity.

Examples:

- evidence spine policy;
- independence policy;
- capability policy;
- architecture constraints.

### AUTHORITY_PROGRAM

Exact program that produced/verified the evidence.

Includes protected authority revision/hash and relevant command identity.

### TOOLCHAIN

Semantically relevant execution environment:

- interpreter/runtime version;
- dependency lock digests;
- compiler/test tool versions;
- runner/container image where output can depend on it.

Do not include irrelevant environment data merely because it is easy to capture.

### EXTERNAL_STATE

World state not fully represented by repository bytes.

Examples:

- live SaaS/API behaviour;
- browser/network journey;
- production service state.

### PREDECESSOR_ATTESTATION

A composed claim may depend on one or more lower-level attestations by digest.

---

## 4. Replay scopes

Replay scope is a semantic declaration about dependency shape.

### EXACT_TREE

Claim remains valid across commit identity changes if the exact relevant tree bytes, policy, authority and toolchain dependencies remain identical.

### TRUST_ROOT

Claim primarily qualifies one exact protected trust-root state, independently of unrelated product-tree changes.

### LIVE_WORLD

Claim depends on external state and requires a freshness/replay policy defined by that authority. A previous PASS cannot simply transfer because repository bytes are identical.

### COMPOSED

Claim is derived from other attestations plus deterministic composition rules.

`COMPOSED` does not weaken underlying replay requirements.

---

## 5. Claim descriptor

Protected policy should eventually define claim semantics declaratively:

```json
{
  "claim":"application-mutation",
  "version":"2",
  "replay_scope":"EXACT_TREE",
  "required_authority":"application-mutation-authority-v2",
  "dependencies":[
    "subject.application_tree",
    "subject.acceptance_tree",
    "trust.detector_registry",
    "trust.application_mutation_catalogue",
    "policy.mutation_policy",
    "authority.program",
    "toolchain.application"
  ]
}
```

The dependency selector must be protected deterministic code/data, not model output at verification time.

---

## 6. Attestation key

Reusable attestation identity should be content-addressed over canonical dependency material:

```text
AttestationKey = H(
  claim_type + claim_version
  authority_id + authority_version
  subject_binding
  sorted dependency digests
  policy digest
  authority-program digest
  relevant toolchain digest
  independence binding
)
```

The actual artifact also records result, evidence digest, timestamps and provenance.

Timestamp is audit metadata unless the claim's policy explicitly makes time a semantic input.

---

## 7. Dependency manifest

Every attestation should expose an inspectable dependency manifest such as:

```json
{
  "subject":{
    "tree":"sha256:...",
    "revision":"git:..."
  },
  "dependencies":[
    {"name":"trust_root","class":"TRUST_ROOT","digest":"sha256:..."},
    {"name":"detector_registry","class":"POLICY","digest":"sha256:..."},
    {"name":"authority_program","class":"AUTHORITY_PROGRAM","digest":"sha256:..."},
    {"name":"python_toolchain","class":"TOOLCHAIN","digest":"sha256:..."}
  ]
}
```

Do not hide dependency derivation in opaque orchestration logic.

---

## 8. Staleness algorithm

When current state changes:

```text
1. recompute canonical dependency digests affected by the change
2. compare against each candidate attestation dependency manifest
3. mark attestation CURRENT only if every semantic dependency still matches
4. otherwise mark STALE with exact changed dependency names
5. propagate staleness through PREDECESSOR_ATTESTATION composition edges
6. recompute allowed actions from current closure
```

No model judgement is needed.

Output should explain:

```text
attestation att_123 stale because:
  trust_root: abc → def
  authority_program: unchanged
  subject_tree: unchanged
```

This is the core primitive for fast qualification and later project-level reconsideration.

---

## 9. Changed bytes do not automatically stale every proof

A Git commit movement may alter metadata while preserving relevant bytes.

Conversely, a tiny protected-policy change may invalidate a large proof family.

Invalidation follows declared dependencies, not:

- branch name;
- commit recency;
- file count;
- human intuition that a change is “small”.

---

## 10. Proposed initial dependency profiles

These are **target hypotheses to verify against the real commands and files before implementation**.

### Independent RED replay

Likely dependencies:

- exact acceptance-test bytes/hash;
- relevant application baseline/tree;
- RED authority program;
- test toolchain;
- RED policy.

RED may have additional provenance/commit-shape dependencies in the current system; preserve them until explicitly mapped.

### Independent GREEN replay

Likely dependencies:

- exact qualified product tree;
- exact acceptance tests;
- GREEN authority program;
- relevant runtime/test toolchain;
- policy.

### Architecture conformance

Likely dependencies:

- candidate tree/diff;
- architecture policy/contract;
- authority program;
- relevant parser/toolchain.

### Blinded holdouts/certifiers

Likely dependencies:

- exact immutable validation subject;
- protected prompts/method version;
- model identity where required by policy;
- independence binding;
- referenced contract/design/proof hashes.

Do not assume model output transfers across changed model/method identity unless policy explicitly permits it.

---

## 11. `FactoryTrustRootAttestation`

Target dependency set:

```text
trust_root_digest
factory_mutation_catalogue_digest
detector_registry_digest
immunity_registry_digest
mutation_policy_digest
authority_program_digest
toolchain_digest
```

It should *not* depend on unrelated application/product bytes.

Payload includes complete mutation coverage accounting:

```text
defects_total
defects_injected
defects_caught
defects_escaped
defects_not_injected
immunity_result
```

Reuse only on exact dependency match.

A change to README or app component that does not alter any trust-root dependency must not force factory mutation requalification.

A one-line detector-registry change must.

---

## 12. `ApplicationMutationAttestation`

Target dependencies may include:

```text
application_tree_digest
acceptance_tree_digest
application_mutation_catalogue_digest
required detector registry subset digest
mutation policy digest
authority program digest
relevant toolchain digest
```

Do not make it depend on the whole factory trust root unless the actual authority requires that entire surface.

If a protected detector implementation changes, only attestations whose detector dependency includes it should stale where the dependency model can safely be that precise.

Start conservatively; narrow after measurement and adversarial proof.

---

## 13. Detector shard attestations

For trust-root/application mutation sharding, each shard attestation binds:

```text
parent catalogue digest
partition algorithm/version
exact mutation IDs
exact required detector IDs
subject/trust-root digest as applicable
authority/toolchain identity
results per mutation
```

Aggregator proves:

```text
union(shards.mutation_ids) == catalogue.required_mutation_ids
intersection(any two shards.mutation_ids) == ∅
all shard dependency identities identical where required
all required verdicts PASS
```

Missing/duplicate/mixed-version shard means no aggregate PASS.

---

## 14. Generic `AuthorityAttestation`

The generic envelope records:

```text
claim
subject
authority identity
result
dependency manifest
policy identity
independence binding
command/model evidence digest
```

Specific authorities may add structured payloads, but generic verifier should be able to answer:

```text
Is this attestation authentic/structurally valid?
Does it bind this requested claim/subject?
Are dependencies current?
Does it satisfy the required authority/independence class?
```

It should not know the bespoke execution algorithm of every authority.

---

## 15. `ReheadAttestation`

Re-head must not say simply “old evidence still valid”.

It records a transformation and claim-by-claim result:

```json
{
  "old_subject":{},
  "new_base":{},
  "new_subject":{},
  "preserved_claims":[
    {"claim":"...","old_attestation":"att_...","reason":"dependencies-identical"}
  ],
  "reissued_claims":[
    {"claim":"...","new_attestation":"att_..."}
  ],
  "invalidated_claims":[
    {"claim":"...","changed_dependencies":["..."]}
  ]
}
```

Dependencies include:

- old exact subject/provenance;
- new exact base;
- transformed/rebased subject;
- re-head authority program;
- relevant policy;
- immutable RED/acceptance constraints.

A ReheadAttestation is therefore a **staleness/composition certificate**, not a blanket qualification PASS.

---

## 16. `MergeTreeEquivalenceAttestation`

Purpose: transfer EXACT_TREE proof across a byte-identical squash merge.

Bind:

```text
qualified_pr_head_sha
qualified_pr_tree_sha
merge_commit_sha
merge_tree_sha
base/main observation
merge method
merge-verifier program/policy
```

Authority proves:

```text
qualified_pr_tree_sha == merge_tree_sha
```

Then deterministic composition may map EXACT_TREE attestations from qualified PR tree to merged main tree.

This does **not** transfer:

- LIVE_WORLD proof;
- claims bound to PR metadata/head identity rather than tree identity;
- claims whose policy says merge/main context itself is a dependency.

---

## 17. Post-merge world attestation

`PostMergeWorldAttestation` is LIVE_WORLD.

Typical dependencies:

```text
merged main revision/tree
external endpoint/service identity
browser/test program identity
runtime/toolchain
world-observation timestamp/window as defined by policy
```

It is fresh evidence, not a cacheable transfer from pre-merge simply because tree bytes match.

If a live-world failure occurs after merge, record incident/containment; do not pretend earlier tree-pure proof became false. Different claims failed.

---

## 18. Environment identity

Environment dependency must be semantic, not a giant dump of every runner variable.

Define per authority which environment facts can affect verdict.

Possible components:

```text
OS/runner image identity
language runtime version
locked dependency digests
browser version
compiler/linter versions
selected environment variables by name+nonsecret digest where relevant
```

Secret values should never be persisted directly.

If an authority unintentionally depends on undeclared ambient state, that is an authority-design defect to remove or declare.

---

## 19. Model authority identity

For model-based independent authorities, dependency identity may include:

```text
provider/model slug
pinned model revision if available
method/prompt version
input subject hash
visibility/blinding manifest
sampling parameters where semantically relevant
```

If provider serves a mutable model behind an unchanged slug, exact reproducibility may be impossible. Policy should be honest about that uncertainty and decide whether a fresh call is required rather than pretending a content-addressed model binary exists.

Do not fabricate determinism.

---

## 20. Independence binding

An attestation filling an independent slot must record and satisfy structural conditions such as:

- producer role/authority identity;
- candidate write capability absent;
- forbidden prior verdict visibility absent;
- learning-store access absent where blindness requires it;
- subject supplied as immutable data;
- authority program protected from subject.

An `independent=true` string is insufficient.

If independence policy changes, old attestations may stale even if subject bytes do not.

---

## 21. Composition

Claims should compose through deterministic rules in protected policy.

Example:

```text
ApplicationMutationAttestation(product tree X)
+
FactoryTrustRootAttestation(trust root R)
+
subject X proves trust root == R
→ final mutation claim for exact head X
```

Composition artifact binds the digests of all predecessor attestations and composition-policy version.

No orchestrator/model may invent a new composition rule at runtime.

---

## 22. Allowed-actions computation

The kernel should eventually derive:

```text
current attestations
→ validate dependency currency
→ compute claim closure via evidence spine
→ enumerate currently authorised actions
```

Example:

```text
RUN_IMPLEMENTATION allowed if required pre-code claims current
PUBLISH_PR allowed if implementation + local evidence current
MERGE allowed if complete exact-head closure + current external observation
```

This replaces procedural “we reached stage N, therefore do N+1”.

---

## 23. Proof registry, not cache

Call the storage concept a proof/attestation registry, not a cache.

Properties:

- immutable artifacts;
- content-addressed lookup;
- append-only provenance;
- explicit dependency manifests;
- no overwrite of historical evidence;
- current/stale is computed relative to requested subject/policy;
- garbage collection may remove old bytes only under a retention policy that does not break audit requirements.

A stale attestation remains historically true about the old subject/dependencies.

---

## 24. Invalidation examples

### Product source file changes

Likely stale:

- GREEN;
- app tests/static;
- app mutation;
- code holdout;
- architecture checks touching product tree.

Likely reusable:

- exact trust-root mutation attestation if trust-root dependencies unchanged.

### Detector registry changes

Stale:

- mutation attestations depending on changed detector registry/subset;
- trust-root attestation if registry is part of it.

Not automatically stale:

- unrelated product holdout if detector registry is not a dependency.

### Model holdout prompt/method changes

Stale:

- attestations produced under old prompt/method version for that authority slot.

### Main advances with byte-identical squash tree

Potentially transferable:

- EXACT_TREE claims through `MergeTreeEquivalenceAttestation`.

Must replay:

- LIVE_WORLD claims.

### README-only main change during product validation

No claim should stale merely because `main` moved if the changed bytes are outside every semantic dependency — but re-head/merge ancestry constraints may still require a new exact subject or mergeability proof. Distinguish Git integration facts from code-property proof.

---

## 25. Conservative-first rule

Initial dependency profiles may be broader than theoretically necessary.

Narrow only after:

1. actual authority inputs are inventoried;
2. adversarial tests prove removed dependency cannot affect verdict;
3. historical corpus shows no hidden coupling;
4. protected policy is updated explicitly.

Never narrow dependencies merely to improve benchmark numbers.

---

## 26. Adversarial acceptance tests

Must prove at least:

- attestation for tree A cannot satisfy tree B;
- tree-identical commit B can consume EXACT_TREE proof only through valid equivalence rule;
- trust-root A proof cannot qualify trust-root B;
- changed policy stales old proof;
- changed authority program stales old proof where required;
- missing declared dependency refuses;
- unknown dependency class refuses;
- undeclared/mixed mutation shard refuses aggregate;
- duplicate mutation shard coverage refuses;
- LIVE_WORLD proof cannot transfer via tree equality;
- independence-policy change invalidates incompatible old independent attestations;
- model authority artifact bound to wrong subject refuses;
- ReheadAttestation cannot preserve a claim whose dependency changed without reissue;
- composition cannot omit a required predecessor;
- stale proof remains visible historically but cannot authorise current action.

---

## 27. Migration sequence

1. Instrument actual authority inputs and substage dependencies.
2. Create protected claim-descriptor registry for current evidence types.
3. Emit dependency manifests alongside existing evidence without changing verdicts.
4. Build deterministic `is_current(attestation, subject, policy)` verifier.
5. Add stale-reason reporting.
6. Introduce exact trust-root attestation.
7. Introduce application mutation attestation.
8. Compose existing mutation claim from the two.
9. Remove duplicate proof only after composed claim is equivalent and adversarially tested.
10. Add validator attestation fan-out on immutable subject.
11. Implement merge-tree equivalence transfer.
12. Reduce post-merge replay to verified LIVE_WORLD set.
13. Make allowed-actions computation consume current claim closure.

---

## 28. Locked conclusions

1. Evidence validity is dependency identity, never recency.
2. Staleness must explain exactly which dependencies changed.
3. Replay scope is declared per claim and verified against actual authority inputs.
4. Trust-root proof and product-tree proof are separate where semantics permit.
5. Re-head is claim-by-claim preservation/reissue, not blanket carry.
6. Merge tree equality transfers only EXACT_TREE claims.
7. LIVE_WORLD claims replay after merge/current-world change.
8. Composition rules live in protected deterministic policy.
9. The attestation store is immutable historical proof, not a mutable cache.
10. Dependency precision may improve over time, but only after evidence proves narrowing is safe.
