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

---

## D-012 · The kernel alone heartbeats the lease; contract and proof hold no credentials

**Status:** recorded · **Raised:** 2026-09-04

Third canary dispatch (run 33880438107): triage and labels were fixed, the build started, and
`scripts/factory_protocol.py contract` died at `LEASE_ERROR gh repo view ... set the GH_TOKEN`.
The contract compiler started the issue lease itself, which needs `gh`, while the kernel ran it
with no credentials. That scope was right: `factory_proof.py` executes checkpoint commands the
model authored, and a program that runs model-authored commands must never hold a repository
token. The lease heartbeat and the model-authored command were sharing a process.

Adopted the architecture of PR #53: `KernelRuntime._lease_heartbeat` is the only build-side
subprocess with GitHub scope, called by the kernel after contract, context, RED, GREEN, final
GREEN and PR handoff; the script-owned `lease()`/`heartbeat()` helpers are deleted rather than
short-circuited, so a missing lease cannot be silently ignored; and `factory_proof.run` scrubs
GH_TOKEN and GITHUB_TOKEN from the checkpoint child as defence in depth. The two `attach`
programs keep GitHub scope because they edit the PR through `gh` and run nothing model-authored.

PR #53's commits were authored by `google-labs-jules[bot]` and the trust-root guard's second
fence refused them, which is the fence working: the change was re-authored under the
maintainer's identity with the reviewer's cleanups. An AST test now pins the scope of every
protocol/proof call in `build_issue` and the exact heartbeat sequence, so a future edit cannot
quietly hand the token back.

---

## D-013 · Methods are pinned text, not a plugin

**Status:** recorded · **Raised:** 2026-09-04

`docs/agents/matt-skills.md` said the builder used the real `mattpocock-skills` plugin and that
a workflow preflight failed closed without it. In autonomous execution neither was true: the
kernel launches every worker with `--bare`, an empty strict MCP configuration and slash commands
disabled, the worker workflow installs no plugin, and no preflight checked for one. The context
worker was told to read that document, so a worker was being told it had a capability its
launcher deliberately removes.

The isolation stays; it is part of the trust root. The disciplines those skills describe now
reach workers as plain text: `.factory/methods/manifest.json` records each method's source and
adaptation and which roles receive it, `factory_kernel/methods.py` validates it fail-closed, and
the kernel injects the role's text between the role prompt and the run context. The directory is
protected by the security guard. Several of Matt Pocock's ideas were already reimplemented
independently (contract compiler, deterministic triage, architecture governor); what this adds
is the explicit text for minimal complexity (Ponytail's ladder), deep-module design, vertical
slice implementation inside the frozen acceptance contract, red-loop diagnosis, and the two
review axes. Two follow-ups make the last two executable: separate spec and standards reviewers
(D-014) and a kernel-executed repro loop for bugs (D-015).

`.claude/settings.json` keeps the plugin registration for interactive human sessions only.

---

## D-014 · Spec and Standards are reviewed by separate processes

**Status:** recorded · **Raised:** 2026-09-04

The single `review` worker was told to judge two axes "independently" inside one context. One
model weighing "does it do what the contract says" and "is it built well" together lets one
impression colour the other; a change that passes its tests reads as well built, and a
well-built change reads as correct. The two axes now run as `review-spec` and
`review-standards`, fresh processes with disjoint prompts, disjoint method text and disjoint
artifacts. `factory_kernel/review.py` aggregates deterministically and fails closed: a missing,
malformed or mislabelled artifact, or a verdict that contradicts its own findings, escalates;
either axis failing fails the review. The rest of the ladder (one repair, second review, GREEN
replay, conformance, holdouts, certifiers) is unchanged.

---

## D-015 · A bug goes red before it is contracted

**Status:** recorded · **Raised:** 2026-09-04

The investigate worker could not run commands, so it proposed a repro and a root-cause
hypothesis without ever seeing the failure. For simple bugs that is fine; for ugly ones it is
a confident guess. The worker now also writes `repro.json` (an allowlisted program, no shell, a
repo-relative cwd, and the exact symptom substring), and the kernel executes it in the build
worktree with no credentials before the contract stage. The run continues only if the command
fails and its output contains the symptom; the observation (`repro-observed.json`: argv, cwd,
exit code, output digest, matched symptom) is passed to the contract worker as fact, while the
investigation's hypotheses remain hypotheses. A repro that passes, misses the symptom, names a
non-allowlisted program or escapes the checkout escalates the issue: a bug that cannot be made
to go red cannot be contracted, and escalating early is cheaper than a confident wrong fix.

---

## D-016 · The credential-free programs read a kernel snapshot of the issue

**Status:** recorded · **Raised:** 2026-09-04

Fourth defect the canary (issue #49) revealed, attempt 2, run 33896546840. D-012 took GitHub
credentials away from the contract, context and proof programs, correctly: they run
model-influenced compilation and model-authored checkpoints. The ticket/frontier compiler
inside the context stage then died, because it read the issue and each `Blocked by: #N`
blocker through `gh issue view`.

The fix keeps the privilege boundary and moves the fetch: `build_issue` resolves the issue
and its blockers with the kernel's own authority and writes `issue-frontier.json` before any
model stage; `factory_artifacts.py ticket` takes `--issue-json` and judges readiness from
that snapshot, refusing a missing file, a snapshot for another issue, or a blocker list that
does not match the `Blocked by` lines in the body. The only `gh` calls left in the build
programs are the two attach paths, which edit the PR and run nothing model-authored; the
kernel gives exactly those GitHub scope.

Each canary defect so far was a program that worked when everything held one credential and
broke the moment the boundary was drawn. That is the boundary doing its job; the canary is
finding every place the old design leaned on it.

---

## D-017 · The full harness re-runs on `main` daily; drafts are judged but not armed; merged branches are deleted by the workflow

**Status:** recorded · **Raised:** 2026-09-04

Three operational gaps, each found by watching the factory run rather than by reading it.

**Periodic regression.** FACTORY_RULES section 13 recorded that nothing re-ran the full
harness on `main` after a merge. A maintainer merge lands on the head-based quick gate only,
and a drift outside the repository (the locked fixture video, a model, an API, the runner
image) would surface only on the next autonomous cycle. `dark-factory-main-regression.yml`
now runs `python harness/ci.py` on current `main` once a day with the worker's exact pins
and disposable environment, copied verbatim and pinned by a test that parses both files.
On failure it files one issue for ordinary triage, comments rather than duplicates, and
escalates to `factory:needs-human` on a second consecutive failure. It fixes nothing and
merges nothing; that is a **judgement** boundary. The count it observes is not written into
the floor file by the job: an observed E2E step count is a judgement value and moves only
through the human lane.

**Drafts.** GitHub refuses `enablePullRequestAutoMerge` on a draft, so the diagnostic draft
PR #50 turned the optional `unattended-merge` job red for a correct outcome. The job is now
skipped for drafts; the required `trust-root-authority` verdict is unchanged.

**Merged branches.** `delete_branch_on_merge` is on, yet ten `human/*` branches accumulated
and were deleted by hand. The setting evidently does not fire when GitHub's auto-merge
completes a merge on behalf of the Actions app. The trust-root workflow now deletes the
head ref on the `closed` event, only if the PR merged and only if the head is in this
repository.

---

## D-018 · A conflicting PR is refused loudly by the base-run authority

**Status:** recorded · **Raised:** 2026-09-04

PR #46 became CONFLICTING after three other PRs landed. GitHub runs no `pull_request`
workflow on an unmergeable PR, so `quick-authority` never reported, while
`trust-root-authority`, a `pull_request_target` job that runs on the base, stayed green. The
PR sat with one green check and nobody was told anything.

The fix is in the job that does run: after the verdict, `trust-root-authority` asks GitHub for
`mergeable_state` and fails the required check on `dirty` with a reason. The verdict on the
diff is untouched; mergeability is a second fact and is reported as such. `unknown` is retried
six times five seconds apart and then tolerated, since GitHub computes it asynchronously and
an unknown state is not evidence of a conflict.

Not chosen: the ruleset's require-branches-up-to-date setting. It would make every unrelated
PR rebase after each merge and re-run every gate on each advance of `main`; the factory merges
many small PRs and that serialisation would cost more than the stall it prevents. A merge
queue remains a future option if PR volume ever makes rebases the bottleneck.

---

## D-019 · A model-authored repro is bounded by shape, environment and an unchanged tree

**Status:** recorded · **Raised:** 2026-09-04

D-015 made a bug go red under kernel execution before it is contracted. Review of that change
found the boundary around the executed command was a program-name allowlist: `python`, `uv`,
`bun`, `npx`, `pytest`. `python -c` is arbitrary code, so that was no boundary. The child
environment was the parent's minus two GitHub tokens, so `OPENROUTER_API_KEY`,
`SUPADATA_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and the validation credentials all reached the
command. And the command ran in the builder worktree with nothing checking that the tree was
unchanged before the contract worker read it.

Three bounds replace the name list. The argv must start with one of the repository's test
runners (`pytest`, `python -m pytest`, `uv run pytest`, `uv run python -m pytest`, `bun test`,
`bun run test`, `bunx vitest run`) and every further argument is refused if it is an eval or
exec flag, contains a shell metacharacter, is an absolute path or escapes the checkout. The
environment is built from an allowlist of eleven benign names plus one synthetic variable, so
the next secret added to the worker is withheld by construction. The kernel compares
`git status --porcelain --untracked-files=all` before and after and escalates on any change.

What this does not claim: a test file the command selects can still run arbitrary code. The
bounds limit what that code can reach (the checkout, CPU for the timeout, stdout) and what it
can leave behind (nothing). The command's output is evidence of what it printed and how it
exited, not a trusted judgement. Six mutations attack the three bounds; IMM-013 records the
class.

---

## D-020 · Workers are bounded, briefed and measured

**Status:** recorded · **Raised:** 2026-09-04

The first canary build (issue #49, worker run 33899592399) spent about twelve minutes per model
stage. A read-only audit found the prompts small (the largest assembled prompt under 10k chars)
and the time elsewhere: no turn cap on the CLI, so the only backstop was the 20-minute subprocess
timeout; the `context` worker handed only the contract's hash and told to name every file the
implementation may touch, so it rediscovered the whole task from a 400-file checkout; three
prompts ordering CLAUDE.md and FACTORY_RULES.md (87k chars) read before anything else, and one
ordering "recent history" a Bash-less worker cannot obtain; cold `uv` and Bun caches on every
hourly run; and no per-stage timing anywhere, so none of this could be read after the run.

Five changes, none touching an authority, an isolation flag, the tool policy, the blinding or an
evidence step. Each role gets a turn cap (`ROLE_MAX_TURNS`) passed as `--max-turns`; the CLI
returns a JSON result envelope and an error envelope is a failed stage. Post-contract workers
receive the validated contract and the original issue in the prompt, hash first. The prompts say
"search before reading whole files" and point at the sections that matter. The workflows cache the
uv wheel store and the Bun package store keyed on the lockfiles, still installing frozen. Every
stage writes its wall time and the model's own telemetry, and the worker uploads transcripts as a
7-day artifact.

What this claims: the loop is bounded and the time is visible. What it does not claim: that any
cap is the right number. The caps are first estimates; the next canary's `stage-timings.jsonl`
is the evidence for adjusting them, and a cap is a trust-root change.

**Merged-branch cleanup, corrected.** D-017 added a `delete-merged-branch` job on the trust-root
workflow's `closed` event. It never ran: on #59, #60 and #61 no workflow run was created for the
`closed` event at all. GitHub's auto-merge closes the PR with the Actions token, and events
caused by `GITHUB_TOKEN` do not start workflows, so neither `pull_request_target` nor
`pull_request` can see that close. The dead job and the `closed` trigger are removed. Cleanup is
now a small hourly workflow (`dark-factory-branch-cleanup.yml`) that lists this repository's
`human/*` and `factory/*` branches, keeps only those whose tip is exactly the head of a merged
PR from this repository, and deletes them; `main` is never a candidate and a branch with commits
past its merged PR is left alone. Chosen over a PAT because no new credential is needed, and over
a step in the daily regression because that workflow deliberately holds `contents: read`.
## D-021 · The ratchet only goes up, and a program says so

**Status:** recorded · **Raised:** 2026-09-04

`.factory/locks/floor.json` once claimed "a second check asserts floor(head) >= floor(base)".
PR #44 found no such program and said so honestly; section 13 then carried the gap: a
maintainer PR lowering a floor would pass every check, and the human lane was the only
control. A ratchet that the maintenance lane can lower is a dial.

The check now lives in the base-anchored trust-root guard, because that is the one program
that runs from `main` on every PR, both lanes, with the base and head SHAs in hand. When the
floor file is in the diff, every numeric key at the base must exist at the head and be at
least as high; notes are free; new keys are how a floor is first measured. The human lane
waives the protected-path veto and nothing else, so this refusal binds maintainers too. The
check is a protected path with its own mutation; removing it fails the factory suite.

`harness/harness.config.json` carries no floors (only `e2e_timeout_s`), so it is not compared.

---

## D-022 · A build that succeeds and cannot open its PR

**Status:** recorded · **Raised:** 2026-09-04

Canary attempt 3 on issue #49 (worker run 33899592399) was the first run to complete the
whole build: executed repro, contract, context, governor, RED, implementation, both review
axes, conformance, final GREEN, quick gate, and a push of
`factory/issue-49-a1-dea37cd1cc` carrying a three-line fix and four acceptance tests. It
then failed at `gh pr create`: "GitHub Actions is not permitted to create or approve pull
requests". The repository setting `actions/permissions/workflow.can_approve_pull_request_reviews`
had never been turned on; every autonomous PR would have died at the same step.

The setting was flipped by hand with the owner's credentials. The worker preflight now asks
for it before dispatching, honestly: an explicit `false` refuses the run; `true` prints
`FACTORY_PREFLIGHT_PR_PERMISSION_OK`; a token that cannot read the setting prints
`FACTORY_PREFLIGHT_PR_PERMISSION_UNVERIFIED` and continues. That last branch exists because
the endpoint needs `administration:read` and the default `GITHUB_TOKEN` does not carry it,
so on the canonical worker the check is expected to report unverified. What the preflight can
prove is bounded by what its token can read; the requirement is recorded in FACTORY.md and
FACTORY_RULES §8 so the next repository does not learn it from a two-hour build. A refusal
made advisory is a factory mutation.

---

## D-024 · The red loop is mandatory; where it is observed is not fixed

**Status:** recorded · **Raised:** 2026-09-04

Canary attempt 4 on issue #49 (worker run 33908589032, the first run on the capped kernel)
failed at its first model stage: `bug repro refused: investigate worker wrote no repro.json`.
The worker was right. The bug is a wrong return value from `formatCitation`; no existing test
covers it, so no allowlisted runner can fail on the unchanged tree, and a `repro.json` would
have asserted an expectation the worker had source-level evidence against. The D-015 gate, as
written, admitted only bugs that already crash an existing command. That is the rarer kind.

The factory already proves the red loop for the other kind: the independent test author writes
acceptance tests and `factory_proof.py red` proves them failing on the unchanged tree. The gate
was demanding, two stages early, evidence the pipeline produces two stages later.

The investigate worker now writes exactly one of two records. `repro.json` when an existing
command fails today; the kernel executes it as before. `repro-deferred.json` when none can: the
reason, the seam, and the exact symptom the acceptance tests will print. The kernel validates
the deferred record, hands it to the contract worker as a deferred red loop (the contract must
state the symptom in the relevant `then`), and after RED refuses to continue unless at least one
checkpoint's recorded failing output contains the symptom (`verify_deferred_in_red`;
`factory_proof.py red` now keeps a bounded output tail per checkpoint). Both records present, or
neither, escalates.

What stays true: a bug that cannot be made to go red cannot be contracted, and the red loop is
observed by a deterministic program, never believed. What changed: the observation happens where
the tests that demonstrate it exist.

---

## D-025 · A cap the timeout would beat is not a cap

**Status:** recorded · **Raised:** 2026-09-04

The first per-stage telemetry (worker run 33908589032, issue #49, investigate on
`z-ai/glm-5.3-flash`): 25 turns, 846 s, 419,517 input and 62,844 output tokens, CLI-reported
$4.00. That is 33.85 s per turn. `provider.timeout_seconds` is 1200, so the subprocess timeout
fires at roughly 35 turns. The caps D-020 set (investigate 60, context 80, test_author 60,
implement 120, repair 80, plan 60, reviews 40) could never be reached, and the way the timeout
ended a stage was the worst available: `subprocess.TimeoutExpired` escaped the provider with no
result envelope and no telemetry, whereas the CLI stopping at `--max-turns` returns an envelope
the kernel records as a clean, measured failed stage.

Changes: every cap now fits under the timeout at a 35 s/turn ceiling (`OBSERVED_SECONDS_PER_TURN_CEILING`,
`assert_caps_fit_timeout`, tested against the checked-in config); implement 120 → 30, context
80 → 24, investigate/plan/test_author 60 → 30, repair 80 → 30, reviews 40 → 30. The provider
now catches the timeout and raises with the role, elapsed seconds, the configured timeout and
the partial output. A per-role `--max-budget-usd` (`ROLE_MAX_BUDGET_USD`) backstops the cost
that turns alone do not bound: each turn resends the conversation, so cost grows with the
square of the turn count. The preflight probe proves the pinned CLI accepts the flag.

What the cost figure is and is not. Arithmetic on the observed run rules out 25 uncached full
resends (that would exceed the billed input alone). Either `num_turns` counts messages rather
than round-trips, or a cached prefix is invisible in `input_tokens`. The envelope now keeps
`cache_creation_input_tokens` and `cache_read_input_tokens`, which decides between those
readings on the next run. Separately, `total_cost_usd` for a model the CLI does not price is
almost certainly a fallback-table figure ($8.29 per million counted tokens, an order of magnitude
above a flash-class list price). **No budgeting or model decision may be made on it until it is
reconciled against the OpenRouter dashboard for a known run.**

The investigate prompt's paragraph describing the executor's refusal rules provoked the worker
to read `factory_kernel/repro.py` and `.factory/decisions.md` to verify them, 64% of everything
it read. The rules are now stated flat, and the prompt says not to read kernel source or this log
to check them.

These are **judgement** values and moved through the maintainer lane.
## D-026 · Prompts are rendered with absolute run paths; `$ARTIFACTS_DIR` is a placeholder, not a variable

**Status:** recorded · **Raised:** 2026-09-04

Canary attempt 5 (worker run 33910993905, issue #49) failed at the first model stage with
`factory worker left the worktree dirty: ?? $ARTIFACTS_DIR/`. The investigate worker had done
its job: seven turns, the deferred-repro shape from D-024 chosen correctly. Then it wrote the
record to a directory literally named `$ARTIFACTS_DIR` inside the build worktree, because that is
what the prompt said and nothing on the worker's side expands a shell variable. Every prompt
under `.factory/prompts/` and the methods under `.factory/methods/` name outputs that way, 37
times. Earlier attempts had inferred the real path from the environment; the capped, briefed
worker of D-020 followed the text literally instead of guessing, which is the behaviour we want
from a worker and exactly why the prompt must not need guessing.

The kernel now renders every placeholder itself. `factory_kernel/prompt_render.py` substitutes
`$ARTIFACTS_DIR` / `${ARTIFACTS_DIR}` (and the other request-local names the provider forwards:
`FACTORY_BASE_REF`, `FACTORY_REPO`, `FACTORY_WORKDIR`) into the assembled prompt in
`WorkerControlledRuntime._agent`, before `provider.run`. The artifacts path must be absolute and
exist, because it is also the one directory the CLI is told it may write to. Any other
`$UPPER_NAME` left in a prompt refuses the launch, so a new placeholder can never reach a worker
unexpanded. After a non-mutating role runs, a literal `$ARTIFACTS_DIR` entry in the porcelain
status is named as this failure class rather than as an anonymous dirty tree.

The prompts keep `$ARTIFACTS_DIR` as their placeholder by contract; a test pins that the set of
placeholders in the checked-in prompts is a subset of what the kernel renders, and that the
renderable set equals the provider's request environment. Three trust-root mutations attack the
substitution, the unknown-placeholder refusal and the named failure.
## D-023 · Refusals are facts; a moved base is re-headed without a model; no repair loop yet

**Status:** recorded · **Raised:** 2026-09-04

A read-only design scout for the validation-side fix loop (FACTORY_RULES §13) found that
nothing could be built on: `_exec` raised a bare `RuntimeError`, `_record_validation_failure`
kept only `type(exc).__name__`, the PR comment promised a transcript "on the host" that an
ephemeral runner discards, and the worker's artifact upload excludes the two logs that carry
the reason. A security-guard veto and a base that moved under the PR were indistinguishable.
The scout also found that a rebuild after any refusal re-spends about seventy percent of a
build's turn budget on stages that were independently certified and did not fail.

Two things are done here, and one is deliberately not.

**Refusals are typed and durable.** `_exec` raises `ToolRefused` (a `RuntimeError`, so every
handler keeps working) carrying the program, subcommand, rc and tail. `validate_pr` tracks the
stage it is in; `factory_kernel/refusal.py` turns stage and refusal into one of a fixed set of
reason codes and the authority that speaks for it. The PR comment carries the code and authority
behind an HTML marker; a scrubbed `validation-refusal.json` (every secret shape the guard knows
is redacted) goes into the run's uploaded artifacts. The false "remains on the host" sentence is
gone.

**A stale base is re-headed, model-free.** The three programs that say main moved under the PR
(`provenance.py`, `merge_verify.py pre`, `factory_evidence.py`) produce `stale_base`. That code
does not write the validation-failed marker on the issue: main's motion is not the build's
defect and must not spend its rebuild budget. On the next dispatch, after review PRs and before
any new build, the kernel fetches and re-verifies the builder's provenance pack at the judged
head, creates a blinded worktree, rebases onto current main, checks that every RED-hashed
acceptance test is byte-identical, replays GREEN, runs the conformance worker and compiler,
replays the final GREEN and the quick gate, pushes with `--force-with-lease` naming the judged
head (the one legitimate non-fast-forward push in the kernel), re-attaches contract and proof,
republishes the provenance note at the new head, and hands the PR back to `factory:needs-review`.
Validation then runs in full and reuses nothing; the bindings in `independence.py` and
`provenance.py` would refuse reuse anyway. One re-head per PR; a second stale refusal is a
human's problem. A rebase conflict is too.

**No model repair loop.** The scout's ranking was explicit: build the model-free case first,
then measure. The most repairable failure class is the holdout suite, and it is exactly the one
that must never be fed back to a builder-path worker: the builder is blinded to those scenarios
by construction, and repairing against them would turn a one-shot blinded judge into an oracle
the builder can iterate against. Whether any other class deserves a loop is a question the
recorded reason codes will answer; until they do, the honest §13 line is that refusals are
classified and a repair loop is deferred, not that none is wanted.

---

## D-027 · A correct contract refused for its spelling

**Status:** recorded · **Raised:** 2026-09-04

Canary attempt 6 (worker run 33912650468, issue #49) cleared the deferred repro in five
turns, had its prompt rendered with real paths, and the contract worker wrote a complete,
correct contract in six turns: three behaviours on the right seam, six invariants pinned to
existing tests, no ambiguities. `scripts/factory_protocol.py contract` refused it with
"contract needs at least one observable behavior" because `behaviors` was an object keyed by
AC id and the compiler required a list of objects each carrying `id`. The prompt said
"`behaviors` as `AC-N` objects", which reads as a keyed map; attempt 3 had guessed the list.

The seventh canary defect, and the cheapest: a prompt described a shape ambiguously and a
deterministic gate refused a contract whose content was exactly what the gate wanted. Two
fixes, both kept: the prompt now shows the whole file as a JSON skeleton and says in one
sentence that `behaviors` is a list, and the compiler normalises the keyed spelling to the
list before validation and hashing, refusing a non-AC key or a conflicting inner `id`. The
canonical hash, the compiled file and every downstream consumer see only the list form. The
refused raw contract is checked in as a fixture and must compile with `criteria=3`.

A deterministic gate should refuse wrong content, not a second spelling of right content.

---

## D-028 · A prompt shows the shape its validator accepts; the kernel supplies what a worker cannot compute

**Status:** recorded · **Raised:** 2026-09-04

Canary attempt 6 (worker run 33912650468) refused a complete, correct contract because the
prompt said "`behaviors` as `AC-N` objects" and the worker wrote a dict keyed by AC id while the
compiler wanted a list (D-027). A read-only audit then compared every worker-written artifact's
prompt against the program that validates it and found the same class fourteen more times. This
change lands the resolutions:

- The kernel renders only the text it wrote (preamble, role prompt, pinned methods); the
  untrusted `context` (issue body, repro record, review JSON) is appended unrendered, so an
  issue that mentions `$PATH` or `$GITHUB_TOKEN` no longer refuses every stage before a model
  runs.
- `investigate.md` no longer lists `-x` (an exec flag the repro validator refuses), lists every
  allowed shape, and states the 2000-character RED tail the deferred symptom is matched in;
  `diagnosing-bugs.md` names only runner shapes and both repro records.
- `architecture.md` and `conformance.md` show the JSON skeleton: `rationale` is an array,
  `required_changes` is `[]` for `proceed`, conformance `findings` are plain strings coupled to
  the verdict, and the policy ID rule is stated exactly as `applicable()`/`overlaps()` compute
  it. The kernel additionally hands the governor the computed sets in its brief; the compiler
  still recomputes and refuses any mismatch.
- `context.md`: `ac_mapping` values are arrays even for one seam; no duplicates in any array.
  `contract.md`: the four arrays are string arrays.
- Reviewers and the conformance authority receive the merge-base diff in their invocation
  context (bounded to 60 000 characters plus a stat); they have no shell to compute one.
- `repair.md` has an explicit "nothing to change" path: fail rather than finish clean.
- `factory_proof.py`'s test-oriented predicate now equals `git_authority`'s, so a `.spec.` or
  `__tests__/` acceptance file cannot be committed and then refused at RED.
- The kernel refuses a plan/investigate stage that wrote no note; nothing else read those files.

The rule: every prompt describing a validated artifact shows the exact JSON skeleton the
validator accepts, and the kernel supplies data the worker cannot compute (diffs, applicable
policy IDs, hashes). Accepting an equivalent spelling is allowed; dropping a required field or
check is not. Trust-root change through the maintainer lane.

---

## D-029 · Validators accept both spellings of a worker-written list

**Status:** recorded · **Raised:** 2026-09-04

Canary attempt 8 (run 33916377607) passed investigate, the deferred repro and the contract
gate, then died at the context gate: `IMPACT_FAIL: context has no seed files`. The context
worker had written every list as objects, `{"path": ..., "why": ...}` for files and
`{"name": ..., "why": ...}` for symbols, and `scripts/factory_impact.py` kept only string
entries. Attempt 7 (run 33914596611) had died the same way one stage later, at the governor
gate, with principles spelled as `{"id": ..., "verdict": ..., "notes": ...}` objects. In both
runs the content was right; the spelling was the whole defect. Earlier attempts had written
plain strings by luck.

The rule, applied to every remaining worker-written list in one change: a validator accepts
each entry as either a plain string or an object carrying that list's canonical key (`path`
for files/tests/adrs/planned files, `name` for symbols/callers/modules/seams, `id` for policy
ids, `text` for prose lists), normalises to the string BEFORE it validates or hashes, refuses
an object without the key, a non-string value, or a duplicate after normalisation, and ignores
a top-level `notes` string. Compiled artifacts and every hash stay in the plain-string form, so
nothing downstream changed. Prompts show the plain-string skeleton and name `notes` as the
place for explanations. The refused raw artifacts from runs 7 and 8 are checked in as fixtures
and must compile.

This accepts an equivalent spelling; it drops no check. It is a **judgement** structure and
moved through the maintainer lane.

---

## D-030 · The stages after the governor, audited before the run reached them

**Status:** recorded · **Raised:** 2026-09-04

Attempts 6, 7 and 8 of the first canary each died one stage later than the last on the same
class of defect: a prompt described a validated artifact in prose, or omitted information only
the kernel holds, and a deterministic gate refused correct work. D-028 and D-029 fixed every
stage up to and including the architecture governor. A second read-only audit covered the
stages after it, before attempt 9 could reach them. Findings, all landed together:

1. The RED gate refuses a deferred repro unless some checkpoint's failing output carries the
   promised symptom, but the test author, the one worker that shapes that output, was never
   shown the symptom. The kernel now appends it to the test author's brief.
2. The conformance compiler computes applicable policy IDs from the changed files; the prompt
   told the worker to use the governor's context/planned basis, and the kernel passed only the
   diff. The kernel now computes the sets from the changed files and supplies them; the prompt
   states the real basis. `_applicable_policy_ids` takes an explicit file set.
3. `test-spec.json` is the one worker-written list that accepts no object spelling (its `argv`
   must stay exact); the prompt now says so instead of implying the D-029 rule applies.
4. Three authorities classified test paths with three predicates; a `test_*.py` under a product
   directory passed the commit envelope and RED and was then an unplanned production file to
   the architecture guard. One predicate, `scripts/factory_shapes.test_shaped`, now serves all
   three, and the prompt states that rule.
5. The pinned review methods said "Output exactly one JSON object"; the kernel reads a file.
   They now say "Write ... to the artifact path your role prompt names".
6. Validator side: the architecture holdout is handed the ID sets computed from the changed
   files (the basis the evidence verifier checks), and the three certifiers receive a literal
   JSON skeleton with `certifies` filled in rather than a one-sentence schema.
7. Cleanup ticket, not changed here: `scripts/factory_provenance.py publish` runs twice per
   build (once inside `factory_protocol.py attach`, once from `_attach_and_publish`). Harmless
   (`git notes add -f`), one redundant fetch/push.

None of these relaxes a check. Items 1, 2 and 6 move information the kernel already computes to
the worker that must echo it; 3, 4 and 5 make a prompt describe the validator that runs.

---

## D-031 · A dropped stream is not a verdict

**Status:** recorded · **Raised:** 2026-09-04

Attempt 9 of the first canary (run 33918953996) was the first to clear the architecture
governor on the audited kernel. The `test_author` worker then returned an error envelope after
seven turns and 11.6 seconds of API time: `API Error: stream closed before completion`. The
provider refused it as a failed stage, as it should for a turn cap or a budget stop, and the
whole build, about fifty minutes of certified work, was thrown away for a network hiccup.

The provider now retries a stage whose CLI process ends in an explicitly TRANSIENT error, and
only then. The list is short and literal: `stream closed before completion`, `overloaded`,
`rate limit`, `429`, `502`, `503`, `504`, `ECONNRESET`, `ETIMEDOUT`, `socket hang up`. An
envelope whose `subtype` starts with `error` (`error_max_turns`, `error_max_budget`) is a
verdict about the worker, not the network, and stays terminal even if a transient word appears
in its text; so does a missing model, unparseable output, a non-zero exit or a timeout.

Each retry is a fresh CLI process with the same prompt, after a 5 s then 15 s backoff, at most
`provider.transient_retries` times (2, bounded 0..3 in `.factory/kernel.json`). Before a retry
of a mutation role the kernel restores the worktree (`checkout -- .`, `clean -fd`) so the
commit envelope never judges the union of two half-finished attempts; for any other role it
asserts the tree is still clean. The provider itself never touches Git.

Telemetry is honest about the cost: `attempts` and `transient_errors` are recorded per stage,
and turns, tokens and dollars are summed across attempts. The dollar cap is a per-process CLI
flag, so a stage's effective ceiling is `max_budget_usd × (1 + transient_retries)`; with the
D-025 budgets that is at most 36 USD for a builder role. Three mutations attack the boundary:
terminal errors retried, retry without the worktree restore, retries unbounded.

---

## D-032 · Scripts put the repository root on sys.path themselves

**Status:** recorded · **Raised:** 2026-09-04

Canary attempt 10 (worker run 33920886708) completed the entire autonomous build — investigate,
deferred repro, contract, context, governor, RED, implement, both review axes, conformance,
final GREEN, quick gate, push — and opened PR #74. The next step,
`scripts/factory_provenance.py publish`, died with `ModuleNotFoundError: No module named
'factory_kernel'`. The script imported the kernel by module path with no `sys.path` bootstrap;
in CI and on a developer machine the cwd is the repository root so the import happened to work,
but the kernel runs its scripts from a detached PR-head worktree with `_run_env`, which sets no
PYTHONPATH. `scripts/factory_evidence_spine.py` had the same shape and the validator would have
hit it one dispatch later.

Both scripts now insert the repository root (derived from `__file__`) into `sys.path` before
their first `factory_kernel` import. The alternative, having the kernel export PYTHONPATH from
`_run_env`, was rejected: the scripts are run standalone by the CI quick gate and by humans, so
their importability must not depend on the caller. A test runs every script from a temporary
directory outside the repository with PYTHONPATH empty; a mutation strips the bootstrap from
the provenance script and is caught.

This is the eleventh canary defect and the first found after a complete build. PR #74 stands;
it needs its provenance note published and the `factory:needs-review` label before validation,
and does not need to be rebuilt.

---

## D-033 · A pushed PR is resumed from its artifacts, not rebuilt

**Status:** recorded · **Raised:** 2026-09-04

Canary attempt 10 (worker run 33920886708) completed the whole build and opened PR #74, then
died one step later in `factory_provenance.py publish` (D-032). The branch and PR were real;
the run's artifacts survived only as the uploaded workflow artifact; the kernel had no path
that finished such a PR, so the honest options were a full rebuild (nine model stages, ~40
minutes, ~$25) or hand-editing GitHub state.

`resume_pr` is the third option. It rebuilds nothing and runs no model. It refuses unless the
supplied artifacts are complete, the final GREEN proof is bound to the exact PR head, and the
RED-hashed tests are byte-identical at that head; then it runs the same attach/publish sequence
the build path runs, finishes the lease, and hands the PR to validation, which reuses nothing.
The attach programs already strip an existing block of their kind before appending, so a
partial first attach leaves no duplicate. A resume marker on the PR caps it at one per PR.

Deliberately human-invoked, not dispatched: the artifacts must be retrieved with
`gh run download`, and a PR that cannot be finished from its own artifacts is a defect to
diagnose, not a state to poll. Four mutations attack the boundary: proof-head binding dropped,
RED check dropped, cap removed, human-authored PR accepted.

---

## D-034 · The worker resumes a pushed PR on GitHub-hosted infrastructure

**Status:** recorded · **Raised:** 2026-09-04

D-033 added `python -m factory_kernel resume --pr N --artifacts <dir>`. Running it needs the
kernel's toolchain, the worker's disposable validation environment and the Actions token
with `contents: write` for the provenance note push; the local Windows machine cannot run the
kernel at all (`work root must be absolute`), and a `workflow_dispatch` workflow must exist
on the default branch before it can be dispatched, so a throwaway workflow on a branch does
not work either.

The canonical worker therefore takes two optional dispatch inputs, `resume_pr` and
`resume_run_id`. With both set, the run keeps every preflight and setup step, downloads that
run's uploaded artifact (the only new permission is `actions: read`), requires exactly one
`final-green-proof.json` inside it, and runs `resume` **instead of** `dispatch --once`. A lone
input refuses at preflight; running both actions in one run is a mutation the structure
test catches. Authentication is unchanged: `factory_provenance.py publish` and
`github_cli.push_branch` already take the Actions token from `GH_TOKEN` through their own
askpass helpers.

First use: PR #74 (canary attempt 10, run 33920886708).

---

## D-035 · One factory identity, one spelling, read from REST

**Status:** recorded · **Raised:** 2026-09-04

The first resume of the factory's own PR #74 (worker run 33927106276) refused with "not
opened by the factory (author 'app/github-actions')". `gh pr view --json author` is a GraphQL
query and names a GitHub App as `app/github-actions`; the REST `pulls/N` endpoint names the
same actor `github-actions[bot]` with type `Bot`. `resume_pr` compared the GraphQL spelling
against a bare `github-actions` and never saw the actor the rest of the system knows.

The trust-root guard already decides lanes from the REST shape (`scripts/factory_security.py
pr_identity`: `user.login`, `user.type`, `author_association`), and the kernel already commits
as `github-actions[bot]` (`worker_policy.KERNEL_COMMIT_NAME`). So the factory's identity has
exactly one spelling and one source: `github_cli.pr_author()` reads the REST `user`, and
`resume_pr` requires type `Bot` and that login. The GraphQL author is no longer consulted for
any decision. Type is part of the identity: a User account named like the bot is refused.

Twelfth canary defect. Grep of the kernel for other PR-author comparisons: none (`triage.py`
compares issue authors for the daily cap, from the issue-list JSON, unchanged).

---

## D-036 · A kernel authority executes from the kernel's checkout, never from the subject's copy

**Status:** recorded · **Raised:** 2026-09-05

The second resume of the factory's first PR #74 (worker run 33927770223, after D-035) died in
`factory_provenance.py publish` with the same `ModuleNotFoundError` that #75 had fixed on
`main`. The kernel had run `python scripts/factory_provenance.py` with the working directory
set to the PR-head worktree, and Python resolved that relative path against the worktree: the
program that ran was the PR head's copy, which predates #75. Thirteenth canary defect, and the
only one that names a property rather than a spelling: **the subject of a judgement was
supplying the program that judged it.** Nothing stopped a PR from carrying a tampered
`factory_proof.py` or `factory_evidence.py` into validation, re-head or resume and having the
kernel run it. (On the autonomous lane the security guard refuses trust-root paths and the
evidence bundle refuses trust-root drift, but both are defence in depth; a human-lane PR that
edits a validator is validated by the kernel too, and it was judged by its own edit.)

The rule now: `KernelRuntime._exec` rewrites every repository-relative trust-root program
(`scripts/factory_*.py`, `harness/merge_verify.py`, `harness/post_merge.py`) to the kernel's
own checkout of `main` (`factory_kernel/trusted_programs.py`) and leaves the working directory
alone. Every one of those scripts derives the tree it inspects from its working directory
(`ROOT = Path.cwd()`), imports the kernel and locates sibling programs from beside itself
(`HERE`), and never through the tree under test. So main's code operates on the PR's tree: in
the build path the two are identical (a fresh branch off main); in validation, re-head and
resume they can differ, and now the difference is data in a diff, not a program.

Two things deliberately stay in the tree under test. The canonical harness (`harness/ci.py`
and what it runs) is the harness *under test*; the evidence bundle runs it inside the worktree
only after `trust_root_drift` has refused any trust-root difference from `origin/main`, and
that check is itself now computed by main's code. Policy files (`.factory/architecture.json`,
`.factory/evidence-spine.json`, the floor) are read from the tree by the programs that judge
it; they are data, protected by the same drift check and by the guard. `FACTORY_REPO_ROOT`,
which four programs honoured as an override for the tree root and nothing ever set, is gone:
one rule, the working directory.

Also found: `harness/merge_verify.py` imported the kernel with no `sys.path` entry of its own,
so the merge pre-authorisation would have failed the same way on the first validation. Both
harness authorities now put their code root on `sys.path` like the scripts (#75).

Detectors: the resolver's unit tests; the real `_exec` with a mocked subprocess (program under
the kernel checkout, working directory the worktree); a real temporary repository whose
`scripts/factory_security.py` is `raise SystemExit(99)` and whose `FACTORY_RULES.md` is dirty,
where the kernel path must return the guard's verdict naming that file; the evidence loader
against a tree of trapped validators; the rehearsal traces of validate, re-head and resume.
Mutations: the resolver call removed, the working directory switched to the kernel checkout,
the authority rule disabled, evidence loading a validator from the subject, a sibling invoked
through the tree.

---

## D-037 · Every object the kernel writes carries the kernel identity, notes included

**Status:** recorded · **Raised:** 2026-09-05

The fourteenth canary defect (worker run 33930385600, the first resume of PR #74 to reach
provenance publish after #79 made the program run from the kernel checkout): `git notes add`
failed with `Author identity unknown`. A note is a commit object on the notes ref and needs an
author exactly as a worker commit does; the GitHub runner configures none. Worker commits
(`git_authority`), the re-head rebase (#65) and the safe revert (`worker_runtime`) already splice
`KERNEL_COMMIT_ARGS`; the notes write never had it because no run had reached that line before.
No exact-head provenance note had ever been created in production.

The rule: every git invocation that creates an object (`commit`, `notes add`, `rebase`, `revert`,
`merge`, `tag -a`) is spelled `["git", *KERNEL_COMMIT_ARGS, ...]`. The inventory at this commit:
`git_authority._commit`, `runtime.rehead_pr` rebase, `worker_runtime._create_safe_revert_pr`
revert, `factory_provenance.publish` notes add. Reads (`notes show`, `rev-parse`, `diff`) carry
no identity. The test runs the real script in a repository whose global and system config are
empty, proves the note's author and committer are the kernel identity, and proves the same
write without the args fails with the runner's exact error.

---

## D-038 · Evidence stores no terminal control sequences; an attach is not done until it round-trips

**Status:** recorded · **Raised:** 2026-09-05

The first production `validate_pr` (worker run 33931048575, PR #74) refused at its third step:
`attached factory-proof is invalid JSON`. The proof's `red_output_tail` (D-024) carried vitest
colour escapes; `canonical()` wrote them as `\u001b`, and the body GitHub handed back held a
backslash followed by caret-notation `^[`, which is not a JSON escape. The uploaded artifact parsed;
the PR-body channel corrupted it. Sixteenth canary defect.

Two rules follow. Runner output that enters evidence is sanitised at the source
(`factory_kernel.attached.sanitise_output`: ANSI/CSI/OSC stripped, other C0 controls to
U+FFFD, newline and tab kept), and the RED symptom match, the stored tail and the output hash
all see the sanitised text so stored evidence and the check agree. And every attach program
writes the body through `--body-file`, reads it back from GitHub, and refuses with
`ATTACH_FAIL` unless the block parses to the same canonical bytes it sent; the extraction is
one shared parser (`factory_kernel/attached.py`) used by both the scripts and the kernel's
`_extract_attached`, so attach and validate cannot drift. An already-attached corrupt block is
repaired by re-running `resume`: attach replaces its blocks.

---

## D-039 · The route probe tests the route, not the model's chattiness

**Status:** recorded · **Raised:** 2026-09-05

Worker run 33931843218 refused `deepseek/deepseek-v4-pro-0813` at preflight
(`FACTORY_PREFLIGHT_REFUSED worker CLI cannot reach model`) and escalated issue #49 to
`factory:needs-human` before any model stage ran. The CLI envelope was healthy:
`is_error: false`, `stop_reason: end_turn`, `num_turns: 2`, four output tokens, the model
present in `modelUsage`. Its `result` was the empty string: asked to "reply with exactly the
word OK", DeepSeek spent its tokens thinking and emitted no final message. The probe's rule
was `is_error is False and result`, so an empty string read as unreachable. Seventeenth
canary defect.

The probe now judges reachability on the error state alone: `is_error` false, a normal
`stop_reason` (`end_turn`, `stop_sequence`, `max_tokens`), at least one turn, and usage or
`modelUsage` present. The text of `result` is not consulted. The OK and REFUSED lines carry
`stop_reason` and `output_tokens` so a future refusal is diagnosable from the log.

Why this is safe: no factory stage relies on a bare final message. Every worker writes its
artifact to `ARTIFACTS_DIR` and the kernel reads the file; a worker that writes nothing is
refused by the stage-note existence check (#70) or the artifact validators, never by the
probe. The empty-result behaviour is a DeepSeek trait under a tiny prompt, not a routing
fault, and the probe must not encode one model's habits as the definition of "reachable".

A mutation (`worker-route-probe-requires-result-text`) restores the text requirement and is
caught by the structure test. Issue #49's escalation from this run is un-escalated by hand.

---

## D-040 · A non-zero CLI exit is classified before it is refused

**Status:** recorded · **Raised:** 2026-09-05

The eighteenth canary defect (worker run 33933101233, issue #49). Investigate, contract,
context and the governor passed on the fully audited kernel; the test author then ended with
`API Error: stream closed before completion` after six turns, the same drop that killed the same
stage in run 33918953996. D-031 added a retry for exactly that pattern, and it never fired: the
CLI exits non-zero when a session ends in error, and `_launch` raised the generic "agent worker
failed rc=1" before the envelope on stdout was ever classified. The retry only saw error
envelopes from zero-exit processes, which in practice is not how the CLI reports a stream drop.

Now a non-zero exit first parses stdout as the result envelope; if it is one and the classifier
calls it transient, the existing retry loop (worktree restore, backoff, summed telemetry) handles
it. A terminal envelope, or stdout that is not an envelope, is refused exactly as before. Caps,
budgets and model errors are never retried. Run 33933101233's envelope is a fixture.

Why the test author: not its prompt. Its assembled prompt is the smallest of the post-contract
stages (about 2k chars of role prompt, no pinned methods, a 10k brief of contract, issue and
deferred symptom), smaller than context (8.5k plus brief) or implement (7.4k plus brief). Both
drops happened at the same wall-clock position, six to seven turns into a stage that reads test
files and writes new ones, and the stage records no telemetry when it fails because
`_record_agent` runs only on success; the failing envelope itself is the only evidence. With the
retry now reachable, the next drop is measured instead of fatal. A failed stage should also
leave a telemetry record; that is a separate change to `_record_agent`'s call site.

---

## D-041 · A failed stage is recorded like a successful one

**Status:** implemented · **Raised:** 2026-09-05

`_record_agent` wrote `agent-<role>.json` and the stage-timings row only after
`provider.run` returned. The two `test_author` stream drops (runs 33918953996 and
33933101233) therefore left one line of exception text and nothing else: no turn count,
no token count, no cost, no attempt count, although the CLI had printed all of it in the
error envelope the provider refused.

`worker_runtime._agent` now records the failure before re-raising it, unchanged. Every
terminal refusal the provider raises (`ProviderStageError`: exhausted transient retries,
a non-transient exit, a timeout) carries the telemetry summed across the attempts it made,
the attempt count, the transient errors seen and whether it timed out; the record is written
with `outcome: failed`, and the error text passes through the guard's secret scrub first,
because a provider error can echo the prompt and the prompt can echo an issue body. The
timing row gains `outcome`, `error_class` and `timed_out`.

This is observability only. Nothing reads the record to decide anything; the failure
propagates exactly as before, with the same class and message, and the retry
classification is untouched.

---

## D-042 · A consumer reads the binding it verifies; the pack records the base the branch was cut from

**Status:** recorded · **Raised:** 2026-09-05 · **Evidence:** worker run 33938048704 (the first
production re-head, PR #85), run 33934857300 (the build that published the pack)

Validation refused #85 with a correctly classified `stale_base`: main had moved from 0c17566 to
14701b8 while the build ran. The next dispatch chose the model-free re-head, which died at
`factory_provenance.py fetch --base 0c17566` with "built from a different base". The note on
aa38448 declared `base_sha = 14701b8`: `publish` had read GitHub's `baseRefOid`, which is the
current tip of main, not the commit the branch was cut from, and 14701b8 is not even an
ancestor of aa38448. The re-head's `merge-base` guess (0c17566) was right; the pack was the one
lying. Two programs computed the same binding two ways and neither read what the other wrote.

Three changes, none weakening a check:

- **The pack records the cut point.** `build_issue` resolves `base_sha` once at its start and
  hands it to `_run_env` as `FACTORY_BASE_SHA`; `_attach_and_publish` passes it as
  `publish --base` and refuses to publish without it. `publish` no longer reads `baseRefOid`.
  The re-head republishes with the rebased base; `resume` uses the merge-base of the uploaded
  head, which the head's own history holds.
- **A base that is not an ancestor of its head is refused everywhere.** `verify_pack` takes an
  `is_ancestor` callback; `publish` and `fetch` pass a real `merge-base --is-ancestor`, the
  kernel passes its own. The first production pack would have been refused at publish.
- **Consumers read, then verify.** `factory_provenance.py peek --head H` prints the identity a
  note declares without trusting its contents. `_pack_base` reads it, checks head and issue,
  checks ancestry, and only then does `fetch` hold the pack to that base. `rehead_pr` no longer
  guesses. `validate_pr` compares the declared base with GitHub's current base before fetching
  and refuses `stale_base` at the provenance stage, the earliest point the class can be known;
  `refusal.py` pins the new producer string.

#85's note is false and cannot be repaired by re-heading; the overseer closes it and rebuilds
#49 on this kernel. The nineteenth canary defect.

---

## D-043 · Static checks run on a worker's files before the kernel commits them

**Status:** implemented · **Raised:** 2026-09-05

Worker run 33938917038 (kernel eb40907) passed every model stage, RED, GREEN, both review
axes and conformance, then failed the quick gate's biome rung on two
`noMultipleSpacesInRegularExpressionLiterals` errors in the acceptance-test file the
independent test author had written. Those files are RED-hashed the moment
`factory_proof.py red` succeeds and immutable for every later stage, so no repair could ever
have fixed them: the build was structurally lost forty minutes before it was reported lost.
The twentieth canary defect, and the first that was a design gap rather than a plumbing one:
the repository's static checks ran only after RED had frozen the files.

The kernel now runs the same tools, scoped to the files a worker just wrote, BEFORE it commits
them (`factory_kernel/static_gate.py`, `WorkerControlledRuntime._static_gate_or_retry`):
ruff check and ruff format for `app/backend/**/*.py`, biome check for
`app/frontend/**/*.{ts,tsx,js,jsx}`. On a failure the files stay uncommitted and the worker
that wrote them is run once more with the checker's output (the test author for acceptance
tests; a fresh `repair` worker for production files); a second failure escalates with the
output in the refusal record. The retry edits the files in place rather than starting from a
restored tree, because the defect is in those very files and nothing has been committed
(contrast D-031's transient restore, where a dropped stream left the tree half-written). mypy
and tsc are whole-program checks with no honest file scope and stay in the quick gate, which
still runs every check over the whole tree afterwards and remains the authority. Nothing is
weakened; a subset of the gate moved earlier, to where it is repairable.

Telemetry from the run: test_author 1149 s / 13 turns, the slowest stage by far; investigate
544 s / 7; contract 321 s; context 155 s; architecture 202 s; implement 132 s; review-spec
130 s; review-standards 176 s; conformance 217 s; quick gate 69 s. Whole build about fifty
minutes. The test author's cost is the next throughput question; it reads test files and
writes new ones and has the smallest prompt of the post-contract stages.

---

## D-044 · One publisher

**Status:** recorded · **Raised:** 2026-09-05

Worker run 33941987102 built issue #49 through every stage, including the new pre-commit
static gate (D-043), and opened PR #88. It then died in `scripts/factory_protocol.py attach`:
`factory_provenance.py publish: error: the following arguments are required: --base`.

D-030 had already listed this call as a cleanup ticket: `run_attach` published provenance
itself, and the kernel's `_attach_and_publish` published again afterwards. D-042 then made
`--base` a required argument of `publish` so the pack records the base the branch was cut
from. The kernel's call was updated; the duplicate, argument-less call in the script was
not, and because it ran first it took the build down before the correct call could run.

The duplicate is removed. Provenance has exactly one publisher, the kernel's
`_attach_and_publish`, which build, resume and re-head all share and which passes the base
the kernel recorded at the start of the build. `tests/factory/test_factory_single_publisher.py`
pins that `run_attach` invokes no program other than `gh`, that `factory_protocol.py` never
names the provenance program, and that every `publish` invocation in the kernel and scripts
passes `--base` and there is exactly one. A mutation reintroduces the argument-less
duplicate.

The lesson is narrower than "delete duplicates": when a program grows a required argument,
every caller is a site to audit, and a caller already known to be redundant is the one most
likely to be missed.

Recovery for PR #88: its artifacts are complete and its head untouched; `resume --pr 88`
runs attach, attach, publish and the handoff from those artifacts.

---

## D-045 · A rebase rewrites the test-author commit; RED is replayed there, never re-bound

**Status:** recorded · **Raised:** 2026-09-05

Worker run 33944595689 was the first production re-head. It did everything D-023 and D-042
promised: fetched the certified pack at its declared base, rebased PR #88 onto current main
(new head 65721af9, base 7fe0c7f6), verified the RED-hashed files byte-identical, replayed
GREEN, ran conformance, the final GREEN and the quick gate, republished, and handed the PR
back to validation in the same dispatch. Validation then refused: `test-author commit is not
an ancestor of current PR head`.

A rebase rewrites every commit. The pack's `red-proof.json` named the pre-rebase test-author
commit as `test_commit`; `scripts/factory_evidence.py` requires that commit to be an ancestor
of the head, to change exactly the declared acceptance files, and to go red when replayed
there. The re-head had checked the files and never the commit, so it handed validation a
proof chain whose anchor no longer existed in the branch.

Two ways to fix it were on the table. Re-binding `test_commit` in the proof to the rebased
hash would have been a one-line edit of evidence: the kernel writing into a proof what a
replay is supposed to establish. That is the shape of every circular certification IMM-004
and IMM-006 exist to refuse, and it is not done. Instead the re-head locates the rebased
test-author commit by shape (the first commit above the new base, carrying the subject
`git_authority` gives every test-author commit, and changing exactly the RED-hashed files and
nothing else; anything else is refused as not a re-head of this build), checks out that
commit detached, and runs `factory_proof.py red` against a spec reconstructed from the pack's
own checkpoints. The proof it writes binds `test_commit` to the rebased commit because that is
where it ran; every checkpoint must still fail for its declared reason, and a checkpoint that
passes after the rebase is refused, because main changed the behaviour under test and that is
a new build, not a re-head. The kernel then verifies the re-issued commit is an ancestor of
the new head, returns the worktree to the branch tip, and continues with GREEN exactly as
before. The RED binding is rebuilt by replay, not rewritten by hand.

The one-re-head-per-PR cap (D-023) counts re-heads, not their outcomes. PR #88 has used its
re-head; the marker was written before the validation that refused. The cap is left as it is:
a second re-head after a kernel-side refusal would need the kernel to judge its own failure
as not the PR's, and that judgement is exactly what the cap exists to keep out of the loop.
#88 is closed and issue #49 rebuilt on the fixed kernel; the attempt budget was not charged,
because `stale_base` never writes the validation-failed marker.

Regression tests: a rehearsed re-head produces a red-proof whose `test_commit` is the rebased
test commit and an ancestor of the new head, in order between the rebase and GREEN; a RED
checkpoint that passes after the rebase refuses before any GREEN or push; a rebased history
without the test-author commit first, or with a test commit that changes other files, refuses.
Mutations: the re-issue skipped (old test_commit kept); the ancestor check on the re-issued
commit dropped; the commit-shape check dropped.

---

## D-046 · The browser opens `localhost`, because the session cookie is Secure

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33947564054 (validation of PR #91)

The first production browser E2E got further than any before it: static 5/5, 1470 unit
tests, `APP_STARTED`, the login page rendered, both fields were filled, "Log in" was
clicked. Twenty seconds later the snapshot still showed the login form. The harness had
never passed anywhere; this was its first real execution.

**Diagnosis.** `app/backend/routes/auth.py` mints the session cookie with `secure=True`
(`_set_session_cookie`, pinned by `app/backend/tests/test_auth.py`, a MISSION security
invariant). `harness/e2e.py` opened the frontend at `http://127.0.0.1:<port>`. Browsers
store a `Secure` cookie only from a secure context; Chromium and Firefox exempt the
`localhost` name on plain HTTP but not the loopback literal. So `POST /api/auth/login`
returned 200 with a `Set-Cookie` the browser discarded; `useAuth.refresh()` called
`/api/auth/me`, got 401, set the user to null; `RequireAuth` navigated back to `/login`;
the predicate "Ask anything about the video library" (which does exist verbatim in
`ChatArea.tsx`) never appeared. Everything else checked out: the bootstrap creates the
user with the same bcrypt hasher login verifies; Circle verification without configuration
returns non-member without raising, and non-members see the chat surface; the signup
rate limiter is not on the login path.

**Fix.** The harness, not the app: `BROWSER_ORIGIN_HOST = "localhost"` for every browser
origin, and the explicit-URL path refuses anything else. The Secure cookie is untouched
and a contract test asserts the app-side pin still exists, so the E2E cannot be made green
by weakening the cookie. The backend is still probed at `127.0.0.1`.

**Consequences.** The refusal wrote one `validation-failed` attempt marker on #49; it is
environmental and the overseer resets it. PR #91's head predates this fix and the full
harness runs at the PR head's tree, so #91 needs a re-head (its one re-head is unspent)
or a rebuild for the fix to reach it. The E2E is still unproven end to end; the next
validation is the first that can reach the citation steps.

## D-047 · The synthetic E2E account is `dark-factory-e2e@example.com`, because the login route refuses reserved names

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33950794336 (validation of PR #93)

D-046 moved the browser origin to `localhost`. The next production validation stalled on
the login form exactly as before, so the Secure-cookie theory was at most one of two
causes, and the harness had produced no evidence that could tell them apart: the
interactive snapshot (`snapshot -i`) lists interactive elements only, so the alert the
login page renders on failure never appeared in it, and nothing captured the page,
console, network log or cookies before the session closed.

**Evidence chain.**

1. `harness/bootstrap_e2e.py` pinned the account as `dark-factory-e2e@localhost.invalid`
   and both workflows exported the same literal. The bootstrap writes the user row with
   `users_repo.create_user` directly, below any route validation, so the row is created.
2. `POST /api/auth/login` validates the body with `LoginRequest(email: EmailStr)`
   (`app/backend/routes/auth.py`). Reproduced against the application test client with
   the repository mocked and the same env shape as the worker: the route answers
   `422 {"detail":[{"type":"value_error","loc":["body","email"],"msg":"value is not a
   valid email address: The part after the @-sign is a special-use or reserved name that
   cannot be used with email." ...}]}`; `/api/auth/me` afterwards answers 401.
3. `email-validator`, which `EmailStr` delegates to, rejects `.invalid`, `.localhost`,
   `.test` and the bare `localhost` domain; it accepts `example.com`
   (reserved for documentation by RFC 2606 but not on the special-use list). Verified by
   instantiating the model with each spelling in the backend's locked environment.
4. On the frontend, `authApi.login` throws `AuthError(422, "value is not a valid email
   address: …")`, `Login.tsx` sets `formError` and renders it in a `role="alert"` div,
   the form stays. That is the recorded snapshot. The Vite proxy target and agent-browser
   `fill` semantics (clear then type, drives React state) were checked and are not causes.

**Fix.** Three parts, all in the harness lane; the application and its validation are
untouched.

- The account is now `dark-factory-e2e@example.com` in the bootstrap and both workflows,
  and the bootstrap instantiates the route's own `LoginRequest` before writing the row,
  so it can no longer provision an account the route would refuse.
- `harness/e2e.py` asks the route first: a harness-side `POST /api/auth/login` with the
  validation credentials, printed as `E2E_LOGIN_PROBE status=… session_cookie=… body=…`
  (password scrubbed). The journey requires 200 plus a session cookie before any browser
  starts, so a route refusal names its reason in the log instead of surfacing as a
  predicate timeout.
- Any `E2EFailure` inside the browser journey now dumps `url.txt`, the full
  (non-interactive) `snapshot.txt`, `page.html`, `console.txt`, `errors.txt`,
  `network.txt`, `cookies.txt` (values scrubbed), `failure.png` and `failure.txt` into
  the artifact directory the kernel already uploads, and prints `E2E_EVIDENCE_DUMP`.

Pinned by `tests/factory/test_e2e_contract.py` (probe refusal before the browser, cookie
required not just 200, scrubbed dump on failure, literal agreement across bootstrap and
workflows, no reserved name) and by `app/backend/tests/test_e2e_account_email.py` (the
pinned address passes `LoginRequest`; the old one is 422 at the route with no repository
call). Mutations `e2e-validation-account-under-reserved-name`,
`e2e-login-probe-assumed-green` and `e2e-failure-dump-skipped` prove each part is
detected.

**Consequences.** The attempt marker this refusal wrote on #49 is environmental and is
reset. PR #93's refusal class is `evidence_spine`, not `stale_base`, so it is not
re-head eligible, and its head carries the old bootstrap literal; #93 is closed and #49
rebuilt on a main that contains this fix. The next validation is the first in which the
browser is asked to log in with an account the route accepts.

## D-048 · The blinded code holdout is shown the RED evidence, because a judge shown only GREEN is right to refuse

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33955178802 (validation of PR #96, kernel 08067a0)

The first validation to reach the blinded code holdout with a green browser E2E behind it
was refused by the holdout itself, with a HIGH finding: "RED-first proof is not established
by the supplied evidence ... the proof_summary supplies only green evidence", and a MEDIUM
that pre-existing-test invariants were asserted but not demonstrated. The diff was three
lines plus four acceptance tests and had passed the same holdout twice on earlier heads.

**What was actually wrong.** Not the judge. `validate_pr` built the holdout's
`proof_summary` from `test_commit`, `green_commit` and `green_results` only. The kernel had
replayed RED at the test-author commit, checked every checkpoint failed for its declared
reason, hashed the acceptance files, replayed GREEN at the head and verified the hashes
again; none of that reached the judge. The holdout prompt says an absence of enough evidence
to establish a material claim is a blocking finding. A judge that refuses on a missing
RED-first proof when shown only GREEN is doing its job. The two earlier passes on the same
diff were judge variance on an evidence gap, which is the worse outcome: a gate that passes
or fails by mood is not a gate.

**Decision.** Supply the evidence; do not soften the judge.

- `proof_summary` now carries `red_results` (per checkpoint: `acceptance_id`, the non-zero
  `red_exit`, `expected_failure`, whether it is `matched` in the excerpt, and a sanitised
  `red_output_tail` of at most 600 characters placed so the expected failure is visible),
  `red_files` (the immutable acceptance files with the SHA-256 RED recorded), `red_commit`
  (equal to `test_commit`) and `preexisting_tests` (a count of `it(`, `test(` and
  `def test_` definitions at base and at head for every test-shaped file the diff touches),
  alongside the `green_results` it always had.
- The RED half is sourced from the attached final proof and cross-checked against the
  note-bound builder pack: the RED commit and the file map must agree, every checkpoint must
  record a failing exit and a declared expected failure, or the run refuses at
  `attached_evidence` before any judge is asked to trust it.
- The pack fetch and `verify_pack` therefore run before the code holdout, not after. Both
  are deterministic and model-free; the holdout's blinding is unchanged (contract, changed
  files, diff, proof summary, nothing else). FACTORY_RULES §3 steps 4 and 5 swap.
- `.factory/prompts/holdout.md` states what the kernel has already proved deterministically
  (RED at the RED commit, GREEN at the head, immutable hash-verified acceptance files, static
  checks, the full harness running after the verdict) and what the judge must decide (the
  diff satisfies the contract without collateral change; any deletion or weakening of
  existing tests is visible in the diff and the counts; nothing beyond scope). The pass/fail
  output shape is unchanged.

Pinned by `tests/factory/test_factory_holdout_evidence.py` through the rehearsal harness
(the holdout context carries failing `red_exit` and `expected_failure` per checkpoint,
`red_commit`, hashed `red_files`, base/head counts, GREEN kept, pack fetch and
`verify_pack` precede the holdout in the trace, malformed or disagreeing RED evidence
refuses before the holdout runs) and a snapshot of the prompt's proved-claims and
judged-questions sections. Mutations `holdout-shown-only-green` and
`pack-verified-after-holdout` are caught.

**Consequences.** The attempt marker this refusal wrote on #49 is factory-caused and is
reset by the overseer. PR #96's head is untouched and its refusal class is `code_holdout`,
which is validator-side, so it can be validated again on a main that contains this fix
without a re-head or a rebuild.

## D-049 · The browser journey targets nodes by role and accessible name, because the React root is listed first with the whole page as its name

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33953697016 (main regression on 08067a0, after D-047)

The first main regression after D-047 printed `E2E_LOGIN_PROBE status=200 session_cookie=true`
and then `E2E_FAIL browser state did not appear in 20s` with the login form still showing.
The route accepted the account; the browser never asked it. The evidence dump D-047 added was
written to `/tmp` on the hosted runner and uploaded by nothing, so the cause was re-derived
locally with the real Vite frontend, agent-browser 0.35.0 and a stub backend that answers the
login exactly as the route does.

**What the browser saw.** `agent-browser snapshot -i` lists the React root first:

```
- generic "DynaChatAsk Cole Medin's YouTube videos ... Log inEmailPasswordLog inNeed a" [ref=e1] clickable [onclick]
  - heading "Log in" [level=1, ref=e2]
  - textbox "Email" [required, ref=e4]
  - textbox "Password" [required, ref=e5]
  - button "Log in" [ref=e3]
```

React delegates every event listener to its root element, so the root is "clickable", and
the snapshot names it by the page's whole text. The harness resolved a target as the first
line containing the query text: "Email" and "Password" both resolved to `e1`. `fill @e1`
on that div returned `Done` (rc 0), both inputs stayed empty, and the click submitted a form
whose `required` fields were empty: the browser blocked the submit silently, no request left
the page, no alert rendered. Every earlier hypothesis (the `127.0.0.1` origin of D-046, the
reserved-name email of D-047, a Vite proxy pointed at the wrong port, the `Secure` cookie on
plain HTTP) was either real and insufficient or refuted: `serve.py` already exported
`VITE_API_TARGET`, the proxy returned the Secure cookie, and Chromium honoured it on
`http://localhost` (the post-login `/me` carried the cookie once the fills landed).

The same defect waits after login: the chat page lists `heading "Ask anything about the
video library"` before `textbox "Ask anything about the video library…"`, so the message
input would have resolved to the heading next.

**Decision.**

- `harness/e2e.py` parses each snapshot line into role, accessible name and ref and resolves
  a target by `_ref(snapshot, role, name)` on those fields only, never on raw line text. A
  container role (`generic`, `group`, `form`, `dialog`, ...) is never a target, even when
  asked for; a query whose text lives only in a container fails naming that container. The
  ref is matched as a word inside its bracket (`[required, ref=e4]`).
- After the two fills the harness reads both fields back (`agent-browser get value`) and
  refuses before the click unless each equals what was typed, printing
  `E2E_FIELD_CHECK email=<bool> password=<bool>`; the failure detail names lengths, never
  values.
- A second probe posts the credentials through the frontend origin the browser will use
  (`E2E_PROXY_PROBE url=... status=... session_cookie=...`), after the backend probe and
  before any browser; it must answer 200 with a session cookie. `harness/serve.py` exposes
  the Vite child's argv and environment as `frontend_launch(backend_port, frontend_port)`
  so the exported `VITE_API_TARGET` is a tested value, not a line in `main`.
- The evidence dump is written under `$ARTIFACTS_DIR/e2e-evidence/`. The validator already
  passes the run's artifacts directory to the evidence program, and the worker now uploads
  that subdirectory; the main-regression workflow sets `ARTIFACTS_DIR` on the gate step and
  uploads the dump and `/tmp/main-regression.log` on every outcome (pinned action, 7 days).
- Two host quirks met on the way are fixed because a maintainer's local run is the only
  place this journey can be re-derived: the CLI is launched by its resolved path (a Windows
  `.cmd` shim is not found by bare name) and the per-command log is left in place when the
  daemon that `open` spawned still holds it.

Pinned by `tests/factory/test_e2e_contract.py` (the recorded real snapshots resolve `e4`,
`e5`, `e3`; a container is refused; the heading is not confused with the input; every
journey `_ref` call is role-qualified; the field check refuses before the click with lengths
only; the proxy probe refuses on 502, on a missing cookie and on an unreachable frontend
before any browser; probe order; the `e2e-evidence` subdirectory; `frontend_launch` names
the chosen backend port) and by `tests/factory/test_factory_workflow_hygiene.py` (the upload
step, its pin, `if: always()`, the paths, and the worker's matching path). Mutations
`e2e-ref-matches-line-text-not-role`, `e2e-field-check-assumed-filled`,
`e2e-proxy-probe-assumed-green`, `e2e-server-vite-api-target-not-exported`,
`e2e-evidence-dump-outside-run-artifacts` and `regression-evidence-upload-only-on-success`
are caught.

**Consequences.** Local proof with the real frontend: `E2E_FIELD_CHECK email=true
password=true`, the click posted the login through the proxy, `/me` carried the cookie, the
page landed on `/` and the message input and send button resolved by role. The remaining
steps (streaming answer, citation, modal) need the real model and are proved only by the
next validation or regression run. PR #96 (issue #49) cannot simply be re-validated: its
head predates this and D-048, its refusal class is `code_holdout` (not re-head eligible),
and the validator's browser journey runs from the trusted base, so once this merges a fresh
validation of #96 would use the fixed harness but its own pack still binds the old kernel;
the overseer closes #96 as superseded and rebuilds #49.

## D-050 · Every model stage of validation leaves a timing record, because 25 minutes of run 33960088633 left none

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33960088633 (validation of PR #99, the first validation after D-048/D-049)

The run's uploaded `stage-timings.jsonl` held six `exec` rows: `backend-sync`,
`frontend-sync`, `security`, `provenance-peek` and `provenance-fetch`, all finished by
10:15:44Z, and `evidence`, started 10:40:37Z and refused after 211 s at the browser journey
(`E2E_FAIL browser state did not appear in 10s`, a separate defect). Nothing was recorded for
the 24 minutes 53 seconds in between, no `agent-*.log` or `agent-*.json` was in the bundle
although the worker workflow's upload globs name them, and the Actions log for the dispatch
step is empty between the step's environment banner at 10:15:37Z and the traceback at
10:44:13Z. Yet `holdout.json`, `architecture-holdout.json`, the three certifications and
`validator-verdict.json` exist, so the five validation authorities ran.

**What the 25 minutes were.** The artifact zip preserves each file's modification time on the
runner, which is the only per-stage evidence the run left:

| stage | wrote its artifact at | wall clock | turn cap | budget at 35 s/turn |
|---|---|---|---|---|
| `holdout` (blinded code holdout) | 10:31:18Z | 934 s | 10 | 350 s |
| `architecture-holdout` | 10:31:40Z | 22 s | 10 | 350 s |
| `contract-certifier` | 10:34:04Z | 144 s | 10 | 350 s |
| `design-certifier` | 10:37:22Z | 198 s | 10 | 350 s |
| `governor-certifier` | 10:40:36Z | 194 s | 10 | 350 s |

1492 s in total, of which the code holdout alone took 934 s: 2.7 times its budget, and by far
the longest single stage of the day (the build run 33956891774, fully recorded, peaked at
`test_author` 797 s / 14 turns). Whether that was one slow process (93 s per turn at ten
turns, against a 35 s ceiling) or two transient retries stacked behind a success (three
attempts of up to 350 s fit the number) cannot be told: the fields that would say so
(`attempts`, `num_turns`, `transient_errors`) were never written. That is the whole finding.

**Why the records were missing.** Not a different `RunPaths`, not a different transcripts
directory, not the upload globs. `_run_blinded_holdout`, `_run_architecture_holdout` and
`_run_precode_certifier` called `self.provider.run(...)` directly and returned the result;
only the build-side `_agent` (and its `worker_runtime` override, D-041) called
`_record_agent`/`_record_failed_agent`. Validation never had a recording path. The log was
silent for a second, independent reason: the kernel's stdout is a pipe under Actions, so an
unflushed `print` reaches the job log when the process exits, and the kernel printed nothing
per stage anyway.

**Decision.** Data first; no behaviour changes.

- `KernelRuntime._agent_stage(paths, request)` is the one place a model is run. It times
  the call, writes `agent-<role>.log`, `agent-<role>.json` and the `kind=agent` timing row on
  return, writes the failed record and re-raises on any exception (D-041), and hands the
  result back unchanged. Both `_agent` paths and the three validation authorities go through
  it; `tests/factory/test_factory_validation_stage_telemetry.py` pins by AST that
  `runtime.py` calls `provider.run` from nowhere else and that `worker_runtime.py` never
  calls it directly. A stage with no record is now a stage that never ran.
- Every record and row carries `outcome` (`ok`, `failed`; `refused` for a gate that exited
  non-zero), `model`, `cost_usd` (the row; the JSON keeps `total_cost_usd`), `num_turns`,
  `seconds`. A returned stage says `ok` where it used to say nothing; the one test that pinned
  the absence is updated.
- `record_stage_timing` prints one flushed line per stage as it ends, deterministic gates
  included: `FACTORY_STAGE kind=agent|exec name=<role or gate> seconds=<n> [turns=<n>]
  [cost_usd=<x>] outcome=ok|failed|refused [over_budget=true]`. The Actions log reads as a
  live progress line rather than a post-mortem.
- `over_budget` is set on the record, the row and the line when a model stage's wall clock
  exceeded `max_turns(role) * OBSERVED_SECONDS_PER_TURN_CEILING`
  (`worker_policy.stage_budget_seconds`). Telemetry only: the result is returned, nothing
  refuses, the caps are unchanged. The 934 s holdout above would have carried it; the agreed
  policy is to tune the caps from real telemetry (D-025), and this is the field the tuning
  reads.
- Observed on the way and deliberately left alone, because this change is data only: the
  direct calls passed neither `allowed_tools` nor `max_budget_usd` to the authorities, unlike
  the build workers. The first recorded validation run says what that costs before anything
  is changed.

Pinned by `tests/factory/test_factory_validation_stage_telemetry.py`: the real `validate_pr`
through the rehearsal harness records all five authorities (record, row, log text, stage
line); a holdout or certifier that raises is recorded under its role with the carried
telemetry and re-raised; a certifier that returned a rejection is an `ok` stage whose refusal
is the kernel's; the exec line omits turns and cost and says `refused` on a non-zero exit;
the print is flushed; the budget is the cap at the ceiling, 934 s against `holdout` is
flagged and 350 s is not; a slow returned and a slow failed stage are flagged in record,
row and line, a stage within budget nowhere; a flagged holdout still returns its verdict.
Mutations verified by direct injection on the maintainer's host (the Windows mutation harness
is unreliable there, and `harness/` was under concurrent change, so they are not yet in
`harness/factory_mutations/defects.json`; that registration is a follow-up):
`validation-stage-records-nothing` (the holdout calls `provider.run` directly again),
`stage-line-dropped`, `stage-line-unflushed` and `over-budget-never-set` are each caught.

**Consequences.** The next validation run uploads five `agent-*.json` records and prints its
progress live; cap tuning for the authorities happens after that run, from its numbers, not
from this one's reconstruction. The refusal that ended run 33960088633 (`evidence_spine`, the
browser journey timing out on the chat page after a successful login) is a separate defect
and is not addressed here.

## D-051 · The streaming step carries its own evidence, because the app process log was a pipe nobody read

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33960088633 (validation of PR #99, after D-049)

The first validation to get past the browser login failed at the streaming step:
`E2E_FAIL browser state did not appear in 10s`, waiting for the transient "Stop response"
button. The evidence dump D-049 made uploadable said what the page looked like and nothing
about why. `network.txt` held `POST /api/conversations/<id>/messages (Fetch)` with no status,
followed by two green GETs; the snapshot showed the question, the "Send message" button, no
assistant text and no inline error; `console.txt` and `errors.txt` were Vite and React
Router noise; `page.html` was 73 bytes of `Missing arguments for: get html`, because the
capture called `get html` without the selector the CLI requires. The one place the backend
says why a stream breaks (`OpenRouter streaming API error` / `Unexpected error during
streaming` in `llm/openrouter.py`, uvicorn's traceback, the bootstrap's own
`E2E_BOOTSTRAP_OK` line printed through `serve.py`) was the app process's stdout, which
`appproc.HttpApp` opened as a pipe and read only on the never-healthy path. After
`APP_STARTED` nothing drained it; everything printed was lost, and a child that filled the
64 KiB buffer would have blocked. The run could not be reproduced locally (no database, no
keys), so the next run has to carry its own evidence.

**Decision.** Ask every boundary from the harness side before the browser, record the
stream window instead of waiting for one transient state, and keep the app log.

- `harness/appproc.py`: `HttpApp` drains the child's combined output on a daemon thread
  into `app-process.log` (under `$ARTIFACTS_DIR`, else a temp file) from before the health
  wait; the never-healthy path reads that file; `APP_STARTED` prints `app_log=<path>`. The
  README's "verbatim from the skill" note now names this one addition.
- `harness/e2e.py` after the login probes and before any browser: when
  `DARK_FACTORY_E2E_BOOTSTRAP=1` the app log must carry `E2E_BOOTSTRAP_OK
  fixture_video_id=<locked id>` (`E2E_BOOTSTRAP_SEEN`, else `E2E_BOOTSTRAP_MISSING` and a
  refusal); `GET /api/videos` with the session cookie must list the fixture
  (`E2E_VIDEOS_PROBE count=N fixture_present=<bool>`); then the streaming route is asked the
  locked question through the frontend origin, reading the body incrementally with the
  journey's `response_timeout_s` (`E2E_STREAM_PROBE status=<n> content_type=<ct>
  first_byte_ms=<n> events=<n> tokens=<n> sources=<bool> done=<bool> error=<payload or ->`
  plus the first 300 scrubbed characters of the body). The probe passes only on 200 with at
  least one token and no error payload; an explicit error payload fails it with that error
  as the named cause. The probe's conversation is deleted so the browser still lands on an
  empty surface. It spends one of the synthetic account's 25 daily messages; the validation
  database is disposable, so the counter starts at zero each run.
- The 10-second wait for "Stop response" is replaced by a recorder that snapshots the page
  every half second until the citation predicate holds or `response_timeout_s` elapses,
  writing each distinct state (stop button, send button, inline error, assistant text,
  citation) with its timestamp to `e2e-evidence/stream-states.jsonl` and printing
  `E2E_STREAM_UI states=[...]`. The transient state is not a hard requirement: the step
  passes when "Stop response" was seen at least once or the answer arrived within one poll.
  The citation and modal requirements are unchanged.
- `page.html` is captured with `get html html` (the document element).
- `e2e_timeout_s` in `harness.config.json` rises from 180 to 360: the rung now streams the
  question twice, each bounded by `response_timeout_s` (90), plus probes and browser startup.
- On any failure, browser or probe, the scrubbed app log is copied into the evidence dump
  as `app-process.log` and its last sixty lines are printed as `E2E_APP_LOG_TAIL`. Scrubbing
  covers the validation password, every environment value whose name ends in `_KEY`,
  `_SECRET`, `_TOKEN` or `_PASSWORD`, `DATABASE_URL` and the password inside it,
  `JWT_SECRET`, session cookie values and bearer tokens.

Pinned by `tests/factory/test_e2e_stream_evidence.py` (the SSE parser; the probe's marker,
its requirement on status, token and error payload, its cookie and cleanup; the recorder's
timeline file and its pass rule; the bootstrap check's seen/missing/absent-log/other-video
answers and its refusal before the browser; the videos probe; the scrubber; the app-log tail
on every failure; a child that prints more than the pipe holds still becomes healthy under
`HttpApp`; and the structural rule that the drain thread starts before the health wait) and
by the updated fakes in `tests/factory/test_e2e_contract.py`. Mutations
`e2e-stream-probe-accepts-any-status`, `e2e-stream-window-requires-transient-stop`,
`e2e-app-log-tail-not-printed` and `e2e-bootstrap-missing-non-fatal` are caught.

**Consequences.** The next validation or main-regression run names its own cause at the
streaming step: a refused or broken route in `E2E_STREAM_PROBE`, a missing fixture in
`E2E_BOOTSTRAP_MISSING` or `E2E_VIDEOS_PROBE`, a page that never streamed in
`E2E_STREAM_UI`, and the backend's own words in `E2E_APP_LOG_TAIL`. The cause of run
33960088633 itself is still unknown; this change is what makes it readable rather than a
guess. The journey now counts 20 deterministic steps instead of 16.

## D-052 · Every validation authority is bounded in tools and spend, because the 934-second holdout of run 33960088633 was bounded in neither

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33960088633 (validation of PR #99), the observation D-050 deliberately left alone

D-050 reconstructed the five validation authorities of run 33960088633 from artifact
modification times and found the blinded code holdout at 934 s against a 350 s budget. It
also noted, and left alone because that change was data only, that the authorities were
constructed differently from the build workers. The build side (`worker_runtime._agent`)
passed `allowed_tools=allowed_tools(role)`, `max_turns=max_turns(role)` and
`max_budget_usd=max_budget_usd(role)`. The validation side (`_run_blinded_holdout`,
`_run_architecture_holdout`, `_run_precode_certifier`) and the base runtime's `_agent` passed
`max_turns` and nothing else; triage passed nothing at all. `ClaudeCliProvider.run` renders a
flag only for a value that is present, so a missing `max_budget_usd` is not a default budget,
it is `--max-budget-usd` absent: the holdout's 934 s had a turn cap and the 1200 s subprocess
timeout behind it and no dollar bound at any point. The tool surface was correct by accident:
`allowed_tools=None` and `allowed_tools=()` both render as `--tools ""` (every built-in tool
disabled), so the judges could touch nothing, but nothing said so; `worker_policy.ROLE_TOOLS`
carried `()` for every authority and no request read it.

**What a judge needs.** Nothing. Every authority runs in an empty temporary directory,
deliberately away from the checkout (the rehearsal's fake provider refuses an authority whose
`cwd` is the repository), and everything it is entitled to see arrives inside its prompt: the
contract, the diff, the RED/GREEN proof summary, the verified builder pack. The holdout prompt
says so in its first sentence. So the surface is empty rather than read-only: a judge that can
edit a tree is a defect, and a judge that can read one is no longer blinded. Triage is the same
shape (MISSION.md, FACTORY_RULES.md and the candidate batch are in its prompt; it decides, it
does not investigate).

**Decision.** Every model call carries all three bounds from the policy, and the funnel
refuses one that does not.

- `worker_policy.JUDGE_TOOLS = ()` is the tool surface of every authority and of triage,
  stated and documented rather than defaulted; `AUTHORITY_ROLES` names the five. The budget
  rows the authorities already had (2.0, triage's cap: ten turns, no tools, one prompt) are now
  read, with the reason beside them.
- Every `AgentRequest` the kernel constructs passes `allowed_tools=allowed_tools(role)`,
  `max_turns=max_turns(role)` and `max_budget_usd=max_budget_usd(role)`: the four sites in
  `runtime.py` (base `_agent`, the code holdout, the architecture holdout, the certifier), the
  one in `worker_runtime.py` (already did) and the one in `triage.py`.
- `KernelRuntime._agent_stage`, the one place a model is run (D-050), refuses a request that
  arrives with any of `REQUEST_BOUNDS` (`allowed_tools`, `max_turns`, `max_budget_usd`) unset,
  before a process starts and before any record is written. An unbounded request is a kernel
  defect, not a stage that failed, so it is a plain `RuntimeError` naming the role and the
  missing bounds rather than a recorded failed stage.
- Observed and left alone: the provider itself still accepts an unbounded `AgentRequest` (its
  own unit tests build them); the funnel is the guard. Triage is bounded but still calls the
  provider directly, because it has no run directory and therefore no record; giving it one
  is a separate change. The `over_budget` flag and the caps are unchanged: the 934 s holdout
  would now stop at $2 rather than at the subprocess timeout, and the caps are still to be
  tuned from the next recorded run (D-025, D-050).

Pinned by `tests/factory/test_factory_authority_bounds.py`: by AST, every `AgentRequest(...)`
in `runtime.py` (4), `worker_runtime.py` (1) and `triage.py` (1) names all three bounds, each
a call to the policy function of the same name with the request's own `role` expression, with
no keyword splat and no local shadow, and no other kernel module constructs one; the policy
gives every authority and triage an empty surface, a row in every table, and triage's budget;
the real `validate_pr` through the rehearsal harness hands the provider each of the five
authorities with exactly the policy's tools, turns and dollars, no environment and a `cwd`
outside the repository; the CLI provider renders the code holdout's and a certifier's request
as `--tools ""`, `--max-turns 10`, `--max-budget-usd 2` with no `--allowedTools`; the funnel
refuses a request missing any one bound, names every missing bound, calls no provider and
writes no record, and runs a bounded one; both `_agent` paths are bounded; and the triage
request is. Mutations `holdout-budget-dropped`, `certifier-tools-widened`,
`agent-stage-accepts-unbounded-request`, `judge-tools-widened-in-policy` and
`triage-turns-unbounded` are registered in `harness/factory_mutations/defects.json` and
caught, as are D-050's four (`validation-stage-records-nothing`, `stage-line-dropped`,
`stage-line-unflushed`, `over-budget-never-set`), whose registration D-050 deferred. Both
detector files are now in the mutation runner's copy list. All nine were verified by direct
injection on the maintainer's Windows host (the copy built by `run.py`, one defect injected,
the two detector files run; the rehearsal needs `FACTORY_WORKDIR` set to an absolute Windows
path there) and by CI.

**Consequences.** The next validation run's five `agent-*.json` records are for stages that
were bounded in tools, turns and dollars, so an outlier in them is a slow model, not an open
loop. A future authority added without its bounds fails the AST pin at test time and the
funnel at run time; a future one added with a widened surface fails the rehearsal's
no-authority-can-write check.
