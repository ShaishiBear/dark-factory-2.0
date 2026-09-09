# Dark Factory — Programme Atlas

Rebuilt from `darkfactory_chatgpt_history__2_.txt` (18 Aug – early Sep 2026) and `ChatGPT-Evaluate_Repository_Progress` (3–8 Sep 2026, 41,532 lines, 131 turns). Current as of **7 Sep 2026**, architecture branch at `56e1497`.

**This atlas has not been checked against the repository.** Every claim about repository state is transcript-derived. Verifying it is the first task in `08-HANDOVER-TO-CLAUDE-CODE.md`.

## Reading order

| | Document | What it is |
|---|---|---|
| **1** | `01-CONSTITUTION.md` | The core ideology. Root positions, invariants, the lock list, the vocabulary. Short, and none of it provisional. |
| **2** | `02-ARCHITECTURE-AND-METHODS.md` | Everything built and everything planned, including the full 7 Sep directive corpus. Status-tagged. Amendments folded in at the point they apply. |
| **3** | `03-PHYSICAL-AND-COMMERCIAL.md` | Dark Factory Physical, the Hestia benchmark ladder, the moat and the business model. |
| **4** | `04-CRITICAL-EVALUATION.md` | Assessment of the 7 Sep decisions: what holds, what is over-locked, what is missing. Read before treating the corpus as binding. |
| **5** | `05-ADOPTION-ACP.md` | The Tier 2 proposal that would make the corpus legitimate. Evidence section deliberately unfilled. |
| **6** | `06-SPIKE-SHADOW-CLAIMS.md` | Two-day experiment settling whether the claim substrate can be introduced incrementally or forces a rewrite. |
| **7** | `07-ADVERSARIAL-BENCHMARKS.md` | K-ADV, CAP-ADV, GIT-ADV, M-ADV, L-ADV as behavioural specs. |
| **8** | `08-HANDOVER-TO-CLAUDE-CODE.md` | State, first tasks in order, working rules, and the opening prompt. |
| **9** | `09-ACP-002-PREFLIGHT-LOOP.md` | Optimising the Preflight algorithm against recorded history. Best target, worst readiness — month six. |
| **10** | `10-ACP-003-COST-RATCHET.md` | The conditional cost ceiling that completes the ratchet family. **The one that could land this month.** |
| **11** | `11-UNCERTAINTY-LIST.md` | Where the atlas is least confident, ranked. Read alongside Task 1. |
| — | `register/` | The 403-decision register, validator and extract tool. Used every session. |

## The register

`register/decisions.json` — 403 decisions across eight registers, each with an ID, status, tier and supersession links. `validate_register.py` enforces integrity; `extract.py` produces per-issue decision packets with transitive resolution.

```
PROPOSED         243     no evidence behind them
SETTLED           78     reopening needs evidence
CONSTITUTIONAL    40     reopening needs an owner decision
BUILT             10     merged and evidenced
AMENDMENT         10     open, from the evaluation
OPEN               9     deliberately undecided
SUPERSEDED         6     replaced
PARTIAL            4     component exists, decision does not
```

## Status vocabulary

**[BUILT]** merged and evidenced · **[CLAIMED]** asserted, not independently evidenced · **[SPEC]** implementation grade, no code · **[AGREED]** conversation only · **[OPEN]** deliberately undecided

## The optimisation architecture

Added 2026-09-08. The system has a monotone safety architecture — floors that only rise, a TCB that only shrinks — and no optimisation architecture at all. Nothing in it says *get better on a number*.

The completion is a third ratchet, not a search:

```
evidence floors    only rise      never worse at proving      BUILT
TCB manifest       only shrinks   never more trusted surface  SPEC
cost ceiling       only falls     never slower at proving     ACP-003
```

And one rule governing when automated search is worth running at all, at every scale from a single probe to an overnight loop: **will the evidence change what I do, by more than it costs to get?** The discriminator is the strength of existing priors, not the size of the prize. See `01-CONSTITUTION.md` and `02-ARCHITECTURE-AND-METHODS.md` Part M.

## Two things to hold onto

**The corpus is not decided.** 400 well-reasoned claims at PROPOSED, awaiting the evidence that promotes or rejects them. Writing is not deciding — that is the precedent the whole system exists to prevent.

**The standing instruction outranks all of it.** No general architecture work until the canary completes or exposes a concrete blocker. The machinery is ahead of the evidence, and one proven Level-4 lap is worth more than any of these documents.
