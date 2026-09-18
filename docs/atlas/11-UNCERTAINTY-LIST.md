# Uncertainty List

> **RESOLVED 2026-09-09 against `origin/main` `f13219b`. Do not read the confidence
> statements below as current.** Items 1, 3, 5 and 6 came back wrong; items 4 and 7 came back
> partly wrong; items 2 and 8 were correct. That is above the one-third threshold set at the
> foot of this document, so the reassessment it calls for is owed. Every error ran in the
> direction of *understating* what the repository contains. The verdicts are recorded in
> `register/decisions.json` under `notes.verification_2026_09_09` and summarised in
> `register/DECISION_REGISTER.md` under "Verification, 2026-09-09".

*Where I'm least confident, ranked by how much would break if I'm wrong. Written 2026-09-08 without repository access; everything in the atlas is transcript-derived. This is the perishable part — my private uncertainty about my own claims — and it exists to make Task 1 sharper than "verify twenty statuses."*

**Check these in order. The first five are load-bearing.**

---

## 1. Does `state.py` drive production dispatch, or is `runtime.py` still procedural?

**Claimed:** audit finding B.4 — two lifecycle representations, with `KernelRuntime.build_issue()` and `validate_pr()` remaining a procedural sequence that does not consume `state.py`'s transitions.

**Confidence: low.** This was a 3 Sep finding. Four days of heavy work followed and I have no evidence either way about whether it was closed.

**Why it matters most.** The claim-centric model (DFV-019, DFV-021) assumes a kernel that is *not yet* transition-driven. If convergence already happened, spike S-001 is asking a question that's been answered and its scope is wrong. If it hasn't, B.4 is still live and DFM-001 — *do not build a second state machine* — is the most important line in the corpus.

**Check:** does anything in the production dispatch path import or consume `state.py` transitions, or is it still a hand-written sequence?

---

## 2. Does the merge verifier actually enforce tree equivalence?

**Claimed:** in `07-ADVERSARIAL-BENCHMARKS.md` under M-ADV-3 I wrote *"the merge verifier reportedly already has this property — verify that claim before writing the test."* That hedge was deliberate and it's the only one of its kind in that document.

**Confidence: low-to-moderate.** The transcript asserts it. I don't know whether it's enforced in code or described in a comment.

**Why it matters.** Merge is the most privileged transition (DFM-025). If `harness/merge_verify.py` compares what was authorised against what actually landed, one of your strongest guarantees is real. If it only compares heads, there's a gap at the single most consequential point in the system, and M-ADV-3 goes from a regression test to a genuine finding.

**Check:** read `harness/merge_verify.py`. Does it compare trees, or heads?

---

## 3. Is the in-repo holdout still readable by build-side agents?

**Claimed:** audit finding B.3 — `.factory/holdout/` protected from modification but not from reading, so tamper-resistant rather than builder-blind.

**Confidence: moderate that it was true on 3 Sep. Low that it's still true.**

**Why it matters.** DFA-031 — learning never enters blind judges — is constitutional, and it's meaningless if the judge isn't blind. CAP-ADV-3 is the remedy and its acceptance criterion is deliberately harsh: the path must be **absent**, not permission-denied. If this is still open, the honest move is relabelling the holdout as a protected adversarial regression suite until it's genuinely isolated.

**Check:** what tools do build-side agents hold, and can they `Read` or `Glob` `.factory/holdout/**`?

---

## 4. Every floor number I quoted

**Claimed:** unit 1033, anchors 428/9/26, static 5, holdout assertions 9, application mutations 9, independent mutation catches 3, security mutation catches 3, full suite 88 green.

**Confidence: low on the specific numbers, high on the shape.** These moved three times inside the transcript (549 → 766 → 1033) and I took the last value I saw. Several were reported on the App branch rather than on `main`.

**Why it matters.** ACP-003 sets cost ceilings from observed p50 and asserts outcome-identity against exactly this set. If the floor file's contents differ from what I recorded, the ratchet's baseline is wrong from day one.

**Check:** read the floor file. Replace every number in `02-ARCHITECTURE-AND-METHODS.md` §A.5 with what's actually there. Confirm `e2e_steps` is still genuinely absent rather than defaulted to zero.

---

## 5. Is per-stage timing instrumentation partially present, or absent entirely?

**Claimed:** in ACP-003 §7 I wrote *"currently absent, or at best partial"* — which is me admitting I don't know.

**Confidence: none. This is a guess.**

**Why it matters.** It's the difference between ACP-003 being a week of instrumentation work and an afternoon of wiring up numbers that already exist. It changes whether the cost ratchet is the cheapest thing on the table or merely a cheap thing.

**Check:** do trajectories or workflow logs already carry per-stage durations? Is anything timing the five authorities individually?

---

## 6. PR states as of 7 Sep

**Claimed:** #149 opened by the App and proving the identity path · #150 held as draft · #134 in the re-head sequence · #49 the canary, still open.

**Confidence: moderate at the moment of writing, low now.** These are the most perishable facts in the atlas and they were already a day stale when I wrote them.

**Check:** current state of #49, #134, #149, #150. Whether the canary has since completed — which would change the standing instruction and make ratcheting `e2e_steps` the immediate next action.

---

## 7. Are the seven architecture commits still unmerged?

**Claimed:** `ffa6623`, `357aca5`, `04eb0b6`, `d2eeda7`, `bc133cf`, `dcd815f`, `cf49319`, `56e1497` sit on `architecture/dark-factory-2-target` with `main` untouched and no PR opened.

**Confidence: moderate.** Deliberate at the time. Four days is long enough for that to have changed.

**Why it matters.** The whole adoption argument in `05-ADOPTION-ACP.md` assumes the corpus is *not yet* in `main`. If it was merged without an ACP, the precedent has already been set and the ACP becomes a retrospective ratification, which is a different and weaker document.

**Check:** is the branch still unmerged? Was a PR opened?

---

## 8. Whether `_worker_env` is genuinely an asserted allowlist

**Claimed:** in §A.2, *"model workers get neither credential; `_worker_env` is an asserted allowlist, not an assumed one."*

**Confidence: moderate.** The transcript says it plainly, but this is exactly the class of claim the corpus warns about — *declared metadata is not an independently enforced fact* (DFC-072).

**Why it matters.** CAP-ADV-4 is one of the three cheap tests I recommended doing first precisely because it guards a property that supposedly already exists. If the allowlist is assumed rather than asserted, the test isn't cheap — it's a fix.

**Check:** is there an assertion, and does anything test it?

---

## Two general cautions

**Everything in `02` Part A is the weakest section in the atlas.** It's the only part making direct factual claims about repository state, and all of it came from reading a conversation. The rest — architecture, decisions, evaluation — is reasoning about that material and doesn't depend on the details being current.

**Part M is mine, not the record's.** The ratchet family, the search-worth rule, the three-loop ranking and the acceleration/Preflight tension are my analysis from 8 September. They're registered as AMENDMENT alongside everything else from the evaluation, which is correct — they're proposals, not findings, and nothing in the source corpus says them.

---

## The threshold

If more than about a third of items 1–8 come back wrong, stop and reassess before proceeding to any other task. That would mean the transcript record diverged from reality more than expected, and the reasoning built on top of it deserves the same suspicion — including the parts that feel most solid.
