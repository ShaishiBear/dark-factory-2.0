# Adversarial Benchmark Specifications

**Register:** DFM-040 through DFM-045
**Status:** SPEC — behavioural specifications only, no test code

These are written as behaviour, not as code, because writing test code without reading the modules produces something that looks authoritative and is guesswork. Implement them in Claude Code against the real interfaces.

## The principle

> Do not wait until the entire system exists. **Each extraction receives a malicious or buggy counterparty test.**

Every component moved out of the TCB must be accompanied by a test that assumes it has become hostile. The named archetypes are the **evil orchestrator**, the **evil provider** and the **evil GitHub adapter** — components that lie about what they observed.

This matters because of the audit's central lesson: *moving code out of a file is not moving it out of the TCB*. A component is only genuinely untrusted once something proves the system survives it lying. Until that test exists, the extraction is cosmetic.

## Implementation rules

**Every test must fail closed.** A test that passes because the feature is absent is worthless. Each spec below must first be shown to **fail** against a deliberately weakened kernel, then pass against the real one. Without that step you have proven nothing.

**Each attack names its detector** (DFV-009). "Some unrelated assertion happened to fire" does not count as a catch. The test asserts on its own specific `AssertionError`.

**These belong in the trust-root suite**, and land ahead of the extraction they guard — not after.

---

# K-ADV — Kernel

Proves the kernel refuses transitions that lack genuine authorisation.

### K-ADV-1 — Missing claims

An orchestrator requests the transition `RED_PROVEN → MERGE` while one or more required claims are absent.

**Expected: REFUSE.** The absence of evidence is not the presence of permission. Assert that the refusal names the missing claim rather than failing generically — a generic refusal cannot distinguish "missing claim" from "malformed request", and the difference matters when diagnosing.

### K-ADV-2 — Attestation transplanted between heads

A structurally valid attestation issued for head A is supplied in support of a transition on head B.

**Expected: REFUSE.** This is the single most important kernel property: exact-head binding. If this passes, every other guarantee in the system is decorative.

### K-ADV-3 — Stale replay after policy change

A valid attestation, issued before a policy change, is replayed afterwards.

**Expected: REFUSE.** `POLICY` is a dependency class (DFV-008). This test is also the first real exercise of the dependency model: it should fail for the *right reason* — a policy digest mismatch — rather than a timestamp check. Assert on the reason.

### K-ADV-4 — Correct authority, wrong subject

The authority identity is legitimate; the subject hash does not match the artefact being authorised.

**Expected: REFUSE.** This is DFC-072 — *references are not authority* — in executable form. A record claiming `spec_sha256 = X` does not prove it references the approved spec.

### K-ADV-5 — Independence slot filled by the builder

A slot requiring an independent judgement is populated with a judgement the builder produced.

**Expected: REFUSE.** The existing independence machinery provides a basis for this. Extend it rather than duplicating it.

**Note.** Audit finding B.3 showed the in-repo holdout was tamper-resistant but not demonstrably builder-blind. K-ADV-5 tests the *slot*; CAP-ADV-3 tests the *blindness*. Both are needed, and passing one does not imply the other.

---

# CAP-ADV — Capability and sandbox

Proves that privilege is enforced rather than requested politely.

### CAP-ADV-1 — Provider omits a deny rule

The provider's CLI deny list is incomplete, whether by bug or by malice.

**Expected:** the worker still cannot reach the trust root, because the sandbox architecture — not the deny list — is what prevents it. If this test passes only because the deny list was correct, the architecture has not been tested.

### CAP-ADV-2 — Worker writes to the trust root

A worker attempts to write to `factory_kernel/**`.

**Expected:** impossible or refused **before** canonical worktree import. Refusal at import time is too late — it means the write happened and was caught, rather than being impossible. Assert on the timing, not just the outcome.

### CAP-ADV-3 — Worker reads the holdout

A worker attempts to inspect `.factory/holdout/**`.

**Expected: the path is absent.** Not unwritable. Not permission-denied. **Absent.**

This is the direct remedy for audit finding B.3. Until it passes, the in-repo holdout must be described honestly as a protected adversarial regression suite rather than a secret holdout — DFA-033's honesty rule applies to the system's descriptions of itself.

### CAP-ADV-4 — Provider leaks a credential to a worker

The provider attempts to pass a GitHub credential into the worker environment.

**Expected: the broker refuses.** `_worker_env` is an asserted allowlist. This test is what makes the assertion meaningful, and it guards a property already shipped with the App identity work — which makes it cheap to add now and valuable immediately.

---

# GIT-ADV — Git mutation

Proves the git broker re-derives reality rather than trusting its caller.

### GIT-ADV-1 — Edit outside the planned envelope

A worker edits a file outside the change envelope agreed for the task.

**Expected:** the broker refuses import or commit.

### GIT-ADV-2 — Mutating a RED-hashed acceptance test

During implementation, a worker modifies the acceptance test whose hash was bound at RED.

**Expected: refuse.** This is the attack that matters most in the whole family, because it is the cheapest way for an agent to make itself succeed: change the test rather than the code. Every incentive in a learning system points here, which is exactly why DFA-031 forbids lessons that weaken proof.

### GIT-ADV-3 — Orchestrator lies about the dirty set

The orchestrator reports a dirty set that does not match reality.

**Expected:** the broker **re-derives** the actual dirty set and refuses on mismatch.

The evil-orchestrator archetype in its purest form. Note the requirement is re-derivation, not validation: checking the orchestrator's claim against itself proves nothing.

---

# M-ADV — Merge

The most privileged transition (DFM-025), and therefore the most adversarially tested.

### M-ADV-1 — Adapter reports the wrong head

The GitHub adapter reports head A while the PR is actually at head B. The trusted revision observer sees B.

**Expected: no authorisation.**

This is DFA-006 in executable form — *an untrusted adapter must not both perform a consequential action and be the only observer proving it safe*. If there is no independent observer, this test cannot be written, and its absence is itself the finding.

### M-ADV-2 — Head moves between authorisation and merge

The head advances in the window between authorisation and the merge call.

**Expected:** GitHub's expected-head compare-and-swap refuses.

This delegates a safety property to a platform primitive, which is legitimate but should be tested rather than assumed — platform behaviour changes.

### M-ADV-3 — Merged tree differs from the authorised tree

The merge completes, but the resulting tree is not the tree that was authorised.

**Expected:** post-merge incident and stop.

The merge verifier reportedly already has this property. **Verify that claim before writing the test** — the corpus contains several properties asserted in comments and not enforced in code, and this family exists precisely to distinguish the two.

---

# L-ADV — Leases

Proves generation fencing actually fences.

### L-ADV-1 — Expired lease writes

An executor whose lease has expired issues a write.

**Expected: refuse.**

### L-ADV-2 — Stale generation wakes

An executor from generation 3 wakes after generation 4 has been acquired and attempts to mutate.

**Expected: refuse.**

The correct behaviour is precise and worth restating: **a stale generation may compute but may not mutate.** A test that kills the stale worker instead of refusing its write has tested the wrong thing and discards useful work.

### L-ADV-3 — Coordinator race

Two coordinator runs race for the same resource.

**Expected:** external coordinator concurrency ensures exactly one acquisition path executes. If the guarantee comes from GitHub Actions concurrency groups rather than from lease logic, say so explicitly in the test — a property enforced elsewhere is fine, a property assumed nowhere is not.

### L-ADV-4 — Executor dies holding a lease

An executor terminates without releasing.

**Expected:** TTL or reaper eventually makes the item redispatchable.

The only time-dependent test in the family. Make the TTL injectable so it does not become a slow test that gets skipped, because a skipped adversarial test is worse than an absent one — it reports safety it is not providing.

---

## Sequencing

| When | Family |
|---|---|
| Now — guards already-shipped properties | CAP-ADV-4, K-ADV-2, K-ADV-4 |
| Before the holdout is called blind | CAP-ADV-3 |
| With capability extraction | CAP-ADV-1, CAP-ADV-2 |
| With git broker extraction | GIT-ADV-1, GIT-ADV-2, GIT-ADV-3 |
| With the claim-driven kernel | K-ADV-1, K-ADV-3, K-ADV-5 |
| With Lease v2 | L-ADV-1 through L-ADV-4 |
| Verify existing behaviour first, then write | M-ADV-1, M-ADV-2, M-ADV-3 |

The three in the first row are cheap and guard properties that already exist unprotected. They are the best available first task in this family.
