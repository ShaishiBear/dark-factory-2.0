# Dark Factory 2.0 — Qualification Acceleration

**Status:** owner-approved target design; implementation must remain evidence-led.  
**Purpose:** reduce qualification wall-clock without reducing, bypassing or probabilising any existing proof obligation.  
**Governing rule:** **Do not remove proof. Stop recomputing identical proof at the wrong granularity.**

This document supplements `DARK_FACTORY_2_TARGET_ARCHITECTURE_AND_OVERSEER.md`.

---

## 1. Measured baseline

A real PR #134 validation (run `34088776764`) measured approximately:

```text
backend sync                   1.984 s
frontend sync                  0.222 s
security                       1.881 s
provenance peek/fetch          1.085 s

blinded code holdout          99.019 s
architecture holdout         188.496 s
contract certifier            54.844 s
design certifier             256.842 s
governor certifier           180.004 s

five model authorities total 779.205 s

evidence stage              3299.912 s
mutation rung inside it     3000.3 s
factory mutation family     1943.6 s
application family approx   1056.7 s
```

Interpretation:

- mutation work was about 91% of evidence-stage wall time;
- factory mutation qualification alone cost about 32.4 minutes;
- five model authorities cost about 13 minutes serially;
- the failed validation did not even reach outer factory-mutation re-observation, merge, or post-merge full replay.

Do not optimise seconds while repeatedly paying tens of minutes for redundant proof.

---

## 2. Proof obligations that must remain

Acceleration must preserve:

- independent RED replay;
- independent GREEN replay;
- exact acceptance coverage;
- security authority;
- architecture drift/conformance;
- blinded code holdout;
- blinded architecture holdout;
- independent pre-code certification;
- mutation/immunity obligations;
- exact-head evidence;
- exact-tree merge verification;
- post-merge world-sensitive verification.

No optimisation may convert `proved` into `probably still true`.

---

## 3. Reuse proof by dependency identity, never by recency

Do not create a TTL cache.

A reusable attestation must bind at least:

```text
claim
authority
subject digest
dependency digests
policy digest
authority/program digest
relevant toolchain/environment identity
result
```

Proof is reusable only when every dependency that defines that claim is identical.

No fuzzy matching. No "same branch". No "recent enough" substitute for identity.

---

## 4. Classify proof by replay scope

Protected qualification policy should distinguish at least:

### EXACT_TREE
Property depends on exact tree plus declared toolchain/policy.

Likely examples, subject to verified dependency analysis:

- static;
- unit;
- application holdout;
- application mutation;
- architecture drift;
- architecture conformance.

### TRUST_ROOT
Property depends on the exact protected factory trust root.

Likely examples:

- factory/kernel mutations;
- factory immunity.

### LIVE_WORLD
Property can change even when Git bytes do not.

Likely examples:

- browser E2E;
- live external integrations.

Do not blindly adopt these labels. Verify each authority's actual inputs. Hidden external dependencies must either be declared or removed.

---

## 5. FactoryTrustRootAttestation

Factory mutation qualification should converge on a content-addressed attestation for one exact trust-root state.

Conceptual payload:

```text
version
trust_root_digest
factory_mutation_catalogue_digest
detector_registry_digest
immunity_registry_digest
authority_revision
relevant toolchain identity

defects_total
defects_caught
defects_not_injected
defects_escaped

immunity_result
verdict
```

A product PR may consume it only if its protected trust-root digest matches exactly.

If no matching qualified attestation exists: refuse or qualify that trust root.

The final mutation claim remains exact-head bound by composing:

```text
ApplicationMutationAttestation(exact product head/tree)
+
FactoryTrustRootAttestation(exact trust-root digest)
↓
MutationClaim(exact-head final evidence)
```

Do not delete current duplicate factory-mutation execution until this replacement proof exists and is independently verified by the evidence spine.

---

## 6. Detector-specific mutation qualification

The current semantics can award a mutation catch when some unrelated focused test happens to go red.

Target semantics:

```text
mutation injected
↓
its declared responsible detector runs
↓
that detector must go red for the expected semantic reason
```

Introduce a protected detector registry.

Each mutation should reference:

```text
mutation_id
target/anchor
required_detector_ids
required authority/channel class where relevant
```

Detector IDs resolve through protected code to exact commands/tests. Do not place arbitrary executable argv in untrusted mutation data.

The catalogue/compiler must refuse:

- mutation with no required detector;
- unknown detector;
- detector no longer runnable;
- missing catalogue partition;
- duplicate or inconsistent identity.

### Clean-baseline rule

Before injection:

```text
collect unique detector IDs
↓
run each unique detector once clean
↓
all must be green
```

Then each mutation runs only its declared detector set in isolation.

A setup/import/timeout/RuntimeError/non-zero exit unrelated to the expected detector semantics is **not** a valid catch.

This is both faster and stronger than "anything went red".

---

## 7. Shard trust-root mutations

After detector-specific execution exists, trust-root qualification may run across deterministic independent shards.

Each shard attestation binds:

```text
trust_root_digest
catalogue_digest
partition specification
exact mutation IDs evaluated
detector registry digest
result
```

The aggregator must refuse:

- missing mutation;
- duplicate mutation;
- wrong catalogue;
- wrong trust-root digest;
- mixed authority versions;
- uninjected mutation;
- escaped mutation.

Start conservatively and measure hosted-runner economics.

---

## 8. Application mutations

Move application mutations toward independent isolated copies/worktrees.

Each defect should declare the authoritative detector/channel requirements that must catch it, e.g.:

- focused unit;
- focused static/type;
- blinded holdout;
- citation holdout;
- security.

Do not require an expensive whole quick gate for every mutation where a narrower authoritative detector proves the required failure property.

Keep broader all-channel coverage as periodic analytics if useful; do not confuse diagnostic coverage with the minimum blocking authority needed for a known defect.

---

## 9. Fan out independent model authorities

After deterministic prechecks and provenance create one immutable validation subject, run independently and concurrently where safe:

- blinded code holdout;
- architecture holdout;
- contract certifier;
- design certifier;
- governor certifier.

No authority may see another authority's verdict.

All bind to the same immutable subject identity as applicable:

```text
pr
issue
base_sha
head_sha
diff_sha256
changed_files
builder provenance hash
contract/context/design/governor/proof hashes
architecture-policy hash
```

Join deterministically. Missing, failed, wrong-head or wrong-authority evidence means refusal.

Benchmark validator concurrency 1/2/3/5 for wall time, cost, throttling and reliability. Do not change models merely to make the benchmark look better.

---

## 10. Parallelise read-only deterministic branches only after mutation work

After the main mutation bottleneck is reduced, benchmark safe concurrency for:

- backend/frontend/factory unit suites;
- independent static tool groups;
- application holdout versus unrelated runtime validation work.

Do not create CPU contention that makes the 4-vCPU runner slower.

Preserve exact test counts and clean checkout semantics.

---

## 11. Preserve fresh RED/GREEN replay

Do not cache away the first independent RED/GREEN validation for an exact subject.

Builder RED/GREEN is not enough.

Proof reuse begins only after an independent authority has established the property for the exact bound subject.

---

## 12. Transfer tree-pure proof across a byte-identical merge

`merge_verify.py` already proves the authorised PR-head tree is byte-identical to the actual squash-merge tree.

Formalise this as a `MergeTreeEquivalenceAttestation` binding:

```text
qualified_pr_head
qualified_pr_tree
merge_commit
merge_tree
qualified_pr_tree == merge_tree
```

For claims classified EXACT_TREE:

```text
pre-merge qualification
+
verified exact tree equivalence
=
valid proof for those same bytes now on main
```

Do not rerun static, unit, tree-pure holdouts, application mutations or factory mutations merely because Git assigned the identical tree a different commit identity.

Keep the original RED/provenance chain attached to the qualified PR head. The merge-equivalence evidence links that qualified tree to the merged result.

---

## 13. Post-merge replay only what is world-sensitive

Keep a fresh post-merge live-world sentinel, initially something like:

```text
locked dependency environment
↓
exact merged main
↓
browser/E2E live-world journey
↓
recheck main identity where required
↓
POST_MERGE_VERIFIED
```

If other claims genuinely depend on live external state, include them.

A post-merge live-world failure remains serious: incident/stop/escalation and safe revert where mechanically valid.

Daily/full-main regression remains useful for environment drift, runner changes and latent systemic failures. It is not a reason to re-prove identical tree-pure properties minutes after merge.

---

## 14. Instrument the evidence ladder before micro-optimising it

Record substage timings for at least:

- head/trust-root verification;
- attached evidence validation;
- contract validation;
- security recomputation;
- proof validation;
- independent RED replay;
- independent GREEN replay;
- architecture guard/conformance;
- pre-mutation harness;
- application mutations;
- factory trust-root attestation/fallback;
- spine closure;
- provenance fetch.

Also time static/unit subcommands separately.

Timing is observability only; it must never influence verdicts.

Leave cheap defence-in-depth duplication alone until the tens-of-minutes bottlenecks are gone.

---

## 15. Required adversarial tests

The accelerated architecture is incomplete until it refuses at least:

- factory attestation for trust-root A used with trust-root B;
- wrong mutation catalogue digest;
- missing mutation shard;
- duplicate mutation across shards;
- mutation whose declared detector stays green;
- mutation caught only by an unrelated detector;
- detector registry silently deleting a required detector;
- old authority version replayed after authority/policy change;
- EXACT_TREE proof transferred to a different tree;
- LIVE_WORLD proof transferred without replay;
- post-merge tree mismatch;
- missing parallel authority;
- authority artifact bound to a different head;
- one independent authority seeing another's verdict.

---

## 16. Execution order

Use evidence to adjust this order, but the preferred sequence is:

1. qualification substage telemetry;
2. explicit detector schema/registry;
3. detector-registry adversarial tests;
4. detector-specific factory mutation execution;
5. isolated/parallel application mutation execution;
6. exact trust-root qualification attestation;
7. consume that attestation in product evidence;
8. remove duplicate outer factory-mutation execution;
9. validator-authority fan-out;
10. replay-scope classification;
11. merge tree-equivalence attestation;
12. reduced post-merge live-world replay;
13. measured unit/static fan-out;
14. workflow-level mutation sharding;
15. remeasure the full qualification path;
16. tighten budgets from observations.

Do not optimise theoretical later phases before measuring the new critical path.

---

## 17. Success metrics

Track:

```text
PR validation wall
post-merge wall
full qualification wall

application mutation wall
trust-root mutation wall

judge/certifier wall
judge/certifier summed compute

model cost
runner minutes
qualification refusal reasons
```

Track both wall-clock and total compute. Parallelism may reduce only the former; proof reuse should reduce both.

Engineering objective, without weakening evidence:

```text
TARGET:  PR-ready → post-merge verified < 25 minutes
STRETCH: PR-ready → post-merge verified < 15 minutes
```

If proof cannot safely meet that target, keep the proof and report the real bottleneck.

---

## 18. Stabilisation discipline before another expensive full run

Until the qualifier itself is stable, a full end-to-end product validation is not the preferred debugging instrument.

Before launching an expensive full qualification after a harness/trust-root repair, prove locally or in the smallest production-faithful environment:

```text
clean baseline GREEN
↓
apply the intended defect
↓
responsible detector RED
↓
RED is for the expected semantic reason
↓
restore
↓
clean baseline GREEN again
```

Do not count generic process failure as mutation evidence.

Run all cheap deterministic invalidators before model judges.

Before any expensive action, state:

1. what uncertainty it resolves;
2. why cheaper evidence is insufficient;
3. expected wall/cost class;
4. what action follows each possible result.

If those cannot be answered, do not launch it.

---

## 19. Central design rule

Dark Factory should move from:

> "Run the whole giant qualification programme again."

Toward:

> **"Which exact claims became stale because which exact dependencies changed?"**

Then execute only the authorities whose proof is actually invalidated.

This is not merely performance work. It is the first production use of the dependency-aware evidence architecture also needed later for programme replanning, Project Decision Graph invalidation, Preflight calibration, learning and architectural reconsideration.
