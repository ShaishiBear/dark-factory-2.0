# Decisions

Product values the factory chose on its own, and the questions it stopped to ask.

**How this file works.** `FACTORY_RULES.md` §7 splits values in two. A **product** value -
a price, a rate, a default, a name, a layout - the factory may choose, record here, and
carry on; the merge is held for a human but the work is not blocked. A **judgement**
value - a lock, a floor, a tolerance, a sample size, a required marker - it may never
choose, because choosing one is tuning the judge.

**Ask a given decision once.** A second issue that needs the same answer references the
ID and carries on. It does not re-ask. An earlier version of this rule told the plan node
to stop for "an answer to any open question", and a PRD that was honest about what it had
not settled blocked every issue downstream of it - four issues, four escalations, zero
PRs, and the same question asked four times. The more honest the spec, the less the
factory could do.

Append only. Newest at the bottom.

---

## D-001 · The e2e floor is not set

**Status:** open · **Raised:** 2026-08-13 · **Blocks:** nothing today

`.factory/locks/floor.json` carries floors for unit, static, holdout and mutations, and
deliberately none for e2e. The canonical `harness/e2e.py` journey now includes the live
API floor plus the browser login → conversation → streaming → citation → exact YouTube
timestamp path, but that expanded journey has not yet been observed on the validation
host.

**Recommendation:** run `python harness/ci.py` on the VPS, where
`/opt/dark-factory/validation.env` and the dedicated validation account exist, and add
`e2e_steps` to the lock at whatever it actually reports. Not before - a floor nobody has
watched the harness clear is the same kind of claim this directory exists to refuse.

This is a **judgement** value, so the factory may not set it. That is the correct
behaviour and it is why this entry exists rather than a number.

---

## D-002 · Section 4's journey lives in two places

**Status:** implemented, pending validation-host observation · **Raised:** 2026-08-13 · **Structural fix:** 2026-08-23

Originally, `FACTORY_RULES.md` §4's agent-browser journey lived only in the validate-pr
workflow while `harness/e2e.py` asserted a smaller HTTP-level floor. That gave "the app
works" two definitions.

The canonical regression now lives in `harness/e2e.py` and is consumed by the full
`harness/ci.py` ladder. It deterministically drives login, a locked RAG question,
streaming, citation metadata, transcript evidence, the citation modal, and the exact
YouTube video/timestamp link. The issue-specific `behavioral-e2e` workflow reviewer is
kept as an additional adversarial holdout rather than as a second canonical definition.

**Remaining evidence:** this code path still has to complete on the validation VPS before
D-001 can gain an observed E2E floor. Structural implementation is not evidence that the
host has successfully run it.

---

## D-003 · Three of four mutations are caught below the independence line

**Status:** open · **Raised:** 2026-08-13

Measured 2026-08-13: `cap-raised-to-100` caught by unit, `uuid-normaliser-dropped` and
`conversations-list-loses-its-guard` caught by static, `lock-key-is-constant` caught by
the holdout. One above the line, three below.

Below-the-line checks are ones the builder can read and edit, so a defect caught only
there is a defect a sufficiently motivated agent could arrange not to be caught. The
cause is the shape of the defects rather than a weakness in the holdout: all three break
type-checking rather than behaviour, and the compiler finds those. Rewriting
`lock-key-is-constant` to be type-clean is what moved it above the line.

**Recommendation:** give the other two the same treatment - a mutation that type-checks
and is behaviourally wrong - and add defects aimed at the RAG and citation path, which is
where DynaChat's value actually lives and where nothing currently probes at all.

---

## D-004 · The validation host is the GitHub-hosted worker, and the trust root has a maintenance lane

**Status:** recorded · **Raised:** 2026-09-03

D-001 and D-002 say "run it on the VPS" and name `/opt/dark-factory/validation.env`.
That host no longer exists as the validation environment. Since the repo-owned kernel
replaced Archon (PR #33), the canonical validation environment is
`.github/workflows/dark-factory-worker.yml`: a disposable `postgres:16` database, a
random JWT secret, a synthetic E2E account and one fixture video ingested per run. The
optional `deploy/systemd/dark-factory.*` units are a scheduling alternative, not a second
environment. Read "VPS" in D-001/D-002 as "the worker".

Their substance stands. The E2E floor is still unset because no complete issue → merge
cycle has been observed under the current kernel with a recorded `e2e_steps` count. The
first canary issue through the full unattended factory is what produces that number; it
is a **judgement** value and moves only through the human lane.

The human lane itself is new (PR #37). Until then every protected-path change, including
correcting this log, was unmergeable: the required `quick-authority` check refused
protected paths for everyone and the ruleset forbade direct pushes. `FACTORY_RULES.md` §5
now records the two authorities and §13 lists what the rules describe but the kernel does
not yet enforce.

---

## D-005 · The judge runs from `main`, and nobody presses Merge

**Status:** recorded · **Raised:** 2026-09-04

PR #40 moved trust-root authority to `.github/workflows/dark-factory-trust-root.yml`, a
`pull_request_target` workflow that checks out the base tip and runs the guard in
`--trusted-base` mode, and armed GitHub auto-merge for maintainer-lane PRs bound to the
exact judged head. It could not be judged by itself, so a delegated maintainer session
merged its exact head with an expected-head squash and the required check was added to the
ruleset afterwards, bypass list still empty.

This PR (#41) is the proof. Its first head was judged from `main` by run 33866228355
(`lane=human-maintenance`, `binding.mode=trusted-base`), armed, then deliberately
superseded by this commit before `quick-authority` could go green on it. The earlier
authorisation merged nothing; the workflow re-judged and re-armed this head; GitHub merged
it once both required checks were green here. A manual attempt to arm auto-merge with a
wrong `expectedHeadOid` was refused by GitHub.

This is a **judgement** mechanism and lives in protected files; it moves only through the
maintainer lane. Maintainer merges still get no post-merge full harness and the
`require_extra_approval_for_unattributed_changes` ruleset flag is unverified against kernel
commits (FACTORY_RULES.md §13). The first unattended canary settles the second.
