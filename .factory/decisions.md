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

---

## D-006 · One executable lifecycle

**Status:** recorded · **Raised:** 2026-09-04

`factory_kernel/state.py` defined a twenty-one-stage happy path with wait, decompose, stop and
needs-human outcomes, and `FACTORY.md` listed it as the kernel's "State machine". Nothing
consumed it: `KernelRuntime.build_issue` and `validate_pr` are procedural, and the only caller
was a `state-next` CLI helper that printed the next abstract stage. The stages it named are,
almost one for one, the `required_claims` of `.factory/evidence-spine.json`, which the runtime
does execute and which `harness/merge_verify.py` enforces as an exact sequence before merge.

Two representations of the lifecycle, one executed and one not, is how a document drifts
into a false claim. The unexecuted one is removed rather than wired in: wiring it would add
a parallel trajectory the spine already proves, and every extra authority is another thing
a future reader must check is real. The spine is the lifecycle. Control-plane states outside
it are labels, applied and read by the runtime.

This is a **judgement** structure and moved through the maintainer lane.

---

## D-007 · The unit floor is the observed value for the tree on `main`

**Status:** recorded · **Raised:** 2026-09-04

`unit_tests` in `.factory/locks/floor.json` was 549, measured 2026-08-13 before the
factory's own suite existed. The required `quick-authority` check has since observed 1024
(#40), 1034 (#42) and 1033 (#43) on ubuntu. The floor moves to **1033**, the value for the
head whose tree is `main` today (https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/33870605247), with zero slack, per the
file's own rule. Not 1034: a floor is what the current tree clears, not the highest number
ever seen, and #43 deliberately removed five tests with an unexecuted state machine.

`holdout_assertions` stays 9 (nine `expect()` calls across three scenarios in
`.factory/holdout/run.py`) and the three mutation floors stay 9/3/3 (nine defects, three
`must_catch=[security]`). Nothing structural changed.

No E2E floor yet. `E2E_PASSED steps` has never been observed for a complete autonomous
cycle under the current kernel; the first unattended canary produces it.

While reading the enforcers, the claim in the floor file that a second program asserts
floor(head) >= floor(base) turned out to be false. The note now says what is enforced and
by which program, and FACTORY_RULES section 13 records the gap. This is a **judgement**
value and moved through the maintainer lane.

---

## D-008 · Kernel commits are attributed to the Actions bot

**Status:** recorded · **Raised:** 2026-09-04

Every kernel-made commit carried `Dark Factory <dark-factory@users.noreply.github.com>`. That
address maps to no GitHub account, so on GitHub the author of every factory commit resolved
to null: attributable to nobody, and a candidate for the `main-protection` ruleset's
`require_extra_approval_for_unattributed_changes` rule, which would demand an approval the
autonomous path can never give.

Kernel commits now carry `github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>`,
the address GitHub attributes to the Actions Bot account. That is exactly what the trust-root
guard's second fence already expects of a factory commit (a Bot, never a User), so the change
strengthens the fence rather than weakening it. The identity lives in one place,
`KERNEL_COMMIT_ARGS` in `factory_kernel/worker_policy.py`, used by both commit sites.

Whether the ruleset rule accepts a Bot-attributed commit is still unobserved; the canary
decides it. This is a **judgement** value and moved through the maintainer lane.

---

## D-009 · A dependency is declared in the contract, never discovered in the diff

**Status:** recorded · **Raised:** 2026-09-04

Issue #39: the security guard requires `## Dependency justification` naming each changed
package, the kernel writes every autonomous PR body, and nothing rendered that heading, so an
autonomous PR that needed a package could not merge. Two ways to fix it: let the implementer
write justification prose into an artifact the kernel pastes, or make the dependency part of
the contract. The second is chosen. A package is a product decision with a blast radius, and
the contract is where the factory already refuses ambiguity: `scripts/factory_protocol.py`
validates each declaration's purpose, why existing dependencies are insufficient and
maintenance evidence, fail-closed, before any design exists. The kernel renders the
declaration verbatim (`factory_kernel/pr_body.py`), so the guard and the contract agree by
construction, and it refreshes the lockfile itself (`refresh_lockfiles` in
`factory_kernel/git_authority.py`) because workers have no shell. The refresh runs only when a
planned manifest changed, requires the lockfile to be planned, and refuses if it touched
anything else.

What this does not do: decide whether a package is wise. The contract certifier, the
architecture governor and the blinded holdout still judge that; this only makes an honest
declaration mergeable and a silent one impossible.

---

## D-010 · The model route had never worked; the preflight now proves the real request

**Status:** recorded · **Raised:** 2026-09-04

The first canary dispatch (issue #49, worker run 33876017910) failed at triage with "There's
an issue with the selected model (z-ai/glm-5.3-flash)". Diagnostic PR #50 (runs 33876770089,
33876959027) showed the cause was not the model: the Anthropic SDK inside Claude Code appends
`/v1/messages` to `ANTHROPIC_BASE_URL`, the worker set that base to `https://openrouter.ai/api/v1`,
and every model call requested `/api/v1/v1/messages` and received OpenRouter's HTML 404 page.
The preflight's curl probe hard-coded the correct path and passed for weeks while proving a
request the worker never made. With `https://openrouter.ai/api` the pinned CLI returns `OK`
for GLM-5.3 Flash, DeepSeek V4 Pro and Claude Haiku 4.5, including a tool-using turn.

What the fix proves: at preflight, the pinned CLI, launched as the kernel launches a worker,
reaches each configured model and returns a non-error result. What it does not prove: that
any model is good enough to build, review or judge; that is what the canary measures.

This is a **judgement** mechanism in a protected workflow and moved through the maintainer lane.

---

## D-011 · The preflight must require every label the kernel can apply

**Status:** recorded · **Raised:** 2026-09-04

Second canary dispatch (run 33880138411): the model route was fixed, triage accepted issue #49,
the kernel applied `factory:accepted` and then crashed on `gh issue edit --add-label
priority:medium` because the repository had never had the `priority:*` and `type:*` labels
that `TriageEngine._apply` attaches. The worker preflight checked only the eight `factory:*`
control labels. The issue was left half-applied: accepted, no priority or type label, no triage
comment. The eight missing labels were created by hand so the canary could continue.

The fix makes the label vocabulary a kernel fact (`label_vocabulary`, derived from the same
sets the decision validator enforces) and has the preflight read it from the kernel rather than
from a second hand-typed list, so a label added in code cannot outrun the check. Same class as
D-010: a prerequisite check that verifies something other than what the run will do.
