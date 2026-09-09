# ACP-001 — Adoption of the 7 September Architecture Corpus

**Tier:** 2 (trust-boundary and system-contract change)
**Status:** DRAFT — evidence section incomplete
**Register:** DFE-001
**Raised:** 2026-09-08
**Decides:** whether ~400 decisions currently at PROPOSED become binding, in whole, in part, or not at all

---

## Why this ACP exists

The corpus defines a governance regime in which Tier 2 changes require an Architecture Change Proposal carrying new evidence, alternatives, TCB effects, proof obligations and a migration path (DFG-003).

Most of the corpus **is** Tier 2. None of it travelled through that process. It could not have: the process is defined inside the same corpus, and the agent proposing the decisions was also the agent adjudicating them.

Adopting it silently would establish a precedent that architecture becomes binding by being written rather than by being judged. That is the precedent the whole system exists to prevent. **This ACP is the corpus submitting itself to its own rules.**

It is deliberately one ACP rather than four hundred. The alternative — a separate proposal per decision — is unaffordable, and the corpus is coherent enough to stand or fall largely together. Where it should not, §5 carves out exceptions.

---

## 1. Decision affected

All decisions in `register/decisions.json` at status `PROPOSED`, `SETTLED` or `CONSTITUTIONAL` (357 of 400), plus the ten amendments at `AMENDMENT`.

Specifically **excluded** from this ACP, to be decided separately:

- `DFE-009` (attestation revocation and authority disagreement) — genuinely new design work, not yet specified enough to adopt. Tier 3.
- Everything in `03-PHYSICAL-AND-COMMERCIAL.md` — a different domain with no implementation to falsify it.

---

## 2. New evidence

*This section is deliberately unfilled. It is completed by running the DFC-074 comparison against the repository. Until it is filled, this ACP cannot be approved — and an ACP with an empty evidence section is exactly the thing the corpus forbids.*

### 2.1 Status verification

Every decision not at `PROPOSED` claims something about the repository. All such claims are transcript-derived and up to a day stale.

- [ ] Verify all 10 `BUILT` decisions against the repository. Record PR or commit for each.
- [ ] Verify all 4 `PARTIAL` decisions. Downgrade any that are aspirational.
- [ ] Verify all 6 `SUPERSEDED` links resolve to documents that actually exist on the architecture branch.
- [ ] Record any decision the repository has already **contradicted** — those are the most informative.

### 2.2 The DFC-074 comparison

For each of the ~49 schemas in register DFC:

- [ ] Does an equivalent artefact already exist in the repository?
- [ ] If yes: does the proposed schema add anything, or is it a rename?
- [ ] Does it **collide** with a trusted artefact format currently carrying evidence?
- [ ] Would adopting it require rewriting anything inside the TCB?
- [ ] Verdict: `adopt` · `adopt with amendment` · `already solved, discard` · `defer, no consumer`

Expected outcome, stated in advance so it can be wrong: **most Phase 2–5 schemas will come back `defer, no consumer`.** If they do not, the phased rollout (DFC-073) is wrong and should be revisited.

### 2.3 Contradiction scan

- [ ] Does any decision contradict a rule currently enforced by `factory_security.py`, the rulesets, or the evidence spine?
- [ ] Does any decision contradict another decision in the corpus? (The three roadmaps are known; look for others.)
- [ ] Does any decision require a capability the GitHub App identity does not have?

### 2.4 Baseline measurement

Adoption should be measurable, so record the pre-adoption state:

- [ ] Trusted LOC, however crudely counted, as the TCB ratchet baseline (DFA-003).
- [ ] Current inner-evidence wall time, per the corpus's own instruction to measure before optimising.
- [ ] Current floors: unit, static, holdout, mutations, anchors.

---

## 3. Alternatives considered

**A. Adopt silently.** Merge the architecture branch, treat it as decided. Cheapest, and it establishes that writing is deciding. Rejected.

**B. Reject the corpus, re-derive incrementally.** Maximally rigorous. Discards several months of design work that is mostly sound, including the single best decision available (DFV-008, the attestation dependency model). Rejected as waste.

**C. One ACP per decision.** Unaffordable at 400 decisions, and most are not independently meaningful.

**D. This ACP.** Adopt as a block, with the comparison as evidence, the ten amendments applied, and explicit carve-outs for what is not ready. **Recommended.**

**E. Greenfield — new repository, claim-first core.** Considered and rejected on separate grounds: it discards the accumulated evidence corpus (floors, mutations, immunity entries), restarts the distance to one proven Level-4 lap, creates the second state machine DFM-001 forbids, and has no trusted authority from which to bootstrap a new one — violating DFG-005. See `06-SPIKE-SHADOW-CLAIMS.md`, which is the experiment that could reopen this.

---

## 4. TCB effects

Adoption itself changes no code and therefore no TCB. But it commits to changes that do:

| Direction | Decisions |
|---|---|
| **Reduces the TCB** | DFA-002 smaller kernel · DFA-004 orchestration/authorisation split · DFA-039 intelligence layer permanently excluded · DFM-010→022 module extraction |
| **Adds to the TCB** | DFV-011/DFE-008 `ReheadAuthority` · DFC-033 capability grant enforcement · DFC-031 attestation validation |
| **Neutral but load-bearing** | DFV-008 dependency model — no new trust, but every proof reuse decision flows through it |

**Proof obligation.** DFA-003's TCB manifest and ratchet must exist *before* the additions land, or there is no way to show the net direction is downward. Sequencing: manifest first, then `ReheadAuthority`.

---

## 5. Recommended amendment

Adopt with the following conditions.

1. **The ten DFE amendments apply on adoption.** DFE-002, DFE-003 and DFE-004 land first; they change what "decided" means.
2. **`ReheadAuthority` is adopted in its amended form only** (DFE-008): a query over the dependency model, never a knower of claim types.
3. **DFC Phases 2–5 are adopted as `PROPOSED`, not as normative.** A schema becomes normative when it acquires a consumer, not when it is written.
4. **DFE-009 is carved out** and raised as its own Tier 3 proposal.
5. **Preflight and Front Door directives adopt in two parts.** The subset that produces a checkable artefact — the approved spec, its versioning, hashing, traceability, and the handoff record — is promoted into the canonical contracts as enforced schema. The remainder, which governs unverifiable conversational judgement, moves to `.factory/methods/` as guidance. Guidance sitting in an architecture document acquires the appearance of an invariant it has not earned.
6. **Adoption does not authorise implementation order.** DFV-018's six-phase ordering is separate, and the qualification floor rule stands: no architecture rewrite until a single path demonstrably operates at current main.

---

## 6. Consequences

**If approved:** 357 decisions move from PROPOSED to their register status. Contradicting a SETTLED or CONSTITUTIONAL decision thereafter requires its own ACP. Every PR names the decision IDs it exercises. The corpus becomes citable in code review, which is the point.

**If rejected:** the corpus remains a design reference with no authority, and architecture decisions continue to be made per-issue. Not catastrophic — the system worked this way until 7 September — but the pre-decided questions get re-litigated, which is the cost the corpus was written to avoid.

**If deferred:** the likely default, and the worst option. The corpus sits on a branch acquiring the appearance of authority without the substance, and implementers cite it selectively. **Deferral should be treated as rejection with extra steps.**

---

## 7. Approval

| | |
|---|---|
| Evidence complete | ☐ |
| Amendments applied | ☐ |
| Carve-outs recorded | ☐ |
| Owner decision (Tier 2 → owner sign-off) | ☐ |

**Do not approve this ACP with §2 unfilled.** An ACP whose evidence section is a set of unticked boxes is the failure mode this document exists to prevent, and approving it anyway would be the first and most damaging precedent the governance regime could set.
