# Dark Factory — Decision Register

**400 decisions.** Extracted from the 7 September architecture corpus and the surrounding record, given stable IDs, statuses and tiers, with the 2026-09-08 evaluation folded in as explicit amendments rather than as a separate opinion.

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
400 decisions

DFA    49   Target architecture directive
DFC    76   Canonical contracts / schema specification
DFP    81   Preflight algorithm directive
DFF    87   Front Door algorithm directive
DFM    67   Repository migration directive, Parts I-XV
DFV    21   v3 claim-centric locked decisions
DFG     9   Architecture governance / self-modification
DFE    10   Amendments from the 2026-09-08 evaluation

PROPOSED         243     awaiting evidence
SETTLED           78     reopening needs evidence, not permission
CONSTITUTIONAL    40     reopening needs an owner decision
BUILT             10     merged and evidenced
AMENDMENT         10     open, from the evaluation
OPEN               9     deliberately undecided
SUPERSEDED         6     replaced; superseded_by names the replacement
PARTIAL            4     component exists, decision does not

tier 0   36      tier 1  172      tier 2  149      tier 3   43
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

## The ten open amendments

Ordered by cost. None of them block the canary.

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

The register has not been checked against the repository. Several `BUILT` and `PARTIAL` statuses are transcript-derived and up to a day stale. **First task in Claude Code: verify every non-PROPOSED status against the actual repo, and correct it.** A register that overstates what exists is worse than no register, and the system's own honesty rule applies to it.
