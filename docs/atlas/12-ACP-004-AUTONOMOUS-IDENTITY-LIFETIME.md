# ACP-004 — The Autonomous Identity Is Minted Once and Spent for Ninety-Five Minutes

**Tier:** 2 (trust boundary — the credential broker)
**Status:** DRAFT
**Register:** proposed as `DFE-018`
**Relates to:** DFV-005 (App identity) · DFM-015 (`credential_env.py`) · DFA-007 (capability model) · DFE-014 (refusal attribution, which hid this for four days)
**Raised:** 2026-09-09

---

## 1. Summary

The GitHub App installation token is minted **once per job** and read **per operation**. Installation tokens live 60 minutes. The three operations that spend the identity are spread across up to 95 minutes of a single job, and the last of them — the exact-head merge — is by construction the latest.

The result is that **no autonomous PR can merge**: the credential authorising the merge expires before the validation that earns it finishes.

This is a missing re-mint and absent expiry handling. It is not a tension between two correct rules, and it does not require trading any guarantee away. Re-minting costs nothing and weakens nothing — the capability stays narrow, scoped, one-shot and fallback-free. The fix is bounded; the Tier 2 label is because the change lands in the credential broker, not because the design is in question.

---

## 2. The defect, precisely

`_autonomous_identity()` (`factory_kernel/github_cli.py:38`) calls `scoped_environment(scope="github-mutation")`, which reads `os.environ[DARK_FACTORY_APP_TOKEN]`. That value is a single static string injected once at `dark-factory-worker.yml:424`/`439` from `steps.factory_identity.outputs.token`, minted by `actions/create-github-app-token` in the job's second step. Every call re-reads the same string.

The kernel holds no private key and has no minting path. There is **no re-mint and no 401 handling** anywhere in `github_cli.py` or `credential_env.py`.

Three operations spend the identity — `push_branch`, `create_pr`, `merge_squash` — and all three age from one clock.

### Measured

Build run `34061371205`, which opened PR #134:

```
21:32:13   job start; token minted at step 2
22:28:40   push_branch / create_pr        +56m27s   → 3m33s of credential life remaining
```

Validation run `34151427980`, on the same PR:

```
18:23:27   job start; token minted at step 2
           FACTORY_STAGE kind=exec name=evidence   seconds=4970.989  outcome=ok
           FACTORY_STAGE kind=exec name=merge-pre  seconds=0.708     outcome=ok
19:58:05   merge_squash                    +94m38s  → 401 Bad credentials
```

The full harness passed. The evidence spine closed. Merge pre-authorization passed. The only thing that failed was spending a credential that was 35 minutes dead.

### The margin is not merely thin at the merge

`create_pr` came within **three minutes thirty-three seconds** of failing on that same build. `test_author` alone burned 788 s and hit `cap_reached=true`, then took another 96 s on a retry. One slower model stage and the build would have died at PR creation instead, leaving a pushed branch with no PR.

So this is a credential-lifetime defect that happened to surface at the last spend point. It is not a merge defect, and fixing only the merge would leave a live fault two spend points upstream.

### Why fail-closed made it worse, correctly

`scope="github-mutation"` deliberately strips `GH_TOKEN` and `GITHUB_TOKEN` so an absent App token raises rather than quietly authenticating as Actions and opening a PR that can never be judged. That design is right and stays. It simply had nothing to say about a credential that was present and expired.

---

## 3. What this ACP rejects

**Giving the kernel the App private key so it can mint on demand.** That places a long-lived signing key in the same process environment as model-adjacent code, to repair a lifetime bug. It widens the TCB to fix a clock. Rejected.

This is the one place the narrow framing has to hold: the defect is that a token was reused for 95 minutes, not that minting lives in the wrong place.

---

## 4. Recommended change

`actions/create-github-app-token` remains the only holder of the private key. The operations that spend the identity move to step boundaries, where the action can be invoked again.

**The split is the fix. The margin is the guardrail. The margin cannot substitute for the split.**

At a 20-minute margin and nothing else, an 83-minute validation refuses at minute 20 instead of failing at minute 95. That is a legible failure rather than a baffling one, and the queue is just as dead. The evidence stage alone was measured at 4970.989 s. Items 1 and 2 are **required**; items 3 and 4 are required *as well*, not *instead*.

1. **REQUIRED — split the merge out of the dispatch step** into its own workflow step in the same job — so the worktree, artifacts and lease persist — preceded by its own `create-github-app-token` invocation. The kernel gains a `merge --from-authorization <path>` entry point.
2. **REQUIRED — give the build's push/PR handoff the same treatment.** At 56m27s observed, the build phase demonstrably can exceed the window; it is not a hypothetical.
3. **A distinct reason code `identity_expired`**, raised loudly, replacing the generic `RuntimeError` out of `run_as_app`.
4. **Refuse before spending, not after.** The workflow exports the mint timestamp; the kernel refuses to spend an identity older than the margin in §5 with a clear message, rather than discovering the fault at the API.

Items 3 and 4 must not ship before DFE-014. They introduce a new refusal path, and a refusal that misattributes is exactly how this defect cost four days. Do not ship a new refusal into a classifier known to name the wrong authority.

### Why this is a repair and not a redesign

The exact-head guarantee is already artifact-mediated. `harness/merge_verify.py pre` writes `merge-authorization.json` binding base, head, tree and evidence hash; the post check re-verifies against the actual merge commit. A separate merge step consumes that artifact and re-verifies exact head at merge time.

Nothing about the guarantee depends on the merge running in the same process as the validation — only on it running against the same authorization. The split therefore costs nothing and weakens nothing. The property was made artifact-mediated before anyone needed it to be, which is why this fix is cheap.

**Scope:** `factory_kernel/credential_env.py`, `factory_kernel/github_cli.py`, `factory_kernel/refusal.py`, `.github/workflows/dark-factory-worker.yml`.

---

## 5. The refusal margin

The margin must be set against **observed spend-point ages**, not against the 60-minute limit. The observed distribution is the argument:

| Spend point | Observed age at spend | Remaining |
|---|---|---|
| `create_pr` (run 34061371205) | 56m27s | 3m33s |
| `merge_squash` (run 34151427980) | 94m38s | expired |

A five-minute margin would have permitted the 56m27s spend that nearly failed. It is nowhere near enough.

**Proposed:** refuse to spend an identity older than **20 minutes**, and re-mint instead. Rationale: the longest single stage that can run between a mint and the next spend should fit inside the margin with room to spare. Under the change in §4 no spend point is ever more than one workflow step from its mint, so 20 minutes is generous for the intended path and tight enough that any regression toward long-lived reuse is refused rather than survived. The number is a policy constant and belongs beside the other budget scopes, not hard-coded at the call site.

The margin is a **refusal**, not a warning. A spend attempted on an over-age identity fails closed with `identity_expired` before touching the API.

### The margin is policy, and policy is a dependency class

**Changing the identity margin does not invalidate any attestation.** The margin governs whether an *actor* may spend a credential; it is not an input to what any authority proves about a *subject*. An attestation asserts that named evidence was produced against an exact tree by a named authority, and that stays true regardless of the margin under which a later merge ran.

**Where it lives, and the case that failed.** The margin is `runtime.autonomous_identity_max_age_seconds` in `.factory/kernel.json`, beside `active_lease_ttl_seconds` and `legacy_lease_ttl_seconds` — the same class of value, a TTL on an operational resource. It was going to go in `harness/budgets.json` beside the other durations, but every scope there is a *ladder* duration derived from measurements via `ceil(p100 × headroom / 60) × 60`, and a credential lifetime is not that kind of number; it would have needed a fabricated measurement to fit the schema.

The obvious defence of `kernel.json` — *the file holds actor governance, so a non-verdict value belongs there* — **is false, and was checked rather than assumed.** The file also holds `prompts`, which names the file each blinded judge reads, and `validation.quick_command`, which names what the builder's gate runs. Both shape verdicts. Neither is bound into any attestation today. So `kernel.json` already mixes classes, and a value placed there on a "this file is not policy" argument would be resting on something untrue.

The narrower claim survives and is the one made: **the `runtime` section specifically is uniformly actor governance** — a rebuild budget, two lease TTLs, a work root, and now a credential age — and none of its members is read by an authority to reach a verdict.

Because the file mixes classes, the classification is written where the value is, using the convention `kernel.json` already uses for exactly this problem: a `_key` sibling documenting the value. That sibling says the margin is not a verdict input, says the claim covers `runtime` and not the file, names `prompts` and `quick_command` as the counterexamples, and forbids generalising outward.

**Corollary, which is the part worth enforcing:** the margin must never become an input to any authority's verdict. The moment a verdict depends on it, this classification is false and it becomes a bound policy hash. Any future change that reads the margin inside an authority reopens this ACP.

**Left open by this ACP:** that `prompts` and `validation.quick_command` shape verdicts and are bound into nothing is a finding of this pass, not a claim about what should happen. It is out of scope here and belongs with DFE-009 (attestation revocation), which is the entry already asking what an attestation should be bound to.

---

## 6. Acceptance criteria

1. **A build whose validation exceeds two hours merges successfully**, and every spend point in that run is verifiably younger than its own mint. Not "exceeds 60 minutes" — that would pass on a 61-minute build and prove only that one arithmetic error was corrected.
2. Every spend of the identity emits its age at spend, so the margin can be audited from a run's own record rather than reconstructed from timestamps.
3. A deliberately expired or garbage App token produces reason code `identity_expired`, and is **never** attributed to `merge_preauth` or to any other authority that did not produce it (DFE-014).
4. An adversarial test asserts (3) against a deliberately weakened kernel before the fix lands, per `07-ADVERSARIAL-BENCHMARKS.md`.
5. **The margin alone does not satisfy this ACP.** A run in which the evidence stage completes (observed: 4970.989 s) and the merge is then authorised on a credential minted *after* that stage is the thing being bought. An implementation that adds §4's refusal without §4's split converts a 95-minute 401 into a 20-minute refusal and merges nothing; it fails criterion 1 by construction, and this criterion exists so that cannot be reported as partial progress.

**Falsifier:** a build whose validation exceeds two hours merges successfully with every spend point younger than its own mint.

---

## 7. Consequences

**The canary is unblocked, or its next blocker is exposed.** PR #134 passed the entire ladder. If the identity fix is correct, #134 merges and the first complete Level-4 lap is observed — at which point `e2e_steps` is ratcheted to the observed value with zero slack, per the standing instruction.

**One suspicion checked and withdrawn.** I suspected the `resume_pr`/`resume_run_id` recovery path (D-034) had been built to clean up after expiry without expiry being diagnosed. It was not. D-032 records the actual cause — `ModuleNotFoundError: No module named 'factory_kernel'`, an import-path bug in `factory_provenance.py publish`, fully diagnosed, named the eleventh canary defect, with a mutation guarding the fix. D-033/D-034 were raised on 2026-09-04, when authentication was still the Actions token ("Authentication is unchanged: … already take the Actions token from `GH_TOKEN`"). The App identity did not exist until PR #150 on 7 September. Resume predates the App token by three days and cannot have been papering over its expiry. D-032 is in fact a counter-example: a cause diagnosed properly rather than a symptom handled.

There is a smaller, genuine observation left over. Resume has *since* become an accidental mitigation for expiry — a human-invoked resume gets a fresh token, so it can finish a build the expiry killed. Nobody decided that, and it is part of why expiry never surfaced as its own defect: an escape hatch existed for unrelated reasons. Worth naming so it is not mistaken for a designed remedy.

**No claim is made here about wall time as a correctness constraint.** Validation duration is load-bearing for this defect only because the credential is misused. Once §4 lands, duration returns to being an efficiency question, and `10-ACP-003` should continue to rest on the floor-lag argument (DFE-017), which is stronger and does not depend on a bug that is about to be removed.
