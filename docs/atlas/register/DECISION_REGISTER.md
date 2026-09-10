# Dark Factory — Decision Register

**416 decisions.** Extracted from the 7 September architecture corpus and the surrounding record, given stable IDs, statuses and tiers, with the 2026-09-08 evaluation folded in as explicit amendments rather than as a separate opinion.

This exists to make one sentence operational: *these are well-reasoned claims at status PROPOSED, awaiting the evidence that promotes or rejects them.* Until now that was a posture. Now it is a file with IDs in it.

## Files

| File | What it is |
|---|---|
| `decisions.json` | The register. Machine-readable, one object per decision. |
| `validate_register.py` | Structural integrity: unique IDs, valid statuses and tiers, referential integrity on supersession and amendment links, symmetry warnings. Exit 1 on error. |
| `extract.py` | Per-issue decision packets (DFE-010). Resolves transitively, so naming a decision always pulls in the amendment that changes it. |

Run `python3 validate_register.py` in CI. A register that no longer validates is a register nobody trusts.

## Current state

```
416 decisions

DFA    49   Target architecture directive
DFC    76   Canonical contracts / schema specification
DFP    81   Preflight algorithm directive
DFF    87   Front Door algorithm directive
DFM    67   Repository migration directive, Parts I-XV
DFV    21   v3 claim-centric locked decisions
DFG     9   Architecture governance / self-modification
DFE    26   Amendments from the 2026-09-08 evaluation and after

PROPOSED         243     awaiting evidence
SETTLED           78     reopening needs evidence, not permission
CONSTITUTIONAL    40     reopening needs an owner decision
BUILT             10     merged and evidenced
AMENDMENT         26     open, from the evaluation and after
OPEN               9     deliberately undecided
SUPERSEDED         6     replaced; superseded_by names the replacement
PARTIAL            4     component exists, decision does not

tier 0   37      tier 1  177      tier 2  158      tier 3   44
```

**118 decisions require an ACP to contradict. 243 have no evidence behind them at all.** That ratio is the honest picture of the corpus, and it is why adoption (DFE-001) matters more than implementation speed.

## Status semantics

| Status | Meaning | To change it |
|---|---|---|
| `PROPOSED` | Written at implementation grade. No evidence. **Default for the whole corpus.** | Nothing. It is not decided. |
| `SETTLED` | Not to be reopened without new evidence. | Evidence, not permission. Record the evidence. |
| `CONSTITUTIONAL` | Not to be reopened without an owner decision. Tier 3. | ACP plus owner approval. |
| `BUILT` | Merged and evidenced in the repository. | Normal change control, plus tier. |
| `PARTIAL` | A named component exists; the full decision does not. | Finish it or downgrade it honestly. |
| `SUPERSEDED` | Replaced. `superseded_by` names the replacement. | Nothing. Read the replacement. |
| `OPEN` | Deliberately undecided pending implementation evidence. Tier 0/1. | Benchmark or probe it. Do not escalate to the owner. |
| `AMENDMENT` | A proposed change to the corpus. | Accept, reject, or defer — but record which. |

**A caution on tiers.** Tiers were assigned by the evaluation, not by the source corpus. They are themselves a proposal. If a tier looks wrong, it probably is; correct it rather than working around it.

## The working protocol

**Every PR names the decision IDs it exercises.** In the body:

```
Decisions: DFM-026, DFC-038, DFC-039
```

Generate the block with `python3 extract.py DFM-026 --markdown` and paste it into the issue. Because resolution is transitive, an issue about probes cannot silently omit the amendment that replaced the probe rule.

**Promotion is an event, not an edit.** When a decision acquires evidence, change its status and record what promoted it. `PROPOSED → BUILT` needs a PR number. `PROPOSED → SETTLED` needs the evidence that settled it. Never promote by editing the title.

**Contradiction requires an ACP.** If work would contradict a `SETTLED` or `CONSTITUTIONAL` decision, stop and produce the six-field amendment (DFG-008): decision affected · new evidence · why the existing decision fails · alternatives · recommended amendment · consequences.

**A decision that cannot be falsified is not a decision.** Several entries carry a `falsifier` field. Most do not, and that is a gap worth closing opportunistically: when you touch a decision, write down what would prove it wrong.

## The twenty-six open amendments

Ordered by cost. DFE-018 and DFE-014 are the two that block the canary; the rest do not.

| ID | Amendment | Affects | Cost |
|---|---|---|---|
| **DFE-002** | Supersession headers on all three roadmaps; DFV-018 declared current | DFA-043, DFM-XIV | Minutes |
| **DFE-004** | Split canonical contracts into normative phase 1 and proposed phases 2–5 | DFC-073 | Minutes |
| **DFE-003** | Split the lock list into CONSTITUTIONAL and SETTLED; demote one-candidate-at-a-time to a default | DFA-046 | Minutes |
| **DFE-006** | Interview learning may not modify the topic coverage map; change the metric to defects traced to unasked questions | DFF-019/043/045/077/079 | An hour |
| **DFE-005** | Replace the V1 value-of-information rule with fixed probe budgets plus logging | DFA-029, DFP-018/019 | An hour |
| **DFE-001** | Adopt the corpus via a Tier 2 ACP, using DFC-074's comparison as its evidence section | DFA-048, DFC-074, DFG-003 | Half a day |
| **DFE-008** | ReheadAuthority becomes a query over the dependency model rather than a knower of claim types | DFV-011, DFM-023, DFV-008 | Design change; cheap now, expensive later |
| **DFE-009** | Specify attestation revocation and authority-disagreement resolution | DFC-031/032, DFV-008 | Genuinely new design work |
| **DFE-007** | Runner-up sampling in Preflight calibration | DFA-032/045, DFP-044/077 | Cheap to specify, real compute to run |
| **DFE-010** | Per-issue decision extracts rather than wholesale references | DFA-048, DFC-047 | Tooling, once — `extract.py` is the first cut |
| **DFE-016** | Raise `static_checks` 5 → 6, finishing the job PR #139 started | DFA-003 | One line |
| **DFE-014** | Never attribute a refusal to an authority that did not produce it | DFA-017, DFV-005, DFM-015 | An hour, plus the constitutional paragraph |
| **DFE-015** | Split the unit floor three ways — factory, backend, frontend | DFA-003, DFM-002 | Half a day across three files |
| **DFE-017** | A PR that changes the number of checks or tests must touch `floor.json` | DFA-003, DFE-012/015/016 | A deterministic check plus its mutation |
| **DFE-018** | The autonomous identity is minted per operation, not once per job | DFV-005, DFM-015, DFA-007, DFE-014 | Bounded — see `12-ACP-004` |
| **DFE-019** | Split the build's push/PR handoff behind its own mint | DFE-018, DFV-005, DFM-015 | A design question, then a workflow step |
| **DFE-020** | Only `stale_base` has a model-free recovery path; every other refusal is terminal | DFE-014, DFE-018, DFV-011 | A design question |
| **DFE-021** | A condition detectable early must be checked early — starting with whole-base movement | DFE-017, DFE-020, DFV-011 | One `gh` call at an existing rung |
| **DFE-022** | A rung's observed measurement must survive a later rung's failure | DFA-003, DFE-015/017, DFM-026 | A scrubbed per-rung measurements file |
| **DFE-023** | A defanged detector reports protection it is not providing | DFA-017, DFE-014/021, DFV-009 | Open — the decidable part is small |
| **DFE-024** | A test that reads source is not a test that ran it | DFE-023, DFA-017, DFV-009, DFE-015 | The rule is cheap; the audit is unscoped |
| **DFE-025** | A refusal must distinguish a verdict from an authority that never ran | DFE-014/020, DFA-032, DFV-016 | A reason code |
| **DFE-026** | The only lane that proves the judge is the lane forbidden from changing it | DFE-021/023/024, DFA-018, DFV-002, DFM-001 | Tier 3 — the cheap end is hours |

**DFE-018 and DFE-014 come first**, in that order: DFE-018 is the blocker (no autonomous PR can merge) and DFE-014 is why it took four days to find. **DFE-017 lands before DFE-012** — ACP-003's argument assumes the ratchet family works as a mechanism, and three misses in three weeks say it does not; adding a fourth dial to a mechanism nobody is turning is the wrong order.

**DFE-002, DFE-003 and DFE-004 should land before anyone implements from the corpus**, because they change what "decided" means. **DFE-008, DFE-009 and DFE-010** get more expensive the longer they wait.

## Supersession chain

```
DFA-043   26-step migration dependency order   ─┐
DFM-XIV   16-phase concrete programme order    ─┴─→  DFV-018  six-phase ordering (current)

DFP-018   value-of-information rule            ─┐
DFP-019   probe execution threshold            ─┴─→  DFE-005  fixed budgets plus logging

DFA-046   twenty questions not to reopen       ───→  DFE-003  split list, one item demoted
DFV-011   re-head as deterministic authority   ───→  DFE-008  re-head as dependency query
```

The floor rule from the superseded DFM-XIV survives independently and is not superseded: **no architecture rewrite before the qualification floor is clear and a single path demonstrably operates at current main.**

## What the register does not contain

- **Code, tests or schemas.** Only the decisions about them.
- **The reasoning.** Titles are handles, not arguments. The reasoning is in `02-ARCHITECTURE-AND-METHODS.md` and the source transcripts.
- **Anything from the physical or commercial work.** That corpus was produced under near-uniform assent and has no implementation to falsify it; giving it decision IDs would imply a rigour it has not earned. Register it when the physical repository exists.
- **Falsifiers for most entries.** The largest honest gap.

## Provenance

Built 2026-09-08 from `darkfactory_chatgpt_history__2_.txt` and `ChatGPT-Evaluate_Repository_Progress` (41,532 lines). Decision titles are faithful to the source headings; statuses, tiers and amendments are judgements added on top, and are marked as such in `decisions.json`.

## The measured cost of a wrong diagnosis

Recorded here because it is the strongest single argument in the register and it was paid, not predicted.

On 2026-09-07 PR #134 passed the entire ladder — `evidence` returned `outcome=ok` after 4970.989 s, `merge-pre` after 0.708 s — and was refused at the merge by a GitHub App token that had expired 35 minutes earlier. The refusal was **correct**. Its attribution was not: it named `merge pre-authorization (harness/merge_verify.py pre)`, an authority that had succeeded in that same run.

The artifacts said *retry the merge*: `merge-authorization.json` written, the spine closed, the evidence intact and bound. The correct action was one command. The label said evidence refused it.

- **Four days** of diagnosis, and four repeat reports on issue #119 reasoning from the wrong cause.
- **Two further days parked**, because `merge_preauth` is not `stale_base` and only `stale_base` has a model-free recovery path (DFE-020). The factory skipped the PR; `rehead` refused it by hand.

Six days, from one wrong word in a refusal that was otherwise right. **A wrong diagnosis is not a smaller defect than a wrong verdict — it is the same defect, pointed at whoever reads it next.**

## The one finding underneath four of them — DFE-026

Two lanes reach `main`. The **autonomous** lane pays 21 required claims, eleven independent authorities, 437 mutations, E2E, holdout, conformance, ratchet and post-merge tree verification — 89 minutes — and is *forbidden* from touching `factory_kernel/**`. The **maintenance** lane may change anything, and is proved by identity, provenance, hygiene, and `harness/ci.py --quick`: static, unit, `return 0`.

Of the 428 factory mutation defects, **193 mutate `factory_kernel`.** They are reached at rung 5 of the full ladder. `--quick` returns at rung 2.

> **The lane that writes the judge proves the least, and the lane that proves the most is forbidden from writing it.**

Concretely: of the 428 factory mutation defects, **193 mutate `factory_kernel`** — and they run only on pull requests that may not change it. Every defect of 8–10 September entered through the maintenance lane; the autonomous lane caught all four, at 21, 57, 89 and 4 minutes each, **one per attempt.**

That is not a coverage gap. It is a consequence of how the two lanes were defined — which is why DFE-021, DFE-023, DFE-024 and the lint gap, which look like four findings, are one. Each is a hole in the maintenance lane; none can bite the autonomous lane. Four for four, all merged on static-plus-unit.

And the daily `main-regression` is the only post-merge route to those 193. It has failed at rung 3 every day since 5 September, so **nothing has verified the kernel's own detectors against anything for five days** — while issue #119 sat at `factory:needs-human`, read as a stuck browser test.

Two things to read before acting. This is a **proof** asymmetry, not an authorisation one: the trust-root veto works exactly as designed. And the fix is **not** the full ladder on the maintenance lane — that recreates audit finding B.1, the deadlock where nobody can safely maintain the judge. The cheap end (lint the trust root, oblige new trusted functions to be invoked, move millisecond checks to the static rung) would have caught **three of this week's four**; the fourth was a credential lifetime that no static check could see.

## Four gates, four different wrongnesses

Filed within a day of each other, and easy to conflate. They are not the same defect and they do not share a fix.

| | The gate | What is wrong with it |
|---|---|---|
| **DFE-017** | a floor | it does not move when the thing it measures moves |
| **DFE-021** | a check | it runs long after its input was knowable |
| **DFE-022** | a measurement | it does not survive an unrelated later failure |
| **DFE-023** | a green marker | it asserts more than the check behind it examined |
| **DFE-024** | a passing test | it examined the code's spelling, never its behaviour |
| **DFE-025** | a refusal | it credits an authority with a judgement it never made |

The fourth is the one to be most careful about. The other three fail visibly — a stale floor, a late refusal, a lost number. `MUTATION_ANCHORS_OK` fails by **succeeding**.

### An escape is visible. A defanged-but-caught defect is invisible.

Nine defects were re-anchored on `746aac7` after DFE-014 moved the text they pinned. Two were defanged, and only one of them said so.

| | `why` claims it tests | What the replace does | Outcome |
|---|---|---|---|
| `worker-resume-also-dispatches` | dispatch loses its inverse guard | renames a step id; guard intact | **escaped** after 2072 s |
| `pack-verified-after-holdout` | pack must be verified before the judge | changes a cursor label; **nothing moves** | **caught — and green** |

The second is the dangerous one. It passes, it will go on passing, and it reports an ordering property as protected while nothing checks that ordering. No number of laps surfaces it; it was found by reading. That inverts what a mutation catalogue is for — it can now assert a protection it is not providing, and the failure mode is silence.

**Today's two visible failures were the lucky ones.**

The shape recurred the next day in an unrelated mechanism (**DFE-024**). Every test for `merge_authorized` asserted its *source* contained the right strings; none called it. It held `create(` where the imported name is `create_detached`, so a method with an undefined name passed its entire suite and raised `NameError` on its first real invocation — in the merge step, after an 89-minute ladder that had gone green on every rung. An AST walk of `tests/factory` puts **254 of 1825** test methods (13.9%) as containing a source read, across 64 of 91 files.

The exposure is enumerable rather than hypothetical: a defect can only be defanged by someone moving its anchor, so the population is every defect whose `find`/`replace` has ever changed. A git walk of both catalogues across all 82 commits that touched them gives **37** — 22 from PR #139, 7 from PR #155, 8 from six other commits. None has ever been checked for preserved meaning.

The third was found the hard way. On 2026-09-09 run 34379226169 passed E2E for the first time under this kernel — provably, since the mutation rung runs after it and ran — and the number was lost, because mutations failed twenty minutes later for an unrelated reason and the bundle is assembled once at the end. `.factory/locks/floor.json` has waited months for exactly that value.

## Evidence, and the absence of evidence, recorded as the same fact

`REASON_CODES` has no `provider_failed`. So these two produce an identical code and an identical sentence in the permanent record:

- the architecture holdout examined this change and rejected it
- the architecture holdout could not be reached

On 2026-09-10 the second happened — unparseable output after 2.023 s, no turns, no cost, no events — and the PR carries *"Refused by: independent architecture holdout"*. Nothing examined anything.

This is not DFE-014 recurring. There the *wrong* authority was named; here the right one is credited with a judgement it never made, and DFE-014's field signature (`stage` and `stage_context` agreeing) correctly reports the cursor as open. The gap is one rung down, in the vocabulary rather than the attribution.

It matters most where it compounds: **any calibration over refusal records reads provider flakiness as architectural rejection.** Flakiness is uncorrelated with the change, so it is noise labelled as signal, in the field a calibrator would treat as ground truth (DFE-025, affecting DFA-032).

## A class worth naming: detectable early, checked late

Two recorded instances, and the second was found by walking into it.

| Condition | Cheaply knowable at | Actually checked at | Closed by |
|---|---|---|---|
| Mutation anchor no longer injectable | second zero, pure text | the full harness, ~50 min in | PR #139 — moved to the head of the static rung |
| Main moved by a non-trust-root commit | second zero, one `gh` call | merge pre-authorization, ~83 min in | open — DFE-021 |

Both have one shape: **the authority that could answer cheaply is not asked until something expensive has already run.** In the first case thirteen maintainer PRs moved anchors before anyone noticed, because nothing a maintainer met could see the drift. In the second, `--currency-only` checks trust-root drift in the first seconds while `merge_verify.py:149` checks whole-main movement only at the end, so a base moved by a docs commit passes every early gate.

A third instance is likelier than not. Look wherever a late gate reads state that was available at the start.

DFE-017 reaches for this class from the other side — it asks that a floor move when what it measures moves. DFE-021 asks that a check run when its input is first knowable. Neither subsumes the other; both are about a gate that exists and fires at the wrong time.

## Verification, 2026-09-09

Every non-`PROPOSED` status has now been checked against `origin/main` at `f13219b`. Verified entries carry a `verified` field, and every entry making a repository claim carries `evidence` naming the PR or file that supports it.

**Nothing was downgraded.** The feared failure mode — a register that overstates what exists — did not occur. Every error ran the other way: the register understated the repository. The four corrections of substance are recorded in `decisions.json` under `notes.verification_2026_09_09`, and the fuller account is in `11-UNCERTAINTY-LIST.md`.

Two facts the corpus has no entry for at all, both found during this pass:

- **The measured floors lag their own evidence again.** `floor.json` records `unit_tests: 1033` and `static_checks: 5`; the same canonical quick gate observed `UNIT_PASSED tests=2353` and `STATIC_OK checks=6` on 2026-09-09 (run 34332642132). This is audit finding B.5 recurring at roughly four times its original magnitude, against a stated zero-slack rule.
- **The daily full-harness regression on `main` has failed every day since 2026-09-05**, always at `GATE_FAILED: e2e`. Escalated as issue #119, now `factory:needs-human`.

Statuses, tiers and amendments remain judgements added on top of the source corpus, not findings from it.
