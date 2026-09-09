# Handover to Claude Code

*Everything a fresh session needs to pick this up. Written 2026-09-08 from the full transcript record; the repository has not been read.*

---

# PART 1 — STATE

## Where the project is

`dark-factory-2.0`, private, standalone, unforked. A repository-owned Python kernel (PR #33) replaced the Archon-shaped runtime. The evidence spine, Architecture Governor, method layer, GitHub App identity and mutation/immunity system are built and evidenced.

**The one thing that matters:** a complete Level-4 lap has never been observed end to end under the current production kernel. Level 4 is architecturally supported and not yet empirically earned. Issue #49 — the canary — is the instrument for earning it, and it has already exposed two real integration defects that every prior static check missed (PR #51 OpenRouter routing, PR #52 label vocabulary).

Last known positions: PR #149 opened by `app/shaishibear-dark-factory` proved the App identity path end to end; PR #150 held as draft; #134 in the re-head sequence. Unit floor 1033, anchors 428/9/26, `e2e_steps` deliberately absent pending the first complete cycle.

**All of this is up to a day stale and none of it has been verified against the repository.**

## The standing instruction

> Stop general factory architecture work until the canary either completes or exposes another concrete blocker.

The architecture has enough machinery. More abstraction now risks optimising something that has not completed one real lap. This instruction outranks everything in the corpus.

## What happened on 7 September

Roughly 400 architecture decisions were produced in a single day, in parallel with implementation work, and committed to `architecture/dark-factory-2-target` (`ffa6623`, `357aca5`, `04eb0b6`, `d2eeda7`, `bc133cf`, `dcd815f`, `cf49319`, `56e1497`). `main` was not moved and no PR was opened.

They are good decisions. They also arrived outside the governance process they define, and **243 of them have no evidence behind them at all.** They are not yet binding.

---

# PART 2 — THE DOCUMENTS

| File | Read it when |
|---|---|
| `01-CONSTITUTION.md` | First, and again whenever a decision feels hard. Short. |
| `02-ARCHITECTURE-AND-METHODS.md` | The full picture: built, specified, and the directive corpus. Amendments folded in. |
| `03-PHYSICAL-AND-COMMERCIAL.md` | Only for the second domain. Not needed for factory work. |
| `04-CRITICAL-EVALUATION.md` | Before treating the corpus as binding. |
| `05-ADOPTION-ACP.md` | The proposal that makes the corpus legitimate. Evidence section unfilled. |
| `06-SPIKE-SHADOW-CLAIMS.md` | The two-day experiment that settles incremental-vs-rewrite. |
| `07-ADVERSARIAL-BENCHMARKS.md` | When implementing any trust-boundary test. |
| `09-ACP-002-PREFLIGHT-LOOP.md` | Month six. Not now — best target, worst readiness. |
| `10-ACP-003-COST-RATCHET.md` | **Now.** Completes the ratchet family. Cheapest high-value item available. |
| `11-UNCERTAINTY-LIST.md` | With Task 1. Eight ranked checks, most load-bearing first. |
| `register/` | Every session. It is the working index. |

---

# PART 3 — FIRST TASKS, IN ORDER

## Task 1 — Verify the register against reality

**Highest priority. Do this before anything else.**

Every non-`PROPOSED` status in `register/decisions.json` claims something about the repository, and every one is transcript-derived. Ten `BUILT`, four `PARTIAL`, six `SUPERSEDED`, plus the floors and PR states in `02-ARCHITECTURE-AND-METHODS.md` Part A.

Work `11-UNCERTAINTY-LIST.md` first — eight ranked checks, ordered by how much breaks if they're wrong. Then sweep the remaining statuses.

For each: verify, correct, and record the PR or commit that evidences it. Downgrade anything aspirational. **Report what you found wrong** — that list is more valuable than the corrections themselves, because it calibrates how much of the rest to trust.

A register that overstates what exists violates the honesty rule it is meant to enforce.

## Task 2 — The three cheap amendments

`DFE-002`, `DFE-003`, `DFE-004`. Minutes each. They change what "decided" means, so they land before anyone implements from the corpus.

- Supersession headers on both superseded roadmaps, naming the six-phase ordering as current.
- Lock list split into constitutional and settled; "one candidate at a time" demoted to a default.
- Canonical contracts split into normative Phase 1 and proposed Phases 2–5.

## Task 3 — Instrumentation and retention

`DFM-026` plus per-stage timing. Retention 7 → 90 days is a config change; verify the repository maximum supports it.

**This is more urgent than it looks.** Retention at seven days is currently deleting the dataset for three separate future capabilities — the cost ratchet, any tail search, and Preflight tuning — and every lap run before it lands is a training example gone permanently. It is urgent whether or not any of those is ever built.

Alongside it, add per-stage wall-time capture to the trajectory on **every** exit path including failures: build stages, validation groups, the five authorities individually, mutation shards, post-merge, and end-to-end. Timings go in the trajectory, never in trusted evidence — they are measurements about a run, not claims about a subject.

This is the precondition for `10-ACP-003-COST-RATCHET.md`, which is the cheapest high-value item currently available and the only one that could land this month.

## Task 4 — The three cheap adversarial tests

`CAP-ADV-4`, `K-ADV-2`, `K-ADV-4` from `07-ADVERSARIAL-BENCHMARKS.md`. They guard properties that already exist unprotected. Each must first be shown to fail against a deliberately weakened kernel.

## Task 5 — The DFC-074 comparison

Fill §2 of `05-ADOPTION-ACP.md`. Every proposed schema against existing repository artefacts: collision, redundancy, or already solved. Expected outcome, stated in advance so it can be wrong: most Phase 2–5 schemas come back *defer, no consumer*.

## Task 6 — Spike S-001

`06-SPIKE-SHADOW-CLAIMS.md`. Two days, hard stop. Settles whether the claim substrate can be introduced incrementally or forces a rewrite. **A failed spike is a successful spike.**

---

# PART 4 — WORKING RULES

**Name decisions in every PR.** Generate with `python3 register/extract.py <IDs> --markdown`. Resolution is transitive, so an issue about probes cannot silently omit the amendment that replaced the probe rule.

**Contradiction requires an ACP.** If work would contradict a `SETTLED` or `CONSTITUTIONAL` decision, stop and produce the six-field amendment: decision affected · new evidence · why the existing decision fails · alternatives · recommended amendment · consequences. Do not silently drift the architecture.

**Escalate on ownership, not on ignorance.** Never ask about PostgreSQL versus SQLite — benchmark it. Always ask when the trade is cost against a weaker guarantee.

**Promotion is an event.** When a decision acquires evidence, change its status and record what promoted it. `PROPOSED → BUILT` needs a PR number. Never promote by editing the title.

**Documentation is a live input.** Stale docs are read by workers and are therefore a correctness defect, not hygiene debt. This is rated the largest quality risk to the agents themselves.

**Do not build the intelligence layer yet.** Front Door, Preflight, decision graph and UI are specified and unbuilt, and they sit behind the canary, the kernel reduction and concurrency in the dependency chain.

---

# PART 5 — THE FIVE THINGS MOST LIKELY TO GO WRONG

1. **Treating the corpus as decided.** It is 400 claims at PROPOSED. Writing is not deciding.
2. **Implementing schemas that have no consumer.** The corpus warns against this and then specifies 49 of them.
3. **Letting `ReheadAuthority` learn claim types.** The most likely place the TCB quietly regrows. Adopt DFE-008's amended form only: a query over the dependency model.
4. **Resuming architecture work before the canary lands.** The standing instruction exists because the machinery is ahead of the evidence.
5. **Believing this handover.** It was written without reading the repository. Task 1 exists for this reason.

---

# PART 6 — OPENING PROMPT

*Paste this as the first message of the Claude Code session, with the files attached or in the repo.*

---

I'm continuing work on Dark Factory 2.0. Read these first, in this order: `01-CONSTITUTION.md`, then `08-HANDOVER-TO-CLAUDE-CODE.md`, then `register/DECISION_REGISTER.md`. Skim `02-ARCHITECTURE-AND-METHODS.md` for structure; don't read it end to end yet.

Context: on 7 September about 400 architecture decisions were produced in one day and committed to `architecture/dark-factory-2-target`. `main` was not touched. Those decisions are good but they arrived outside the governance process they define, and 243 have no evidence behind them. They are recorded in `register/decisions.json` at status PROPOSED. Nothing in the corpus is binding until adopted via `05-ADOPTION-ACP.md`.

Everything in the handover about repository state came from conversation transcripts, not from reading the repo. Some of it will be wrong.

**Start with Task 1: verify the register against the actual repository.** Check every decision not at status PROPOSED — the 10 BUILT, 4 PARTIAL, 6 SUPERSEDED — plus the floors, PR states and commits in Part A of the architecture document. Correct `decisions.json` where it's wrong, record the PR or commit that evidences each surviving claim, and downgrade anything aspirational.

Then tell me what you found wrong, before doing anything else. That list matters more than the corrections, because it tells us how much of the rest to trust.

Don't start any architecture work. There's a standing instruction not to resume general architecture work until the canary (#49) completes or exposes a concrete blocker, and that outranks everything in the corpus. Also don't implement any schema that doesn't have a consumer.

Run `python3 register/validate_register.py` after any edit to the register — it must still validate.

---

## Notes on running the session

**Attach or commit all of `register/`.** `extract.py` and `validate_register.py` are how the register stays honest; without them it becomes a document nobody updates.

**Don't paste the whole corpus into context.** That is the context-window decay named in the original 1.0 diagnostic, and `extract.py` exists to prevent it. Pull per-task extracts.

**When the canary lands**, the immediate action is to ratchet `e2e_steps` to the observed value with zero slack, and only then consider architecture work.

**If Task 1 finds the register substantially wrong** — say more than a third of the non-PROPOSED statuses — stop and reassess before proceeding. That would mean the transcript record diverged from reality more than expected, and the rest of the corpus deserves the same suspicion.
