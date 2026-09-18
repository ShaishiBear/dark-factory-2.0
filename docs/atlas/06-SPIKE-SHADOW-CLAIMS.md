# Spike S-001 — Shadow Claim Emission

**Purpose:** settle, with evidence rather than argument, whether the claim-centric model can be introduced into the existing kernel incrementally — or whether a rewrite is forced.
**Timebox:** two days. Hard stop.
**Register:** informs DFV-019, DFV-020, DFV-021, DFA-002, and the greenfield alternative rejected in `05-ADOPTION-ACP.md` §3E.
**Blocks:** nothing. Runs after the canary lands, or alongside it if it touches no production path.

---

## The question

The v3 model says Dark Factory is claim-centric: the kernel authorises transitions against claims and their attestations rather than executing a lifecycle. The existing kernel is procedural — `KernelRuntime.build_issue()` and `validate_pr()` run a sequence, with `state.py` defining a lifecycle that production dispatch does not consume (audit finding B.4).

Two futures follow, and only one is affordable:

- **Incremental.** A claim substrate can observe existing runs and record what it *would* have authorised, without altering the trust path. Once shadow and production agree, promote path by path. This is DFG-006 — shadow and equivalence first, cutover second — expressed as code.
- **Forced rewrite.** Emitting claims requires restructuring the evidence spine, in which case a phased migration is a fiction and the honest choice is a planned rewrite rather than one discovered halfway through.

**Nobody currently knows which.** Both this document and the whole architecture corpus assume the first. That assumption is untested and it is load-bearing.

---

## Design

Build `factory_core/` as a new package with **no authority whatsoever**. It observes. It cannot refuse, grant, mutate or gate anything. Nothing in the trust path may import it, and no workflow may depend on its output.

```
existing run  ──────────────────────────────►  existing decision (authoritative)
      │
      └─► observer ─► claim record ─► shadow verdict (recorded, ignored)
```

If the spike ever needs a decision from `factory_core` to proceed, it has failed its own premise. Stop and record that.

### Scope: one claim type, one path

**RED.** Chosen deliberately:

- It is early in the lifecycle, so a broken observer cannot contaminate merge.
- It has a crisp subject: a failing acceptance test at an exact revision.
- It already produces something attestation-shaped, so the mapping is a real test rather than a trivial one.
- Its dependencies are simple enough to digest by hand and check.

**Do not** extend to GREEN, mutation, holdout or merge inside the timebox. Breadth is the enemy here; the question is about coupling, not coverage.

### What a shadow claim must contain

Minimum viable, per DFC-031 and DFV-008:

```
claim_id
claim_type            RED
subject               revision identity + test identity
authority             which authority produced the underlying result
authority_version
dependencies          { SUBJECT_BYTES, TRUST_ROOT, POLICY,
                        AUTHORITY_PROGRAM, TOOLCHAIN }  → digests
verdict               PROVEN | FAILED | INDETERMINATE
observed_at
production_verdict    what the real runtime concluded
agreement             bool
```

The `dependencies` block is the part that matters. If digests cannot be computed for a RED claim without reaching into internals that would have to change, that is the finding.

---

## Acceptance criteria

The spike succeeds if all four hold:

1. **Observation without modification.** Shadow claims are emitted for at least five real runs with **zero changes to any file inside the TCB**. Measured, not asserted: the diff touches only `factory_core/**` and, at most, a single call site.
2. **Dependency digests are computable.** All five dependency classes resolve for a RED claim using information the runtime already has or can expose read-only.
3. **Agreement is measurable.** For every observed run, the shadow verdict and the production verdict are comparable, and disagreements are explainable rather than mysterious.
4. **The observer is inert.** Removing `factory_core/` entirely leaves every run byte-identical. Prove it by doing exactly that on the last run.

### What counts as failure

- The call site count exceeds **three**, or any of them sits inside `factory_kernel/`.
- Any dependency class cannot be digested without restructuring how evidence is stored.
- The observer needs state the runtime does not persist — meaning claims would require a storage change before they could be recorded at all.
- Making it work requires touching `spine.py`, `evidence_closure.py` or `provenance.py`.

**A failed spike is a successful spike.** Two days spent discovering that migration is impossible is enormously cheaper than four months spent discovering it in the middle.

---

## Decision gate

| Outcome | Reading | Action |
|---|---|---|
| All four criteria pass | Incremental migration is real. The corpus's phased plan is sound. | Proceed with DFV-018. Promote RED to authoritative once agreement holds over ~50 runs, then extend claim-by-claim. |
| Criteria 1–3 pass, 4 fails | Observer has hidden coupling. Recoverable. | Fix the coupling; re-run the spike. Do not proceed on a leaky observer. |
| Criterion 2 fails | Evidence is stored in a shape claims cannot address. | **Reopen the greenfield question.** The dependency model is the highest-value decision in the corpus; if it cannot be retrofitted, that changes the calculus in `05-ADOPTION-ACP.md` §3E. |
| Criterion 1 fails badly | Claim emission requires TCB surgery. Phased migration is a fiction. | Stop. Raise a Tier 3 proposal: planned rewrite versus indefinite procedural kernel. Do **not** drift into a rewrite by accident. |

---

## What the spike deliberately does not do

- **Does not authorise anything.** No gating, no refusal, no capability.
- **Does not persist beyond artifacts.** No database, no schema migration. If claims need storage to be observable, that is criterion-2 evidence, not a task.
- **Does not model the full claim lifecycle.** `UNKNOWN → PROPOSED → … → SUPERSEDED` (DFV-020) is out of scope. One claim, one verdict, one comparison.
- **Does not touch `state.py`.** The dual-lifecycle problem (B.4) is real and separate. Converging it inside this spike would confound the result.

---

## Reporting

One page, whatever the outcome:

- Files touched, with line counts, split by TCB and non-TCB.
- Number of call sites and where.
- Which dependency classes digested cleanly and which did not.
- Agreement rate across observed runs, and an explanation of every disagreement.
- The decision-gate row that fired.

File it as evidence against DFV-019 and DFV-021 in the register, and move their status. **This is the first decision in the corpus that will be promoted or rejected by evidence rather than by assertion** — which makes it worth doing carefully, and worth reporting honestly even when the result is inconvenient.
