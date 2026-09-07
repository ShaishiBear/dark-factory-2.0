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
## D-053 · The validation database is pgvector, because production's is and the retrieval SQL says `::vector`

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33963509318 (main regression, the first run carrying D-051's evidence)

The first run to keep its app log named the streaming step's cause in one line. The
bootstrap printed `E2E_BOOTSTRAP_OK fixture_video_id=pjF-0dliYhg chunks=209`, so the fixture
ingested; on the question the backend printed `WARNING:backend.rag.tools:search_hybrid
failed: type "vector" does not exist`, raised from `vector_search_pg` in
`app/backend/db/repository.py` (`asyncpg.exceptions.UndefinedObjectError`), and the same
for `search_semantic`. Both `dark-factory-worker.yml` and `dark-factory-main-regression.yml`
provisioned the validation database from the plain `postgres:16` service image, which does
not ship pgvector, and no migration created the extension: `0001_initial.py` creates
`citext` and `pgcrypto` and nothing else. Production runs `pgvector/pgvector:pg16`
(`deploy/docker-compose.yml`) and its extension was evidently created by hand once. The
embedding column is TEXT, cast with `::vector` only at query time, so ingestion succeeds
everywhere and only retrieval fails, degrading every answer to "no sources". The browser
journey could never have seen a citation in validation, and every autonomous PR would have
failed its E2E on a defect that was never the PR's.

**Decision.** The validation database is the production image, and the schema creates the
extension it depends on.

- Both workflows' `postgres` service image is `pgvector/pgvector:pg16`, the same major as
  production. The health-check options and everything else are unchanged;
  `test_service_block_is_the_workers_verbatim` keeps the two blocks identical.
- Migration `0006_pgvector_extension.py` runs `CREATE EXTENSION IF NOT EXISTS vector`.
  pgvector is marked trusted, so the database owner (the app's own connection) can create
  it without superuser rights. Downgrade is a no-op: dropping the extension would refuse
  while any object of type `vector` exists or cascade to those objects, a shared database
  may have other users of it, and unused it is harmless. This is the only product-code
  change and, being a schema change, an Alembic migration as CLAUDE.md requires.
- `deploy/README.md` says the migration creates the extension, so a fresh production
  database needs no manual step.

Pinned by `app/backend/tests/test_migration_pgvector_extension.py` (0006 follows 0005 and is
the sole head; `upgrade()` issues exactly that statement through a fake `op`; `downgrade()`
issues nothing; no database) and by `ValidationDatabaseTests` in
`tests/factory/test_factory_workflow_hygiene.py` (both workflows' postgres image starts with
`pgvector/pgvector:` and pins `pg16`); `test_worker_provisions_disposable_validation_state`
in `tests/factory/test_factory_github_e2e_bootstrap.py`, which pinned the literal
`postgres:16`, now pins `image: pgvector/pgvector:pg16`. Mutation
`validation-db-without-pgvector` (the worker's image reverted to `postgres:16`) is caught.

**Consequences.** The next validation or main-regression run reaches the citation step with
retrieval working; whatever fails there next is a new cause. The validation database is
disposable, so no existing database needs `CREATE EXTENSION` by hand; production already
has the extension and `IF NOT EXISTS` makes the migration a no-op there. On a plain
`postgres` image the migration now fails startup loudly instead of retrieval failing
quietly per query. The embedding column is still TEXT cast at query time
(`vector_search_pg`'s docstring says so); moving it to `vector(1536)` with an HNSW index is
a separate decision.
## D-054 · A worker's timeout is measured on its event stream, and leaves telemetry, because the 1200-second `test_author` of run 33987381035 left none

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33987381035 (build of issue #103)

The run's stage lines, printed live by D-050's funnel:

```
FACTORY_STAGE kind=agent name=investigate seconds=888.607 turns=22 cost_usd=1.467 outcome=ok
FACTORY_STAGE kind=agent name=contract seconds=280.317 turns=12 cost_usd=0.836 outcome=ok
FACTORY_STAGE kind=agent name=context seconds=696.978 turns=30 cost_usd=1.478 outcome=ok
FACTORY_STAGE kind=agent name=architecture seconds=262.363 turns=12 cost_usd=0.649 outcome=ok
FACTORY_STAGE kind=agent name=test_author seconds=1200.121 turns=0 cost_usd=0.0 outcome=failed over_budget=true
```

`test_author` died at the 1200 s subprocess timeout: `subprocess.TimeoutExpired`, refused as
`agent worker timed out role='test_author' after 1200.1s (timeout_seconds=1200,
max_turns=30); partial output:` and nothing after the colon. Its record
(`agent-test_author.json`) says `attempts=1`, `num_turns=0`, `duration_ms=0`,
`total_cost_usd=0`, `timed_out=true`, `transient_errors=[]`. Two facts follow.

**(a) The stage that most needed measuring left nothing.** With `--output-format json` the
CLI prints its one envelope when the session ends, so a process killed before then has
printed nothing at all. Whether the worker had done twenty-nine turns of work or none, whether
it was hung on a stuck API stream or slowly writing tests, cannot be told from the record:
`turns=0` means "no envelope", not "no turns". The build died at its most expensive stage
with zero evidence, twenty minutes after the last thing it said.

**(b) The stated ceiling was already refuted by the data.** `OBSERVED_SECONDS_PER_TURN_CEILING
= 35` (D-025, stated from one 33.85 s/turn observation) and the same run measured
`investigate` at 888.6 / 22 = 40.4 s per turn (the others: contract 23.4, context 23.2,
architecture 21.9). A 30-turn worker at 40.4 s needs 1212 s; the single global wall of
1200 s sat exactly on the turn budget, so a worker that legitimately used its cap at the
observed rate was killed rather than stopped at `--max-turns` with a clean envelope.

**Decision.** The timeout is measured on the event stream, per role, and a killed process
still reports what it showed.

- **Stream-aware launch.** The CLI is run with `--output-format stream-json --verbose` (in
  print mode the CLI refuses `stream-json` without `--verbose`; verified against the
  installed 2.1.259). It prints one JSON object per line as the session runs. The shapes the
  kernel relies on, from a probe of the installed CLI: `{"type":"system","subtype":"init",
  "session_id":...,"model":...}`; `{"type":"assistant","message":{"id":...,"usage":{
  "input_tokens","output_tokens","cache_creation_input_tokens","cache_read_input_tokens"},
  "content":[...]},"session_id":...}` (possibly once per content block of the same message
  id); `{"type":"user","message":{"content":[{"type":"tool_result",...}]}}`; and the final
  `{"type":"result","subtype":...,"is_error":...,"result":...,"num_turns":...,
  "duration_ms":...,"total_cost_usd":...,"usage":{...},"session_id":...}`, which carries
  exactly the fields the `json` envelope did. `_stream_cli` reads stdout line by line on a
  reader thread with a timestamp per event; `_launch` hands the final `result` event to the
  unchanged `unwrap_result_envelope`, so every consumer sees what it saw before. The parser
  is tolerant (a line that is not a JSON object with a string `type` is ignored), and a
  one-line `json` envelope is a stream of exactly one `result` event, so nothing is coupled
  to the flag; the `json` format is dropped outright rather than kept selectable, because
  keeping it would keep the defect selectable. The prompt travels on argv, so stdin is now
  `DEVNULL`: the worker never reads it, and an open pipe is one more way for a print-mode
  CLI to wait forever.
- **Idle-hang detection.** `provider.idle_timeout_seconds` (`kernel.json`, default 420,
  refused above `timeout_seconds`): no stream event for that long kills the process. A
  working CLI prints an event per model turn and per tool call, so the longest legitimate
  silence is one model call; the slowest turn observed is 40.4 s, and seven minutes is ten
  times that. The kill raises `WorkerHungError`, a `TransientProviderError` whose envelope
  is what the events showed (turns per distinct assistant message id, tokens summed per
  message, the session id) and whose telemetry is the hang itself (`hang=true`,
  `last_event_age_s`, `wall_seconds_last_attempt`, the last five event lines as
  `partial_output`, capped at 1500 characters). The existing retry loop relaunches it once
  (`HANG_RETRIES = 1`), calling `before_retry` first so a mutation role's worktree is
  restored (D-031); a second hang is terminal whatever the transient budget still allows,
  because a process that hangs twice on the same prompt is not suffering the network.
- **Per-role wall.** `worker_policy.stage_timeout_seconds(role) = ceil(max_turns(role) ×
  OBSERVED_SECONDS_PER_TURN_CEILING × 1.5)`: 2025 s for a 30-turn role, 1620 s for `context`
  (24), 1350 s for `triage` (20), 675 s for the ten-turn judges. The 1.5× headroom is for the
  work between turns that is not a model call and for the spread the four observations
  already show (21.9 to 40.4 s/turn). Every `AgentRequest` carries it as `timeout_seconds`
  (all six construction sites; the `_agent_stage` funnel requires it as the fourth bound
  beside tools, turns and dollars, D-052); the provider uses it as the wall for each process,
  clamped to `provider.timeout_seconds`, which is raised to 2700 and becomes the global
  maximum every wall must fit under (`assert_caps_fit_timeout`), not a wall itself.
- **The ceiling is stated from data, not tuned.** `OBSERVED_SECONDS_PER_TURN_CEILING = 45`,
  citing the run's four observations in the comment beside it. It is a ceiling above the
  highest measured rate with a margin; the caps are unchanged, and `over_budget` keeps its
  meaning (`max_turns × ceiling` exceeded, now 1350 s for a 30-turn role).
- **A timed-out or hung stage leaves telemetry.** The refusal carries the partial envelope,
  the retry loop sums it with every earlier attempt's, and `_record_failed_agent` writes the
  sum: `num_turns`, tokens, `events_seen`, `last_event_age_s`, `partial_output`,
  `timed_out`/`hang`, and the role's `timeout_seconds`. The stage line gains three optional
  fields, `FACTORY_STAGE ... outcome=failed [events=N] [timed_out=true] [hang=true]
  [over_budget=true]`, and a returned stage now also says `events=N`. Dollars are known only
  from a `result` event, so a stage whose only attempt was killed before one reports its cost
  as unknown (omitted from the line) rather than as a false `0.0`.

Pinned by `tests/factory/test_factory_stream_timeouts.py`, whose end-to-end cases launch a
fake CLI (a Python script the test writes, emitting stream-json lines with controllable
pacing) through the provider's own argv and reader: a streamed session yields the same
`AgentResult` the json envelope did, with `--output-format stream-json --verbose` in the
argv; a hung process is killed at the idle timeout, recorded (`hang`, `events_seen` and
turns summed across attempts, `last_event_age_s`, the partial output), retried exactly once
with the restore hook called, then terminal; a hang followed by a healthy process is one
retry whose turns include the hung attempt's; events 0.3 s apart under a 1 s idle timeout
are progress, not a hang; a process killed at the request's 2 s wall (configured maximum
60 s) records partial turns, tokens and events and is not retried; the reader is given the
request's wall and the configured idle timeout, and a wall above the maximum is clamped;
the partial envelope counts one turn per assistant message id and leaves the cost unknown;
the record, row and stage line of a timed-out and of a hung stage carry the fields above;
every role's wall is `ceil(cap × 45 × 1.5)`, fits under the checked-in 2700, and the old
1200 could not hold a 30-turn role; the ceiling is above 40.4 and `over_budget` still means
the turn budget; `idle_timeout_seconds` is parsed, defaults to 420 when absent, and refuses
non-positive, non-integer and larger-than-`timeout_seconds` values. Updated:
`test_factory_worker_throughput.py` (argv, the timeout carrying `events_seen`, the ceiling at
least 41), `test_factory_authority_bounds.py` (the fourth bound at every site, from
`stage_timeout_seconds`), `test_factory_validation_stage_telemetry.py` (the stage-line shape,
the holdout budget now 450 s), and the provider tests that faked `subprocess.run` now fake
`_stream_cli` with a `CliRun`. Mutations `idle-detection-never-fires`, `hang-not-retried`,
`timed-out-telemetry-dropped`, `per-role-wall-ignored` and `ceiling-reverted-to-35` (with
`assert_caps_fit_timeout` still passing) are registered in
`harness/factory_mutations/defects.json`, the detector file is in the runner's copy list,
and each was verified by direct injection on the maintainer's Windows host (the copy built by
`run.py`, one defect injected, the two detector files run).

**Observed and left alone.** The brief for this change assumed the test author runs the
unit suites itself; it cannot (`WRITE_TOOLS` carries no Bash, and the
`worker-shell-tool-enabled` mutation keeps it that way). The kernel runs the static gate and
the suites after the author returns, outside the stage's wall. The headroom is for tool
calls and turn variance, not for test runs. The caps and the dollar backstops are unchanged;
the walls and the idle timeout are the next numbers to tune from `stage-timings.jsonl`
(D-025), and the first stream-read build is the evidence for that.

**Consequences.** A hung worker now costs at most two idle timeouts (14 minutes) plus a
restore before the stage is refused with its evidence, instead of twenty silent minutes with
none; a slow worker is stopped at `--max-turns` with a clean envelope before its wall, as
D-025 intended; a stage that is killed says what it had done. A 30-turn stage may now run
2025 s rather than 1200 s before its wall, so a build's worst case is longer, but bounded
per role and measured per turn. The next build's stage lines carry `events=N`, and the
next timed-out stage, if there is one, is a record to read rather than a gap to reconstruct.

---

## D-055 · Every worker runs at a stated effort, and every stage keeps its whole transcript, because the 2025-second `test_author` of run 33992451400 thought at the CLI's default and left 1500 characters

**Status:** recorded · **Raised:** 2026-09-05 · **Run:** 33992451400 (build of issue #103, the first stream-read build after D-054)

The run's stage lines, with D-054's `events=N`:

```
FACTORY_STAGE kind=agent name=investigate seconds=782.704 turns=17 cost_usd=1.388 outcome=ok events=30432
FACTORY_STAGE kind=agent name=contract seconds=366.228 turns=20 cost_usd=0.940 outcome=ok events=11614
FACTORY_STAGE kind=agent name=context seconds=461.222 turns=29 cost_usd=1.208 outcome=ok events=13790
FACTORY_STAGE kind=agent name=architecture seconds=203.013 turns=10 cost_usd=0.679 outcome=ok events=5414
FACTORY_STAGE kind=agent name=test_author seconds=2025.084 turns=14 outcome=failed events=76248 timed_out=true over_budget=true
```

D-054 worked: the killed stage has a record. `agent-test_author.json` says `num_turns=14`,
`events_seen=76248`, `last_event_age_s=0.0` (alive to the last second, not hung), and its
`partial_output` is five consecutive `{"type":"system","subtype":"thinking_tokens",
"estimated_tokens":13247,"estimated_tokens_delta":1,...}` lines. Per turn: `test_author`
5,446 events and 145 s, against 476-1,790 events and 16-46 s for the four stages before it.
The design was small (three acceptance criteria, one new file,
`app/frontend/src/components/ChatArea.test.tsx`). Two facts follow.

**(a) Nothing bounded how long a turn thinks.** The turn cap bounds iterations and the
dollar cap bounds the resent conversation; a turn that streams thinking for minutes is one
turn and a few cents of output tokens, and it sits inside both. The kernel named no effort
level, so every request ran at the CLI's default, which the CLI's documentation says is
`high` ("balances token usage and intelligence") on every model; the route is OpenRouter's
Anthropic-compatible endpoint to `z-ai/glm-5.3-flash`, and what that model does with the
default is what the stream shows: it thinks without bound. The installed CLI (2.1.259) and
the workflow's pin (2.1.245) both accept `--effort <level>` with levels `low`, `medium`,
`high`, `xhigh`, `max` (`claude --help`); the documentation calls effort the control on
adaptive reasoning, "whether and how much to think on each step", with `low` reserved for
"short, scoped, latency-sensitive tasks that are not intelligence-sensitive", `medium` as
"reduces token usage for cost-sensitive work that can trade off some intelligence", and
`max` "prone to overthinking".

**(b) A killed stage leaves a tail, not a transcript.** `_record_agent` wrote
`agent-<role>.log` (the worker's final text) only for a stage that returned; a stage that
was killed had only `partial_output`, the last five event lines capped at 1500 characters.
Fourteen turns and 76,248 events of what the worker read, wrote and thought are gone, and
whether it was writing the test file, re-reading the tree, or looping on one thought cannot
be told from what was kept.

**Decision.** Every request names an effort level, every stage streams its transcript to
disk as it runs, and the thinking each stage showed is recorded beside its turns.

- **Per-role effort.** `worker_policy.EFFORT_LEVELS = ("low", "medium", "high", "xhigh",
  "max")`, the pinned CLI's scale lowest first, and `ROLE_EFFORT`, a table beside
  `ROLE_MAX_TURNS` with a row for every role. Workers (every role that edits or drafts
  against a checkout: `plan`, `investigate`, `contract`, `context`, `architecture`,
  `test_author`, `implement`, `repair`, `conformance`, `review-spec`, `review-standards`)
  run at `medium`: one notch below the default, so thinking is bounded but not disabled.
  `low` was considered for the three mutation roles, whose per-turn work is file edits, and
  not chosen: the documentation reserves it for work "not intelligence-sensitive" and
  effort is adaptive, so at `low` a step may not think at all; choosing what a test should
  assert, or how a fix should land, is intelligence-sensitive, and the defect being
  corrected is unbounded thinking, not thinking. Judges (the five validation authorities
  and triage) run at `high`, the default: tool-less single-prompt calls where reasoning is
  the whole job, already bounded to ten turns; `max` is documented as prone to overthinking
  and `xhigh` is not offered on every model and would fall back silently. `effort(role)`
  is the fifth bound every `AgentRequest` carries (all six construction sites; the
  `_agent_stage` funnel refuses a request without it, beside tools, turns, dollars and the
  wall, D-052, D-054), and `ClaudeCliProvider.run` renders it as `--effort <level>` on
  every launch after applying `provider.effort_overrides` from `kernel.json`, a
  `{role: level}` table validated at load against the policy's roles and the CLI's levels
  (a typo is a refused configuration, not a silent default); the provider refuses a level
  the CLI does not accept before any process starts. The checked-in table is empty.
- **The route is measured, not assumed.** The scale is calibrated per model and the route
  is not Anthropic's, so the worker workflow's preflight, after the route probe (which now
  makes a judge's request, `--effort high`), runs `scripts/factory_effort_probe.py`: the
  worker model twice on one fixed reasoning prompt ("Plan, in numbered steps, how you
  would add a column to a Postgres table without downtime."), one turn and one dollar
  each, at the lowest and the highest level the policy uses (`medium` and `high`, the
  spread the policy relies on; `--low`/`--high` widen it), counting the thinking each
  stream showed with the same estimator the stage records use. It prints
  `FACTORY_PREFLIGHT_EFFORT_PROBE model=<slug> low_thinking=<n> high_thinking=<m>
  honoured=true|false low_level=<l> high_level=<h> low_events=<n> high_events=<n>
  [error=<what>]` and exits 0 whatever it found; `honoured` means the higher level thought
  at least 1.5× and at least 100 tokens more. A `honoured=false` is data for tuning the
  levels, never a refusal: the run continues, and the next build's `thinking=` fields say
  what the levels bought.
- **Full stream logs for every stage.** `_agent_stage` hands the provider
  `transcripts/agent-<role>.log`; `_launch` opens it for append before the process starts,
  writes `--- attempt N role=<role> started=<utc> ---`, and `_stream_cli` writes every
  stdout line to it, flushed, as the reader sees it and before it is parsed, then an end
  marker (`--- attempt N ended rc=... timed_out=... hung=... elapsed=...s events=...
  thinking=... ---`). Every attempt of a retried stage appends under its own header. A
  process killed at its wall or its idle clock therefore leaves exactly what it had printed.
  `_record_agent` no longer overwrites that file: it writes the worker's text there only
  when the file does not exist (a provider that does not stream, such as the rehearsal
  fakes), and `partial_output` stays in the JSON record as the capped tail for quick
  reading.
- **Thinking telemetry.** `providers.thinking_tokens(events)` sums the CLI's
  `thinking_tokens` events. Whether `estimated_tokens` counts from the session's start or
  restarts with each turn is not documented, so the sum is taken in the way that is right
  either way: the counter is walked in order, a value below the one before it is a reset,
  and the high-water mark of every monotone run is summed (one run whose mark is the final
  value if the counter never resets; one run per turn if it does). The per-event
  `estimated_tokens_delta` is not used: summing it would be exact only if no line were ever
  dropped and the first event after a reset carried its own value as its delta, and
  neither is promised, whereas a high-water mark survives a missed line. The figure rides
  on every envelope (`ResultEnvelope.thinking_tokens`, from the events of a returned and
  of a killed process alike), is summed across attempts, and lands in `agent-<role>.json`
  as `thinking_tokens`, in the timing row, and on the stage line as `thinking=<n>`, beside
  `effort=<level>`, the level the CLI was actually asked for. The `result` event's
  `usage.output_tokens_details.thinking_tokens`, where the route reports it, is kept as
  `thinking_tokens_reported` for reconciling the estimate against the bill.

Pinned by `tests/factory/test_factory_effort_and_stream_logs.py`, which reuses the fake CLI
of D-054's detector: every role with a turn cap has a level from the CLI's scale, judges at
`high` and workers at `medium`, and the workflow pins a CLI at or above 2.1.245; a worker's
argv carries `--effort medium` and a judge's `--effort high`, a configured override wins and
is what the result and the record say, an unknown level is refused before any launch, and a
failed stage carries the level it ran at; the funnel refuses a request without `effort`,
names it, calls no provider and writes no record, hands the provider the stage's log path,
and writes the worker's text to that path only when nothing was streamed there; the
override table is parsed, defaults to empty, and refuses an unknown role, a level the CLI
does not accept, and a value that is not an object; the log of a completed, a wall-killed,
a hung-then-retried, a terminally hung, and a never-printed stage holds every stream line
under its attempt headers, and through the funnel the log holds more than the capped tail
the record keeps; the estimator sums high-water marks across resets and ignores deltas,
survives a missed line, ignores other events, and reaches both envelopes; the record, row
and line of a returned, a killed and a retried stage carry `thinking=` and `effort=`; and
the probe's argv, margin rule, default levels, line format (honoured, not honoured, an
errored call, explicit levels), exit code and place in the workflow. Updated:
`test_factory_authority_bounds.py` (the fifth bound at every site, from `effort`),
`test_factory_stream_timeouts.py` and `test_factory_validation_stage_telemetry.py` (the
fifth bound, the stage-line shape). Mutations `effort-flag-dropped`,
`judge-effort-lowered-to-worker-level`, `stream-tee-dropped`,
`thinking-telemetry-always-zero` and `effort-override-not-validated` are registered in
`harness/factory_mutations/defects.json`, the detector file and the probe script are in the
runner's copy list, and each was verified by direct injection on the maintainer's Windows
host (the copy built by `run.py`, one defect injected, the detector file run).

**Observed and left alone.** The levels are stated from the CLI's documentation, not from
a measured run; the first build at `medium` is the evidence for tuning them, and the probe
line says whether the route honours them at all. The caps, the dollar backstops, the walls
and the idle timeout are unchanged. The `result` event's reported thinking count and the
stream's estimate are both recorded and not yet reconciled.

**Consequences.** A worker's turn is bounded on a third axis, and the next `test_author` at
`medium` either finishes inside its wall or leaves a full transcript that says why it did
not. Every stage line now ends `thinking=<n> effort=<level>`, so over-reasoning is visible
per stage as it happens rather than reconstructed from an event count. The preflight costs
two one-turn calls more per run.

---

## D-056 · A refused RED names its output, and an operator stop is not a failure, because run 33997386843 kept a verdict and run 33989911383 escalated a button press

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 33997386843 (build of issue #103, the first `test_author` at `medium` after D-055) and 33989911383 (build of issue #49)

Two build endings from the same day, each recorded wrongly in its own way.

**(a) The RED gate refused without saying why.** Run 33997386843's `test_author` returned
after 1375 s and 33 turns (`over_budget=true`, a separate matter), and then:

```
FACTORY_STAGE kind=exec name=red-gate seconds=0.448 outcome=refused
red-gate.log: PROOF_FAIL: AC-1 RED failed for the wrong reason
```

Four vitest checkpoints, 0.448 s for all of them, so something failed before any test
executed, and nothing says what. `scripts/factory_proof.py red` ran the checkpoint's argv,
compared its output with `expected_failure`, and on a mismatch called `die` with the
verdict alone: the argv, the exit code, the seconds and the output were local variables of
a loop that had just ended. No artifact was written. The kernel's failure comment carried
the first 1500 characters of the tool's message, which was that one line. The build ended
at `factory:needs-human` with nothing a human could diagnose from; whether `bun` was
missing, `vitest` was not installed in the worktree, the test file did not parse, or the
author's `expected_failure` was simply not the string vitest prints, could only be found by
re-running the build. A command that could not be launched at all, or that ran past the
300-second timeout, was worse: `subprocess.run` raised, the traceback was the log, and the
argv that produced it was not in the traceback.

**(b) An operator stop was recorded as a build failure.** Run 33989911383 was building #49
when a stop issue was opened. The kernel re-read the stop between `investigate` and
`contract`, correctly:

```
KERNEL_STOP_CHECK_OK
FACTORY_STAGE kind=agent name=investigate seconds=417.004 turns=6 cost_usd=0.764 outcome=ok
KERNEL_STOPPED STOPPED: an open issue carries factory:stop
```

`FactoryStopped` is a `RuntimeError`, `build_issue`'s last handler is `except Exception`,
and that handler is `_mark_issue_human`: #49 lost `factory:accepted`, gained
`factory:needs-human`, and was told "Dark Factory stopped this run without merging.
builder failed closed: STOPPED: an open issue carries factory:stop". The operator who
pressed the button then had to un-escalate the issue by hand before the factory would look
at it again. A stop is an operator's action on the whole factory; the issue did nothing.

**Decision.** A proof refusal carries its evidence, and a stop hands the issue back.

- **Every RED and GREEN refusal of a checkpoint says what ran and what it printed.**
  `factory_proof.run` returns `(rc, output, seconds, fault)`; a command that cannot be
  started reports `fault='launch'` and one killed at `CHECKPOINT_TIMEOUT_SECONDS` reports
  `fault='timeout'` (both `rc=None`), and both are refused whatever the output says, as the
  traceback used to refuse them. `refuse` writes `$ARTIFACTS_DIR/red-proof-failure.json`
  or `green-proof-failure.json` (`acceptance_id`, `argv`, `cwd`, `rc`, `fault`, `seconds`,
  `expected_failure`, `reason`, `stage`, `output_tail`) and then dies with the same on
  stderr: the reason line as before, then argv, cwd, rc, seconds, `expected_failure`, and
  the last `OUTPUT_TAIL_LINES = 80` lines of combined output capped at
  `OUTPUT_TAIL_CHARS = 6000`. The verdicts are unchanged: an unexpected pass, a wrong
  reason, a GREEN exit other than zero refuse exactly as they did. A proved checkpoint
  records `red_seconds` (RED) or `seconds` (GREEN) beside its exit code and hash, prints
  `RED_CHECKPOINT`/`GREEN_CHECKPOINT` lines as it goes, and the `RED_PROVED`/`GREEN_PROVED`
  line ends `seconds=<sum>`.
- **The kernel quotes the record.** `build_issue`'s two failure handlers pass
  `_proof_failure_evidence(paths, exc)` to `_mark_issue_human`: for a `ToolRefused` from
  `factory_proof.py red` or `green` whose record exists, the needs-human comment gains the
  stage, the checkpoint's argv, cwd, rc, seconds and `expected_failure`, and the record's
  output tail scrubbed of every secret shape the guard knows and capped at
  `PROOF_FAILURE_COMMENT_CHARS = 3000`. Any other refusal, and a proof refusal raised before
  any checkpoint ran (`worktree must be clean`, an invalid spec), adds nothing.
- **A stop between stages returns the issue to `factory:accepted`.** `build_issue` catches
  `FactoryStopped` before `NeedsHuman` and `Exception` and calls `_release_stopped_build`:
  `factory:in-progress` is removed, `factory:accepted` is left (and re-added, so the
  invariant holds whatever else happened), the lease is finished with stage `stopped` when
  the build got as far as taking one, and the issue is commented with the stop issue
  numbers parsed from the stop check's output (`  #120 <title>` lines), the output itself
  quoted, and, if a PR had been opened, that PR's number with the statement that it is left
  as it is. No `factory:needs-human`, and no validation-failed marker, so
  `_next_build_attempt` does not count it. The stop propagates unchanged to the CLI, which
  still prints `KERNEL_STOPPED` and exits 0. Every stop check in the build path runs inside
  `_agent`, before the push, so a stopped build leaves no PR today; the docstring says what
  happens if one ever fires later. Validation, re-head and resume are untouched: a stop
  during validation still refuses the PR as before.

Pinned by `tests/factory/test_factory_red_evidence_and_stop.py`: a wrong-reason refusal
carries argv, cwd, rc, seconds and the checkpoint's output on stderr and writes the record;
a missing command is refused with the launch error as evidence, and is never RED even when
the error text matches the symptom; an unexpected pass carries the same evidence; a GREEN
refusal writes `green-proof-failure.json` with its stage; the tail is the last 80 lines
capped at 6000 characters; a proved checkpoint records its seconds and writes no record;
`red` and `green` run every checkpoint through the evidence path; the kernel's evidence
carries rc, argv, cwd, seconds and the tail, lands in the needs-human comment, reads the
green record for a green refusal, is capped and scrubbed, and is empty for a missing record
or another tool; both failure handlers hand it over. For the stop: `build_issue` driven to
its first model stage against a fake GitHub, with the stage raising `FactoryStopped` --
the issue returns to accepted, gains no needs-human, is commented once naming stop issue
#120 and quoting the check output, has no validation-failed marker and is still attempt 1,
pushes nothing, opens no PR, finishes a lease it took and touches none it did not; a
kill-file stop with no issue number is still not a failure; a `NeedsHuman` and a generic
exception still escalate exactly as before; the stop handler precedes the failure
handlers. Updated: `test_factory_lease_authority.py` and
`test_factory_attached_round_trip.py` unpack `run`'s four values. Mutations
`proof-refusal-without-output-tail`, `proof-failure-json-not-written`,
`stop-still-labels-needs-human` and `stop-comment-missing-the-stop-issue` are registered
in `harness/factory_mutations/defects.json`, the detector file is in the runner's copy
list, and each was verified by direct injection on the maintainer's Windows host (the copy
built by `run.py`, one defect injected, the detector file run).

**Observed and left alone.** What actually failed in run 33997386843's RED gate is still
unknown; the next refusal will say. The 1375-second, 33-turn `test_author` of that run is
the D-055 question, not this one. A stop during validation, re-head or resume keeps its
current handling.

**Consequences.** The next refused RED or GREEN gate leaves a record a human can act on
without re-running the build, and the needs-human comment quotes it. Pressing the stop
button no longer costs the stopped issue its place in the queue.

---

## D-057 · A builder reads only its product tree, and must draft before its turns run out, because four builds of issue #103 died in `test_author` and the last read the kernel for 31 turns

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34002520477 (the fourth build of issue #103), 33997386843 (its third), 33999901008 (the build of issue #49, for contrast)

The fourth build's stage line, printed live:

```
FACTORY_STAGE kind=agent name=test_author seconds=1925.537 turns=31 cost_usd=4.535674 outcome=failed events=69124 over_budget=true thinking=102959 effort=medium
```

Subtype `error_max_turns`. D-055's full transcript says what the 31 turns were: **46 Read tool
calls and zero Write or Edit calls.** The worker read far outside its task (kernel source, the
harness, biome and tsconfig, `authApi`) and at one point set out to "verify the kernel's
deferred-repro check", because the prompt had told it "the kernel refuses the build after RED
otherwise" and it went to read how. The previous build (33997386843) wrote its test file at
turn ~30 of 33 and its RED was refused. Issue #49's test author (33999901008), the healthy
case, made 11 Reads and 3 Edits in 15 turns, 618 s, $1.28, RED proved, first write at turn ~5.
Two facts follow.

**(a) Nothing bounded what a worker could read.** The prompt said "relevant source/tests and
repository guidance", which is unbounded, and the tool policy said `Read, Glob, Grep, Write,
Edit` with no path. The builder's worktree is a sparse checkout without the holdout scenarios
(`BUILDER_BLIND_PATHS`, D-048), and everything else in the trust root was on disk and readable:
the kernel, the harness, the workflows, the prompts of every other role. A worker that can read
its judge will, given 30 turns, read its judge.

**(b) Nothing ended a worker that was not drafting.** Turns (D-020), dollars (D-025), the wall
and the idle clock (D-054) and effort (D-055) each bound a different axis, and a worker that
reads one file per turn sits inside all five until its cap. The stage that most needed
stopping at turn 15 was stopped at turn 31 by the cap, and the cap is a clean failed stage,
not a diagnosis: the record said `error_max_turns`, not "read 46 files and wrote none".

**Decision.** Every tool-bearing worker runs inside a stated file boundary, and a mutation
worker that has not drafted by six tenths of its turns is stopped and refused by name.

- **The read scope is policy data.** `worker_policy.ROLE_PATH_SCOPE` gives every role a
  `PathScope` (`agents.PathScope`: `read`, `write`, `deny`, gitignore-style patterns relative
  to the worktree root). The mutation roles (`test_author`, `implement`, `repair`) read
  `app/**`, `docs/**`, `README.md`, `CLAUDE.md`, `MISSION.md`, `FACTORY_RULES.md` and write
  `app/**`, `docs/**`, `README.md`; the drafting roles (`plan`, `investigate`, `contract`,
  `context`, `review-spec`) read the same and write nothing in the tree; `architecture`,
  `conformance` and `review-standards` additionally read `.factory/architecture.json`, the
  one documented exception, because each of their prompts opens with it; every one of them
  is denied `factory_kernel/**`, `harness/**`, `scripts/**`, `tests/factory/**`, `.github/**`
  and the protected `.factory/` files (`kernel.json`, `evidence-spine.json`, `decisions.md`,
  `locks/**`, `prompts/**`, `methods/**`, `holdout/**`, `benchmark/**`); the judges and triage
  have no tools and an empty scope. Every `_agent` path (the base runtime's and the worker
  runtime's) passes `path_scope(role)` on the request, and the `_agent_stage` funnel refuses
  a repository-mutation request that arrives without one, before any process starts.
- **The provider renders it as permission rules, and the rules are stated from the CLI's
  documentation.** `ClaudeCliProvider.argv_for` (the argv rendering, factored out of `run` so
  the preflight can prove the same command line) renders a scoped request's `--allowedTools`
  as `Read(./<pattern>)` for each read pattern, `Edit(./<pattern>)` for each write pattern, and
  `Read(//<artifacts>/**)`/`Edit(//<artifacts>/**)` for the run's artifacts directory; and
  `--disallowedTools` as `Read(./<pattern>)` and `Edit(./<pattern>)` for each deny pattern. No
  bare tool name goes into the allow list: a bare `Read` would match every call and make the
  paths moot. Only `Read` and `Edit` rules are rendered because those are the two the CLI
  consults for file permissions (its documentation: `Edit` rules apply to every built-in tool
  that edits files, `Read` rules to Grep and Glob, and a `Write(...)`, `Glob(...)` or
  `MultiEdit(...)` path rule is accepted and never consulted). The same documentation says
  what each list is for: a file inside the working directory or an `--add-dir` directory is
  readable without any rule, so the **deny list is the read boundary** (and a `Read` deny also
  blocks Edit/Write on the path, v2.1.228+; the workflow pins 2.1.245); a write inside the
  working directory needs an allow rule under `dontAsk`, so the **`Edit` allow list is the
  write boundary**; the `Read` allow rules grant nothing the working directory did not
  already, and are rendered for completeness. Deny is evaluated before allow, which is why
  `.factory/architecture.json` is absent from the deny list rather than allowed over it. A
  `./` prefix is the "relative to the current directory" form; the first rendering stripped
  leading dots with `lstrip("./")` and would have denied `github/**` and `factory/**`, which
  the detector caught before the change was ever run.
- **The enforcement is proved on the runner, not assumed.** The local probe the change asked
  for could not run: the maintainer's shell has no `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`
  and `--bare` skips keychain reads, so the installed 2.1.259 answered `Not logged in`. The
  worker workflow's preflight therefore runs `scripts/factory_read_scope_probe.py` after the
  effort probe: a throwaway tree with `app/probe.txt`, `factory_kernel/probe.txt` and an
  artifacts `note.txt`, each holding a sentinel, and the exact argv the kernel renders for a
  `test_author` (its tools, scope and effort; six turns, one dollar), asking the worker to
  read all three. It prints `FACTORY_PREFLIGHT_READ_SCOPE_PROBE model=<slug>
  denied_outside_scope=true|false attempted_outside_scope=... read_inside_scope=...
  read_artifacts=... events=<n> [error=<what>]`, exits 0 for a denial and for an inconclusive
  run (the worker never tried, the route failed), and exits 2 only when the trust-root
  sentinel came back through the stream; the workflow refuses the run on that code alone,
  because a worker that can read its judge is the defect the scope removes, and prints the
  line on any other outcome.
- **The draft deadline.** `worker_policy.DRAFT_DEADLINE_FRACTION = 0.6` and
  `draft_deadline_turn(role, cap) = ceil(cap × 0.6)`, turn 18 of 30, for the three mutation
  roles and `None` for every other. The data it is stated from: #49 wrote at turn ~5 of 15;
  #103's third run wrote at ~30 of 33 and its fourth never; 18 is three times the healthy
  draft turn and well before the cap the two dead builds spent. The provider hands
  `_stream_cli` a `DraftWatch` beside the wall and idle clocks; the reader shows it every
  parsed event; it counts turns as distinct `assistant` `message.id`s, exactly as
  `ResultEnvelope.from_events` counts a killed process's `num_turns` (D-054), notes the first
  Write/Edit/MultiEdit/NotebookEdit tool_use as the draft, and counts `Read` tool_use calls
  with their `file_path`s. The moment a turn past the deadline begins with no draft seen, the
  reader kills the process and `_launch` raises `DraftDeadlineMissed` (a `ProviderStageError`,
  so the retry loop refuses it as terminal: not transient, not retried, no worktree restore)
  with telemetry `subtype=no_draft_by_turn`, `draft_deadline_missed`, `draft_deadline_turn`,
  `reads`, `files_read` (capped at `FILES_READ_CAP = 40`) beside the partial envelope. The
  record carries them, the timing row and the stage line say `draft_deadline_missed=true
  reads=N`, and `build_issue`'s failure handlers now pass `_failure_evidence` (the D-056 proof
  record plus `_draft_deadline_evidence`) to the needs-human comment, which names the reads,
  the deadline and the paths.
- **The prompts state both bounds.** `test-author.md`, `implement.md` and `repair.md` each say
  "You can read only the product tree under app/ and your run's artifacts; the kernel's own
  code, harness and workflows are not readable and not your concern" and "Write your first
  draft ... by turn $DRAFT_DEADLINE_TURN; the kernel ends the stage if nothing is written by
  then". `$DRAFT_DEADLINE_TURN` is a kernel placeholder (`prompt_render.KERNEL_PLACEHOLDERS`),
  rendered by the worker runtime from `draft_deadline_turn(role)` the way `$ARTIFACTS_DIR` is
  rendered from the environment, and refused in any other role's prompt. The test author's
  "relevant source/tests" became "the source and test files under `app/` that they name", and
  "the kernel refuses the build after RED otherwise" became "that is a requirement of the RED
  gate, so put the symptom in the assertion text the runner prints"; every other constraint
  is verbatim. No method file repeats the reading guidance (`tdd.md` is about the loop).

Pinned by `tests/factory/test_factory_read_scope_and_draft_deadline.py`: the table is complete
for every role, mutation roles write the product tree and never governance, every tool-bearing
role is denied every trust root, drafting roles write nothing in the tree, judges are empty,
the architecture policy is readable to exactly the three roles and matched by no deny pattern,
a pattern must be relative; a mutation role's argv carries `Read(./app/**)`, `Edit(./app/**)`,
the absolute artifacts rules and no bare tool, its deny list every root, a drafting role no
`Edit(./app/**)`, only `Read`/`Edit` rules are rendered, an unscoped request renders bare names
as before, a judge renders nothing, and `run` launches exactly `argv_for`; the funnel refuses a
scopeless request for each mutation role (no provider call, no record) and runs a scoped one;
both `_agent` paths carry the scope, the worker path renders `$DRAFT_DEADLINE_TURN` as 18 and
refuses it for a drafting role; the checked-in prompts carry the sentences and no other prompt
names the deadline; the fraction, the turn per role, the roles without one, the request's own
cap; the watch counts distinct ids, disarms on a write, only counts without a deadline; through
the fake CLI a worker that only reads is killed at turn 19 in one launch with no restore and
the telemetry above, a worker that wrote at turn two is untouched, a drafting role has no
deadline, the deadline follows the request's cap, the paths are capped and the count is not, the
record, row, line and log say so, a healthy session records no deadline fields; the stage-line
shape; the comment evidence, its cap and scrub, the combined evidence, and `build_issue` driven
to the miss against the fake GitHub; the probe's argv, tree, measurements, line, exit codes,
`main`, and its place in the workflow. Updated: `test_factory_stream_timeouts.py` and
`test_factory_effort_and_stream_logs.py` (a scoped mutation request through the funnel),
`test_factory_prompt_paths.py` (the kernel placeholder), `test_factory_red_evidence_and_stop.py`
(`_failure_evidence`). Mutations `read-scope-dropped-for-test-author`,
`read-scope-lets-factory-kernel-through`, `draft-deadline-never-fires`,
`draft-deadline-fires-on-a-run-that-wrote` and `draft-deadline-counted-as-transient` are
registered in `harness/factory_mutations/defects.json`; the detector, the probe and
`implement.md` are in the runner's copy list; each was verified by direct injection on the
maintainer's Windows host (the copy built by `run.py`, one defect injected, the detector run).

**Observed and left alone.** `deploy/` is neither allowed nor denied: not writable by a scoped
worker, so a deployment issue (CLAUDE.md's `deploy/Caddyfile` exception) needs a scope change
through the human lane; that is the right lane for it. The absolute-pattern form for a Windows
path (`//C:/...`) is unverified; the runner is Linux and the probe proves the runner. A
returned stage does not yet record its reads; the deadline miss does. The reader leaves a
killed process's pipes to the garbage collector as it did for a hang. The first scoped build
is the evidence for tuning the fraction.

**Consequences.** A builder can no longer read the machinery that judges it, and a builder
that is not drafting by turn 18 costs at most eighteen turns before the stage is refused with
the list of what it read, instead of thirty-one turns and $4.54 before a cap that says
nothing. The preflight makes one more bounded model call per run, and refuses the run if the
CLI ever stops honouring the rules the boundary is made of.

---

## D-058 · A checkpoint can guard kept behaviour, and every run of a stage keeps its record, because run 34008561672 refused a correct test and lost a 2687-second one

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34008561672 (the fifth build of issue #103, the first with D-057's read scope and draft deadline)

The build got further than any before it: `investigate`, `contract`, `context` and
`architecture` returned, `test_author` wrote its file, the static gate handed a biome
formatting failure back once (D-043), the second `test_author` fixed it in 473 s, the kernel
committed, and RED ran:

```
RED_CHECKPOINT AC-1 rc=1 seconds=1.955
RED_CHECKPOINT AC-2 rc=1 seconds=2.883
PROOF_FAIL: AC-3 RED command unexpectedly passed
  argv: ["bunx", "vitest", "run", "src/components/ChatArea.test.tsx", "-t", "AC-3"]
  rc: 0
  expected_failure: 'switching conversations must abort the in-flight stream'
```

D-056's evidence made the refusal readable, and what it shows is not a wrong test. Two
defects, one in the protocol and one in the record.

**(a) The proof protocol could not express kept behaviour.** The contract compiled AC-3 as
*given* "a stream is in flight for /c/a", *when* "the conversation id actually changes,
navigating from /c/a to /c/b mid-stream", *then* "the old stream's fetch is aborted and
streaming state resets ...; this kept behaviour is pinned by the existing 'conversationId
reset' tests, which must stay green". The issue itself says that behaviour "is kept and
tested", and the contract's `invariants` say it again. The test author read all of that,
wrote a test that exercises it, and declared the only checkpoint shape `test-spec.json` 2.0
has: `expected_failure` required, must fail at RED, must pass at GREEN. Its `notes` even say
AC-3 "is expected to pass on current main while remaining green after the fix; its
expected_failure fragment is the assertion message that fires if a fix overcorrects". The
RED gate refused, correctly, because a test of kept behaviour passes on the unchanged tree by
definition. The protocol had no way to say "this must pass before AND after", so an issue that
legitimately carries a kept-behaviour acceptance item could not be built, whatever any worker
did.

**(b) The static-gate hand-back overwrote the first run's record.** The run's transcripts hold
one `agent-test_author.json`: 473 s, 23 turns, `attempts=1`, the second run's. The stage-timings
file has two `test_author` rows, and the first says `seconds=2687.134 turns=41
cost_usd=4.657 over_budget=true`, against a 2025-second wall for the role. Nothing in the row
says how, and the record that would have is gone; the streamed log survived only because the
provider opens it for append, so it holds three `--- attempt N ---` headers, two of them
"attempt 1". Reading those headers answers the question the row could not: **the first
`test_author` was two CLI processes.** Attempt 1 ran 717.8 s and 14 turns, then the CLI
returned `is_error: true`, `terminal_reason: api_error`, `result: "API Error: stream closed
before completion"` with rc=1; the provider classified that as transient (D-031) and
relaunched. Attempt 2 started at 04:06:04, ran 1964.2 s and 27 turns, wrote the test file and
the spec, and returned. 717.8 + backoff + 1964.2 = 2687 s. `wall_seconds` is measured across
the whole stage, the per-process wall is per process, and neither attempt hung or timed out;
`hang=true` would have been wrong, `attempts=2` would have been right, and the record would
have said `attempts=2` had it not been overwritten. The timing row did not carry `attempts`
at all, so even the surviving evidence said nothing.

**Decision.** A checkpoint declares its kind, and every run of a stage keeps its own files.

- **`test-spec.json` version 2.1.** Each checkpoint carries `kind: "red" | "guard"`; absent
  means `red`, so a 2.0 spec reads exactly as before and a 2.1 spec with only red
  checkpoints has the identical outcome. A `red` checkpoint keeps today's semantics to the
  letter. A `guard` checkpoint has no `expected_failure` (one that declares it is refused as
  malformed, `guard checkpoint must not declare expected_failure`) and must exit 0 at RED and
  at every GREEN: `prove_guard` refuses `guard failed on the unchanged tree` at RED and
  `guard broken by the implementation` at GREEN and final GREEN, through the same `refuse`
  path D-056 built, so `red-proof-failure.json` / `green-proof-failure.json` carry the argv,
  cwd, rc, seconds, output tail and now `checkpoint_kind`. At least one checkpoint must be
  red (`a spec of only guards proves no change`), every AC still has exactly one checkpoint,
  and a `guard` in a 2.0 spec is refused (`guard checkpoints require test-spec version
  2.1`). The proof stays version 2.0: a kind-less checkpoint is a red one to every reader
  (`checkpoint_kind`), the proof carries `guards=N`, the `RED_PROVED`/`GREEN_PROVED` lines
  end `guards=N`, a guard's GREEN result says `kind: guard`, and the test plan carries
  `kind` for every checkpoint that has one and omits `expected_failure` for a guard
  (`PLAN_KEYS`, the same tuple and the same "leave out what the checkpoint lacks" rule in
  `factory_proof.plan_from` and `factory_evidence.plan_from_proof`, so a pre-guard proof's
  `test_plan_sha256` still reconstructs).
- **The evidence bundle judges a guard as a guard.** `validate_checkpoint` accepts a guard
  only with `red_exit == 0` and no `expected_failure` and a red checkpoint exactly as before;
  a proof of only guards is refused before any replay; `replay_red` and `replay_green` route
  a guard through `validate_guard_result` (exit 0 at both, `independent RED replay: guard
  failed on the unchanged tree` / `independent GREEN replay: guard failed at the head`), and
  the bundle counts `guards` beside `criteria`. The kernel's readers follow: the holdout's
  `proof_summary.red_results[]` carries `kind`, shows a guard with `red_exit: 0`,
  `expected_failure: null`, `matched: null` and its passing tail, and refuses a guard that
  recorded a failing exit or an expected failure; the re-head reconstructs its spec through
  `rehead_spec_from`, which carries `kind` and omits a guard's `expected_failure` (a
  pre-guard proof is reconstructed as the 2.0 spec it came from); the needs-human comment
  says `kind=guard` where it said `expected_failure=`; and `verify_deferred_in_red` is
  untouched, because a guard's tail is passing output and a deferred symptom must be shown by
  a failure.
- **The contract may say it.** A behaviour may carry `kind: "guard"` (`BEHAVIOR_KINDS`,
  default `behaviour`, never inserted, so a contract without it hashes as it always did),
  and the RED gate holds the author to it: a contract guard behaviour with a red checkpoint
  is refused before anything runs. The reverse is allowed, because a Then that says "kept"
  needs no contract key. ACs are counted and certified exactly as before; the contract
  certifier's question and the holdout prompt each gain one sentence saying a guard is
  verified kept behaviour, not an unverified or invented requirement.
- **The prompts state the rule.** `test-author.md` asks for version 2.1, defines both kinds,
  and says: an AC of kind `guard`, or whose Then says the behaviour is kept, preserved or
  must stay green, gets a `guard` checkpoint, never a `red` one; at least one must be red.
  `contract.md` says when a behaviour may carry `"kind": "guard"` and not to use it for the
  behaviour the issue asks to change.
- **Every run of a stage keeps its record.** `runtime.stage_record_name` picks
  `agent-<role>` for the first run of a role in a run directory and `agent-<role>.2`, `.3`,
  ... for each later one, the first stem with neither a `.json` nor a `.log` on disk; the
  funnel streams into `<stem>.log` and `_record_agent` / `_record_failed_agent` write
  `<stem>.json`, so the hand-back's second `test_author` no longer touches the first's
  files. The worker workflow's upload glob (`transcripts/agent-*.json|log`) already catches
  every suffix. Each record and each timing row says `record` and `stage_run`; the stage
  line prints `stage_run=N` after the name when N > 1.
- **A record says how many processes it took.** `AgentResult.hangs` counts the attempts the
  provider killed for silence before one returned; `_record_agent` writes `attempts`,
  `hangs` and `hang` (`hangs > 0`) into the record and the row, and the stage line prints
  `attempts=N` after the seconds when N > 1 and `hang=true` for a returned stage as it
  already did for a failed one. Run 34008561672's first `test_author` would now print
  `FACTORY_STAGE kind=agent name=test_author seconds=2687.134 attempts=2 turns=41 ...` and
  its record would survive as `agent-test_author.json` beside the hand-back's
  `agent-test_author.2.json`.

Pinned by `tests/factory/test_factory_guard_checkpoints.py`: a 2.0 spec is accepted and reads
as red; a 2.1 red-only spec has the same outcome as 2.0; a guard beside a red is accepted
and counted; a spec of only guards, a guard with `expected_failure`, a red without one, a
guard in a 2.0 spec, an unknown kind, an unknown version, a contract guard behaviour with a
red checkpoint, and a duplicate AC are each refused by name; a guard that passes on the
unchanged tree is proved at RED with `GUARD_CHECKPOINT`, one that fails is refused with the
D-056 evidence and `checkpoint_kind`, one that passes at GREEN is proved, one broken by the
implementation is refused at final GREEN with its record, a launch failure is refused, a red
checkpoint keeps its semantics; `red` then `green` on a real repository with one red and one
guard print `guards=1`, write a 2.0 proof with `guards`, a plan whose digest both programs
reconstruct, GREEN results labelled per kind, and a proof the evidence bundle accepts
checkpoint by checkpoint; the bundle's `validate_checkpoint`, only-guards refusal, guard
replay verdicts, replay routing and plan rule; the contract kind is accepted, an unknown one
refused, no default inserted, the keyed spelling carries it; the holdout summary, the re-head
spec (both shapes and its call site), the comment evidence; and the prompt sentences and
rules text. By `tests/factory/test_factory_stage_runs.py`: the stem for the first, second and
third run and a log-only first run; two runs through the funnel write both records and logs
with the first intact, two rows and two lines with `stage_run`, a failed second run beside a
returned first; the real static-gate hand-back keeps both runs; a hang then a healthy
process records `attempts=2`, `hang=true`, `hangs=1` in the record, row and line; a dropped
stream then a healthy process (run 34008561672's shape) records `attempts=2` and no hang; a
single-process stage reads as before; the provider's `hangs` count; the stage-line shape; and
the record helpers with and without a stem. Updated: `test_factory_validation_stage_telemetry.py`
(the line regex admits `stage_run` and `attempts`). Mutations `guard-failure-at-green-ignored`,
`guard-only-spec-accepted`, `guard-treated-as-red` and
`second-attempt-record-overwrites-the-first` are registered in
`harness/factory_mutations/defects.json`, both detector files are in the runner's copy list,
and each was verified by direct injection on the maintainer's Windows host (the copy built
by `run.py`, one defect injected, the two detector files run).

**Observed and left alone.** The transient retry that made the first `test_author` two
processes is D-031 working as designed; whether a 717-second attempt that ends in `stream
closed before completion` should be retried at all, given that the relaunch cost 1964 s and
$3.58 more, is a budget question this decision only makes visible. The 41 turns and $4.66 of
that stage are the D-055 effort question. The rehearsal fakes (`harness/rehearsal.py`) still
build kind-less 2.0 specs and proofs, which is the backward-compatible shape and needs no
change. The local factory suite runs green on the maintainer's Windows host once
`FACTORY_WORKDIR` names an absolute path; `kernel.json`'s `work_root` is a runner path.

**Consequences.** An issue that says "this behaviour is kept and tested" can be built: the
contract can say so, the test author can guard it, and RED proves the guard green instead of
refusing the build for a test that did what it should. A stage the kernel runs twice leaves
two records, and a record that outlived its wall says why.

---

## D-059 · The route is probed for a thinking budget, and a worker can carry one per role, because six builds of issue #103 died in `test_author` at an effort the route does not honour

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 33987381035, 33992451400, 33997386843, 34002520477, 34008561672, 34013852733 (the six builds of issue #103)

Six builds of the same three-criterion design, on `z-ai/glm-5.3-flash` over OpenRouter's
Anthropic-compatible route (`ANTHROPIC_BASE_URL=https://openrouter.ai/api`), and the
`test_author` line of each:

```
33987381035  seconds=1200.121 turns=0  outcome=failed                                        (no telemetry, D-054)
33992451400  seconds=2025.084 turns=14 outcome=failed events=76248 timed_out=true            (D-055)
33997386843  seconds=1375.701 turns=33 outcome=ok     events=63414 thinking=92984  effort=medium  (RED refused, D-057)
34002520477  seconds=1925.537 turns=31 outcome=failed events=69124 thinking=102959 effort=medium  (no draft, D-057)
34008561672  seconds=2687.134 turns=41 outcome=ok     events=94909 thinking=141172 effort=medium  (two processes, D-058)
34013852733  seconds=2025.142 turns=15 outcome=failed events=81231 thinking=121065 effort=medium timed_out=true
```

The sixth is 135 s per turn, 5,415 events per turn, and its last turn alone thought about
22,500 tokens, while the four stages before it in the same run took 15-46 s per turn
(`investigate` 847 s / 27, `contract` 194 s / 6, `context` 576 s / 35, `architecture`
332 s / 14). D-055 put every worker at `--effort medium` from the third build on, and the
numbers did not move: the three builds at the CLI's default and the three at `medium` are
the same shape. The preflight measured why. `FACTORY_PREFLIGHT_EFFORT_PROBE` on the same
route, same prompt, same model, run to run:

```
33997386843  low_thinking=4256 high_thinking=3352 honoured=false
34002520477  low_thinking=2883 high_thinking=6360 honoured=true
34008561672  low_thinking=2051 high_thinking=3550 honoured=true
34013852733  low_thinking=4401 high_thinking=2460 honoured=false
```

`medium` out-thought `high` twice in four. The CLI documents effort as adaptive and
calibrated per model; on this route to this model it is not a bound, and a level the route
does not honour cannot be tuned into one. Turns, dollars, the wall and the draft deadline
(D-025, D-054, D-057) each bound something else; none bounds how long one turn thinks.

**The lever not yet tried.** The installed CLI (2.1.259) reads `MAX_THINKING_TOKENS` from
its environment as an integer. Above zero it sends every request with `thinking: {type:
"enabled", budget_tokens: N}`, raising N to 1024 if it was lower and holding it below the
response's max_tokens; exactly zero sends `thinking: {type: "disabled"}`; unset leaves
thinking adaptive, which is what `--effort` steers. There is no flag for it (`claude --help`
lists only `--effort`), and the CLI's own error text for a level that needs thinking says
`unset MAX_THINKING_TOKENS=0`. It is a stated budget rather than a calibrated level, so
whether the route passes it through is a different question from whether it honours a
level, and it is asked the same way D-055 asked its question: measured before it is used.

**Decision.** The preflight probes the budget, and the worker environment can carry one per
role; no row carries one until the probe says the route honours it.

- **The probe.** `scripts/factory_thinking_cap_probe.py`, run by the worker workflow right
  after the effort probe: the worker model three times on the effort probe's fixed
  reasoning prompt, one turn and one dollar each, at the mutation roles' level (`medium`),
  uncapped, with `MAX_THINKING_TOKENS=1024` (the smallest budget the CLI sends as given) and
  with `MAX_THINKING_TOKENS=0` (thinking disabled), counting the thinking each stream showed
  with the estimator the stage records use. It prints `FACTORY_PREFLIGHT_THINKING_CAP_PROBE
  model=<slug> uncapped=<n> cap1024=<m> cap0=<k> honoured=true|false
  cap1024_honoured=true|false cap0_honoured=true|false effort=<level> uncapped_events=<n>
  cap1024_events=<n> cap0_events=<n> [error=<what>]` and exits 0 whatever it found. A cap is
  honoured when its run returned, thought no more than 1.5× the cap (at most 1536 for the
  budget, nothing at all with thinking disabled) and clearly less than the uncapped run, by
  the effort probe's own margins (at least 1.5× less and at least 100 tokens fewer);
  `honoured` is both caps at once, and the two are printed separately because a route may
  respect a budget and refuse a disabled-thinking request (the CLI's error path above says
  some models do). The uncapped run gets the runner's environment with the variable removed,
  never inherited. The workflow prints a `honoured=false error=probe-did-not-run` line if the
  script does not run; nothing here refuses a run.
- **The policy row.** `worker_policy.THINKING_CAP_ENV = "MAX_THINKING_TOKENS"`,
  `THINKING_CAP_MIN_BUDGET = 1024`, `THINKING_CAP_DISABLED = 0`, and `ROLE_THINKING_CAP`, a
  table beside `ROLE_EFFORT` with a row for every role, every row `None`. `thinking_cap(role)`
  reads it; `check_thinking_cap` refuses a bool, a string, a negative number and a positive
  budget below 1024, which the CLI would silently raise. The provider's `environment_for`
  (the environment counterpart of `argv_for`) is what every process is launched with: the
  filtered worker environment, plus the variable only when the role's cap is not `None`. The
  runner's own value of the variable never reaches a worker: it is neither a provider
  credential nor on the exact list, and the request-local environment is whitelisted, so
  the policy table and the configured override are the only two ways in.
  `credential_env.scoped_environment` passes it through unchanged; it is not a credential.
- **The override.** `provider.thinking_cap_overrides` in `kernel.json`, `{role: cap}`,
  validated at load against the policy's roles and `check_thinking_cap`, applied by the
  provider over the table exactly as `effort_overrides` is. The checked-in table is empty.
  Setting a row in either place is the next decision, taken from this probe's line and
  not before; the table's comment says so.

Pinned by `tests/factory/test_factory_thinking_cap.py`: the variable's name and the two
values; a cap the CLI would honour as given and the refused shapes; a row for every role,
every row `None`, an unknown role refused, a set row checked when read; no cap exports
nothing, the runner's own value never reaches a worker, a configured override is exported
as the variable (1024, 0, 8192), an override for another role changes nothing, a set policy
row is exported and the override wins over it, the request environment cannot smuggle it,
the launched process gets exactly `environment_for`'s result; the checked-in override table
is empty, absent means none, a table is parsed, a cap below 1024 or of the wrong type is
refused, an unknown role and a non-object are refused, the message names the key;
`scoped_environment` passes the variable through; the probe's three runs and their fields,
the uncapped run inherits nothing, the argv is the effort probe's at the builders' level,
`within_cap`, `clearly_below`, `cap_honoured` and `honoured` case by case, the line when the
route honours both caps, ignores the variable, honours the budget but not disabling, when a
process did not start and when one timed out, one run per cap; and the workflow step's
place, script, credential, fallback line and absence of any refusal. Mutations
`thinking-cap-set-but-not-exported` and `thinking-cap-honoured-rule-inverted` are registered
in `harness/factory_mutations/defects.json`, the script and the detector are in the runner's
copy list, and each was verified by direct injection on the maintainer's Windows host (the
copy built by `run.py`, one defect injected, the detector run).

**Observed and left alone.** `budget_tokens` is a request field on Anthropic's API; whether
OpenRouter maps it onto GLM's own reasoning control, drops it, or errors, is exactly what the
line will say, and the decision to set a row waits on it. The stage record does not yet say
what cap a stage ran under; when a row is set, `thinking_cap=<n>` belongs beside `effort=`
on the stage line and in the record, and that change goes with the row. The workflow's pin
(2.1.245) is fourteen patch releases behind the binary the variable was read from; the
variable predates both by a long way, and the probe's line will show it either way.

**Consequences.** The next worker run prints one line saying whether a thinking budget is a
lever on this route. If it is, one row in `ROLE_THINKING_CAP` bounds what six builds could
not; if it is not, the factory knows the CLI's last thinking control is closed to it here
and the remaining question is the route or the model, not the level.

---

## D-060 · A guard behaviour keeps its `AC-N` id, because the first build after D-058 wrote `AC-G1` and the compiler refused it without naming the rule

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34015187797 (issue #49, the first build after D-058)

The contract worker read D-058's guard wording and numbered its three guards `AC-G1..AC-G3`; the compiler refused `invalid/duplicate behavior id AC-G1`, correctly, and the build ended at needs-human with a message that named neither the rule nor the fact that the behaviour was otherwise sound. `kind` is a field on a behaviour and never changes its id: both prompts now say so, `contract.md` with an ordinary behaviour and a guard side by side, the refusal names the rule (`behavior ids must be AC-1..AC-N in order; got 'AC-G1'`) and says when the id is the behaviour's only fault, and the compiler still does not renumber, because the certifiers hash the contract as the worker wrote it. Pinned by `tests/factory/test_factory_guard_behavior_ids.py` (the run's contract is a fixture: refused as written, compiled once its guards are `AC-5..AC-7`); mutation `contract-guard-id-scheme-accepted` in `harness/factory_mutations/defects.json`.

---

## D-061 · A role can run on its own model, because six builds of issue #103 died in `test_author` on a route that bounds nothing else

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 33987381035, 33992451400, 33997386843, 34002520477, 34008561672, 34013852733 (the six builds of issue #103)

D-059 tabulated the six `test_author` lines: 1200 to 2687 s, 14 to 41 turns, 63,414 to
94,909 events and 92,984 to 141,172 thinking tokens, where every other stage of the same
builds ran at 15-46 s per turn. Three of the six ran at the CLI's default effort and three
at `medium` (D-055), and the two sets are the same shape; the effort probe read
honoured=false, true, true, false on the route from run to run. D-059's answer was the CLI's
last thinking control, `MAX_THINKING_TOKENS`, measured before use, and the thinking-cap
probe read `honoured=false` on this route as well. `--effort` and the budget are the two
levers the CLI has for how long one turn thinks, and OpenRouter's Anthropic-compatible route
to `z-ai/glm-5.3-flash` honours neither for it. Turns, dollars, the wall and the draft
deadline (D-025, D-054, D-057) each bound something else. What is left is which model the
role runs on.

The kernel had exactly one per-role model. `provider.architecture_model` sends the
architecture holdout to a different model family (`deepseek/deepseek-v4-pro-0813`), and
`ClaudeCliProvider.model_for` chose it by naming the role in code: `architecture_model` if
the role was `architecture-holdout`, else the request's model, else `provider.model`. Every
request the kernel built carried `model=self.config.provider.model` (four sites in
`runtime.py`, the triage worker, the worker runtime), so "the request's model" was never a
choice, only the default restated, and the architecture rule had to beat it to mean
anything. Routing `test_author` elsewhere would have meant a second role named in the
provider, and a third role a third.

**Decision.** The model per role is a configuration line, resolved in one stated order,
recorded where the stage's telemetry is read, and proved reachable before any stage runs.

- **The table.** `provider.model_overrides` in `kernel.json`, `{role: model_slug}`,
  validated at load by `worker_policy.validate_model_overrides` against the roles the
  policy knows (`ROLE_MAX_TURNS`) and a non-empty slug, exactly as `effort_overrides` and
  `thinking_cap_overrides` are; a typo refuses the configuration. The checked-in value is
  `{}`. The owner chooses the model; this decision makes the choice one line.
- **The order.** `model_for` resolves every request the same way: the request's own model
  if it names one; else `model_overrides[role]`; else `architecture_model` for the
  architecture holdout; else `model`. The kernel's own requests no longer name a model
  (`runtime.py`, `triage.py` and `worker_runtime.py` pass none), so for a stage the
  resolution is the whole story and an override cannot be beaten by a default stamped
  upstream; the preflight probes, which must make the request a role makes against the
  model under test, still name theirs. The architecture rule is unchanged in effect: with
  no override the holdout runs on `architecture_model` as before, and an override for
  `architecture-holdout` is the one way to move it.
- **The record.** The stage record (`agent-<role>.json`) and the timing row already carried
  `model` (D-050); the `FACTORY_STAGE` line did not. It now ends `model=<slug>`, after
  `effort=`, and a stage that raised before it had a result is recorded with the model the
  provider had resolved for it (`_resolved_model`, asked of the provider before the launch)
  rather than the request's `None`. A rehearsal provider without a resolver records the
  request's model, else the configured worker model, which is what every kernel request
  said before.
- **The probe.** The worker workflow's route probe listed two models with an inline
  one-liner (`provider.model`, `provider.architecture_model`); a role routed to a third
  would have found its route closed mid-build, after the stages before it had spent their
  budgets. `scripts/factory_models.py --list` prints every distinct model a run can use, the
  worker model, the architecture holdout's and every override value, each once, validated
  as the kernel validates them; the step reads the list, refuses an empty one, runs the
  pinned CLI once against each model, prints `FACTORY_PREFLIGHT_MODEL_ROUTE_OK model=<slug>`
  per model and refuses the run if any is unreachable, exactly as it did for the two.
  `--role <role>` prints the one model a role resolves to, for the effort and thinking-cap
  probes to reuse when they are pointed at a role rather than at the worker model.

Pinned by `tests/factory/test_factory_model_overrides.py`: the checked-in table is empty and
absent means none; a table is parsed and its slugs stripped; every role the policy knows is
accepted; an unknown role, an empty slug, a non-string slug and a non-object table are
refused and the message names the key; the dataclass default; the default is the worker
model, an override wins over it for its role only, the architecture holdout keeps its own
model without an override and an override wins over that rule, the request's own model wins
over everything, the launched argv and the result name the resolved model, and the kernel's
own requests name none; a returned stage and a timed-out one record and print the resolved
model, a provider without a resolver records the request's model or the worker model, and
the line shape; the script
lists the two checked-in models, every override value once after them, nothing for an
override equal to the worker model, the worker alone without an architecture model, refuses
a table the kernel would refuse (and its exit line), prints one slug per line, agrees with
`model_for` for every role with and without overrides, bootstraps from beside itself and
runs from outside the repository; and the workflow step reads the list before the loop,
refuses an empty list, keeps its refusal, and no longer carries the inline two-model list.
`tests/factory/test_factory_runtime.py`'s architecture-holdout test now makes the request
the kernel makes (no model named) and still expects the holdout's own model. Mutations
`model-override-ignored-by-run`, `model-override-unknown-role-accepted` and
`model-route-probe-skips-override-models` are registered in
`harness/factory_mutations/defects.json`, the script and the detector are in the runner's
copy list, and each was verified by direct injection on the maintainer's Windows host (the
copy built by `run.py`, one defect injected, the detector run).

**Observed and left alone.** Which model `test_author` should run on is the owner's call and
is not made here; the checked-in table is empty and the next build runs exactly as the last
did. The probe costs one one-word CLI call per distinct model, so a table that names three
models costs three: the price of proving a route before a build rather than during one. A
role's effort and thinking-cap rows are keyed by role, not by model, so a role moved to a
model with a different scale carries the same level name; the effort and thinking-cap probes
still measure the worker model, and pointing them at an overridden role (`--role`) is the
next step once a row is set. The `model=` field is appended after `effort=` so every
existing reader of the line, which anchors on the fields before it, reads as before.

**Consequences.** The owner can route `test_author`, or any role, to another model with one
line in `kernel.json`; the next run's preflight proves that model reachable before any stage
runs, the stage line says which model each stage ran on, and the question D-059 left open,
whether it is the route or the model that cannot be bounded, is answered by changing one and
reading the line.

## D-062 · The journey asks about the locked video by its title, and the probe demands a citation, because the model answered "the video I just added" in one round with no search

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34020965800 (main regression, 2026-09-06 08:07Z), 33963509318

With D-053's pgvector database in place, the main regression got the fixture ingested
(`E2E_BOOTSTRAP_SEEN`, `E2E_VIDEOS_PROBE count=1 fixture_present=true`) and still failed at
the citation. The app log named the cause twice, once for the harness-side probe and once
for the browser's own request: `stream_chat round=1 finish_reason=stop tool_calls_made=0`.
The chat model answered in a single round and never called a retrieval tool, so the route
emitted tokens and `[DONE]` and no `sources` event (`E2E_STREAM_PROBE ... sources=false`),
and the browser journey had no citation to find. The locked question was "Based on the
video I just added, summarize its main topic and cite the source." An earlier run's answer
to it began "I don't have visibility into which video you just added": the wording invites
the model to explain that it cannot see uploads, and whether it searches anyway is luck.
Run 33963509318 called tools for three rounds on the same question; run 34020965800 called
none. A gate whose pass depends on a model's mood is not a gate. D-051's stream probe saw
all of this and passed, because it required 200, a token and no error payload, and the
citation was left to the browser to miss.

**Decision.** The question names the video the run ingested, by the title the catalog
lists, and the stream probe refuses an answer without a citation of that video before any
browser opens.

- **The question.** `browser.question_template` in `harness/harness.config.json`, checked
  in as `Summarize the main topic of the video titled "{title}" and cite the source.`, is
  rendered by `harness/e2e.py` (`_journey_question`) with the fixture's `{title}` (and
  `{description}`) exactly as `GET /api/videos` returned them to the videos probe, which
  now hands back the fixture's catalog row. The one rendered question goes to the stream
  probe and to the browser. It is printed as `E2E_QUESTION title="<title>"
  question="<rendered>"`. A row whose title is empty or missing cannot ground anything:
  `E2E_FIXTURE_TITLE_MISSING` is printed and the journey refuses before the stream probe
  spends a message. A template that never names `{title}` is refused at configuration.
  The literal `browser.question` is honoured only when `question_template` is absent, so
  a configuration from before this decision still runs; the checked-in configuration no
  longer carries it.
- **The probe.** `_probe_stream` passes only on 200, at least one token, no error payload
  and a `sources` event carrying a non-empty array whose first entry is the locked fixture:
  its `video_url` holds the locked YouTube id (the field that is the same on every run), or
  its `video_id` is the catalog row's id (fresh per bootstrap, compared with the row the
  videos probe returned). The line gains `reason=<why or ->` after `sources=`, then
  `sources_count=<n> fixture_in_sources=<bool> tool_calls=<n>`, where `tool_calls` counts
  the `event: status` frames of type `tool_call_start` that `llm/openrouter.py` emits per
  executed tool call; a stream that never searched now says `sources=false
  reason=no-sources-event ... tool_calls=0` and stops the run there, as the other probes do.
  Rounds are not on the wire and are not reported.
- **The page.** agent-browser escapes the quotes inside a node's text; the rendered
  question quotes the title, so snapshot names and text nodes are read unescaped, or the
  page's echo of the question would count as fresh assistant text.
- **The rules.** FACTORY_RULES §4 steps 2, 3 and 5 say the question is rendered from the
  locked fixture's title and that the stream probe demands the citation. The journey now
  counts 21 deterministic steps instead of 20.

Pinned by `tests/factory/test_e2e_stream_evidence.py` (the checked-in configuration uses
the template and no literal; the template is rendered with the title and printed, with
the title used verbatim and `{description}` honoured; a missing, blank or absent title
fails closed with the row named, and the journey refuses before the stream probe; the
literal question is used only without a template, and a pre-D-062 configuration runs the
journey on it; a template without `{title}` and a configuration with neither key are
refused before the login probe; the parser's `sources_event`, `sources_count`,
`sources_list` and `tool_calls`, an empty or malformed sources payload, and which status
frames count; the fixture is named by URL or by catalog row id and never by a later entry;
the probe refuses a stream with no sources event, an empty array, or another video first,
and names each; it accepts the row-id match; a failed DELETE is appended to a stream
failure; the journey refuses before the browser when the stream had no citation; the
videos probe returns the fixture's row; the browser asks the same rendered question as the
probe; snapshot text is read unescaped) and by the updated fakes in
`tests/factory/test_e2e_contract.py`. Mutations `e2e-stream-probe-passes-without-sources`,
`e2e-fixture-title-missing-non-fatal` and `e2e-question-template-ignored` are registered
in `harness/factory_mutations/defects.json` and each was verified by direct injection on
the maintainer's Windows host (the copy built by `run.py`, one defect injected, the
detector run).

**Observed and left alone.** Whether the model searches when asked about a titled video
is still the model's choice; the question now names something it can search for, and the
probe reports `tool_calls` so the next refusal says how many rounds it took. The
bootstrap titles the fixture from Supadata (`title` or `Video <id>`); a fallback title is
a title and is not refused here. The StrictMode step the same run was expected to fail at
next is untouched.

**Consequences.** The next main regression prints the question it asked and the title it
was built from, and either `sources=true fixture_in_sources=true` from the probe or
`reason=no-sources-event` with the tool-call count, before a browser opens. A citation
the browser cannot find is no longer a browser finding.

---

## D-063 · The draft deadline is 0.8 of the cap, because the first MiniMax M3 build was ended at turn 18 after seventeen in-scope reads

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34024234313 (the seventh build of issue #103, the first on MiniMax M3 for the mutation roles), 33999901008 and 34002520477 (D-057's two points)

D-057 set `DRAFT_DEADLINE_FRACTION = 0.6` (turn 18 of 30) from two points: issue #49's
test author on GLM wrote at turn ~5 of 15, and issue #103's fourth build on GLM made 46
Reads and no Write in 31 turns. The seventh build of issue #103, the first with D-061's
`model_overrides` sending `test_author`, `implement` and `repair` to `minimax/minimax-m3`,
is the third point, and it contradicts the value:

```
FACTORY_STAGE kind=agent name=test_author seconds=247.176 turns=19 outcome=failed events=32493 draft_deadline_missed=true reads=17 thinking=47272 effort=medium model=minimax/minimax-m3
```

Thirteen seconds a turn, one Read a turn, seventeen Reads, every one inside scope and every
one about the React component test it was to write: the hook and its test, `ChatArea`,
`ChatInput`, `App`, two existing component tests, `package.json`, `biome.json`,
`vite.config.ts`, `main.tsx`, `useMessages`, `authApi`, `useAuth`, and the three artifacts.
The deadline killed it as turn 19 began, four minutes in, before its first write. That is
not the run D-057 described (a slow model reading the kernel for half an hour); it is a
fast, disciplined model doing a legitimate ~20-read task, and 18 was set without seeing one.

**Decision.** `DRAFT_DEADLINE_FRACTION = 0.8`: `draft_deadline_turn(role) = ceil(cap × 0.8)`,
turn 24 of 30 for the three mutation roles. A run that only reads is still ended as turn 25
begins, seven turns short of the 31 the fourth build spent, and a one-read-per-turn model
has room to read a component test's neighbourhood before it drafts. Nothing else about the
deadline changes: the same roles, the same kill on the first turn past it, the same
`no_draft_by_turn` refusal with the reads and the paths, never retried, the same
`$DRAFT_DEADLINE_TURN` rendered from the policy into the three prompts.

Pinned by `tests/factory/test_factory_read_scope_and_draft_deadline.py` (the fraction and
the turn, now also above 18; the request's own cap of 10 gives 8; through the fake CLI a
worker that only reads is killed as turn 25 begins with 24 reads, the record, row, line and
log say so, and the prompt renders `draft by turn 24 of`). The constant's comment in
`factory_kernel/worker_policy.py` carries the three data points. The three deadline
mutations in `harness/factory_mutations/defects.json` edit `providers.py`, not the fraction,
and inject unchanged.

**Observed and left alone.** Whether 24 is enough for M3 on an `implement` or `repair`
stage is the next build's evidence; the fraction is one number and moves on data. A
returned stage still does not record its reads (D-057), so the healthy draft turn on M3 is
read from the transcript, not the stage line.

**Consequences.** The next M3 `test_author` that reads its neighbourhood at one file a
turn gets to its first write; a pure-reading run costs at most 24 turns before the stage is
refused with the list of what it read.

---

## D-064 · A guard's test file may stay untouched, because the first build with a guard was refused for leaving the kept test alone

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34027157595 (issue #103, the eighth build, the first whose test author wrote a guard)

The test author wrote `app/frontend/src/components/ChatArea.test.tsx`, declared red checkpoints on it and a `guard` checkpoint on the existing `app/frontend/src/hooks/useStreamingResponse.test.ts`, the kept hook test the contract says must stay green, and changed nothing else; the static gate passed on the second run, and the kernel refused `builder failed closed: test-author changed ['app/frontend/src/components/ChatArea.test.tsx']; declared acceptance files are ['app/frontend/src/components/ChatArea.test.tsx', 'app/frontend/src/hooks/useStreamingResponse.test.ts']`. The rule that the dirty checkout equals the declared union predates D-058, and a guard's file is precisely an existing test the author must not change. **Decision.** The commit authority (`git_authority.commit_acceptance_tests`) and the RED gate (`factory_proof.declared_files`, called by `red`) apply three rules instead of one: every changed file is declared (`test-author changed undeclared files [...]; every changed file must be declared by a checkpoint` / `test commit changed undeclared files [...]`); every file of a `red` checkpoint is changed (`red checkpoint files [...] are unchanged; a red checkpoint's file must be new or modified`); a `guard` checkpoint's file exists at the head (`guard checkpoint file '...' does not exist at the head; a guard pins an existing test`), is test-shaped by the shared rule, and may be untouched, and a guard file that was changed is accepted only when a red checkpoint declares it too (`guard checkpoint file '...' was changed and no red checkpoint declares it; a guard alone must not rewrite the test it guards`). A red-only spec has the identical outcome to before; the proof still hashes the guard's file, so it is immutable from RED on. `test-author.md` says the rule in one sentence, and D-058's end-to-end fixture now holds its guard file at the base, as a real one is. Pinned by `tests/factory/test_factory_guard_files_untouched.py`; mutations `acceptance-guard-file-required-to-change`, `acceptance-guard-on-missing-file-accepted`, `worker-test-envelope-disabled` (re-anchored), `red-gate-guard-file-required-to-change` and `red-gate-stray-file-accepted` in `harness/factory_mutations/defects.json`.

---

## D-065 · A turn cap ends the worker's loop, and the gates judge its draft — because a build died on an exit code with the test file already written

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34033360798 (issue #103, the ninth build)

`test_author` ended at its 30-turn cap: 289 s, 31 turns, $2.63, `error_max_turns`, 44 tool calls of which 35 were `Read`, and one `Write` plus three `Edit`s had already put `app/frontend/src/components/ChatArea.test.tsx` on disk. The kernel refused the stage on the CLI's exit code, so the static gate, the commit authority and the RED gate never saw the file, and a build that had paid for a draft threw it away. The same transcript shows four `Bash` calls and one `Glob` call answered `No such tool available`, though `worker_policy` granted the role `Read`, `Glob`, `Grep`, `Write` and `Edit`. Two independent defects, measured rather than reasoned about.

**The tool surface.** `--bare` sets `CLAUDE_CODE_SIMPLE=1`, and in that mode the pinned CLI registers only `Read` and `Edit` whatever `--tools` names: measured on 2.1.245 (the pinned version) and 2.1.259 alike, an `init` event reporting `tools=['Edit','Read']` for a request that asked for all five, so `Glob`, `Grep` and `Write` never reached the model however the policy table read. **Decision.** The provider launches every worker with `--safe-mode --setting-sources ""` instead (`providers.ISOLATION_FLAGS`). `--safe-mode` disables the same customisations `--bare` did — CLAUDE.md, skills, plugins, hooks, MCP servers, custom commands and agents — and leaves auth, model selection, built-in tools and permissions working as documented; `--setting-sources ""` loads no user, project or local settings file. The same request under those flags registers all five (`tools=['Edit','Glob','Grep','Read','Write']`, both versions). The D-057 read scope is unchanged and still holds: the CLI applies `Read(...)` rules to `Grep` and `Glob`, so with the trust root denied a tree-wide `Grep` returns the in-scope line and omits the trust-root one, a `Grep` pointed straight at the trust root is refused by name (`Permission to read ... has been denied`), and a `Glob` omits the trust-root path entirely — no `Grep(...)` deny rules are needed and `Glob` is granted in full. The three mutation prompts name the five tools in one sentence and say there is no shell and no test runner, because the kernel runs every check after the worker returns. The read-scope preflight now also greps the tree and globs for the probe files and prints `grep_denied_outside_scope=`, `glob_outside_scope=`, `tools=` and `tools_missing=` beside its old fields, so a future CLI that silently drops a tool is caught on the runner before any stage spends a budget; a leak through `Grep` exits `EXIT_LEAK` exactly as one through `Read`.

**The cap.** A cap is the loop ending, not a verdict about the work (D-020). **Decision.** For `test_author`, `implement` and `repair` only, an `error_max_turns` envelope is returned marked `cap_reached` instead of raised, in both places a cap is classified (the non-zero-exit path and the unwrap). The stage record, its timing row and its `FACTORY_STAGE` line carry `cap_reached=true`, where `outcome=ok` says only that the worker returned; the gate rows that follow say whether the draft was accepted. The kernel then runs exactly the gates a returned worker gets — the scoped static gate with its one hand-back, the commit authority's D-064 rules, then RED or GREEN — on whatever is in the checkout. Two refusals guard the entry: a checkout with no change at all is refused as `no_draft_at_cap`, because there is no draft to judge and the commit authority one step later would misname the cap; and for `test_author` a dirty checkout without a usable `$ARTIFACTS_DIR/test-spec.json` (absent, malformed, or declaring a file that is not on disk) is refused as `no_spec_at_cap` naming the files that were written — precisely run 34033360798's state. When a gate refuses a capped draft, the needs-human comment says the cap was reached, so the verdict is read as a gate's judgement of the draft rather than as the cap's. Every other role's cap, and every budget stop, API error and hang, stays the failed stage it was.

**Not chosen.** The caps themselves do not move. Raising `ROLE_MAX_TURNS` would be tuning a judge's bound on evidence from one build, and the observation here is that the cap was never the problem: the draft existed and was discarded unexamined. Nothing is retried on a cap either — a fresh process would start from an empty checkout and pay the same turns again.

**Consequences.** A capped mutation stage now costs a static-gate run and a commit-authority verdict it did not before, and can end a build green on a draft the worker did not consider finished — which is the point, since the gates, not the worker's own sense of completion, are the authority. Pinned by `tests/factory/test_factory_cap_ends_the_loop.py`; mutations `cap-treated-as-failure-again`, `cap-unwrap-refuses-a-mutation-role`, `cap-with-clean-worktree-proceeds`, `no-spec-at-cap-accepted`, `glob-dropped-from-mutation-roles`, `bare-mode-collapses-the-tool-surface`, `cap-reached-not-recorded` and `cap-comment-silent` in `harness/factory_mutations/defects.json`.

---

## D-066 · The draft deadline records, and the turn cap decides, because the kill retired a worker that was reading its way to a draft

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34042566216 (issue #103, the eleventh build), 34024234313 (D-063's point), 34027157595 and 34033360798 (the same model drafting), 34002520477 (D-057's point)

```
FACTORY_STAGE kind=agent name=test_author seconds=956.017 turns=25 outcome=failed events=148490 draft_deadline_missed=true reads=22 thinking=217204 effort=medium model=minimax/minimax-m3
```

The draft deadline killed a `test_author` at turn 25 of 30 after 22 in-scope reads. It is the
second time on this model: D-063 raised the fraction from 0.6 to 0.8 precisely because run
34024234313 had been killed at turn 18 after 17 reads, and the same model wrote a complete
8.2 KB test file in runs 34027157595 and 34033360798. The model converges; it front-loads
reading, and the deadline lands in the middle of that.

The deadline was invented (D-057) to solve one problem: *a worker reads forever and the build
then dies with nothing to judge*. **D-065 solves that problem strictly better.** A turn cap
now ends the worker's loop rather than the build, and the static gate, the commit authority
and RED judge whatever draft is on disk, with `no_draft_at_cap` and `no_spec_at_cap` as the
named refusals when there is nothing. Everything D-057's kill was for is covered, and covered
later, by evidence about the draft rather than about the clock. What the kill adds on top is
the failure mode above: it truncates a worker that would have drafted at turn 25-28 inside a
budget the kernel had already granted it.

**Decision.** The draft deadline stops killing and stops refusing. It is telemetry.

- **`DraftWatch` observes and never terminates.** It still counts turns as distinct
  `assistant` message ids, still notes the first Write/Edit as the draft and still counts
  `Read` calls with their paths. `observe` returns nothing; the watch carries a sticky
  `deadline_missed`, set the moment a turn past `deadline_turn` begins with no write seen and
  never cleared, so a run that read past its deadline and then drafted says both. `_stream_cli`
  reads the flag off the watch after its loop instead of breaking out of it, and the two clocks
  that do kill (the wall and the idle timeout) are untouched.
- **`_launch` records instead of raising.** `DraftDeadlineMissed` and the `no_draft_by_turn`
  refusal are gone. `providers.draft_deadline_telemetry(run)` returns the same four fields the
  refusal carried - `draft_deadline_missed`, `draft_deadline_turn`, `reads`, `files_read`
  (capped at `FILES_READ_CAP` = 40) - and they ride on every way out of the process: on the
  `AgentResult` of one that returned (new fields on `agents.AgentResult`), and in the
  `observed` telemetry of one killed at its wall, hung, or refused on an error envelope.
- **The record, the row and the line are unchanged in shape.** `runtime.draft_deadline_fields`
  puts the four fields in the stage record of a stage that returned, `record_stage_timing`
  carries `draft_deadline_missed` and `reads` onto the timing row, and `FACTORY_STAGE` still
  prints `draft_deadline_missed=true reads=N`. What changed is what it means: `outcome` beside
  it now says how the stage actually ended, and the flag is a WARNING-level signal for the
  cost and latency analysis, not a verdict. A stage that drafted in time still records no
  deadline fields at all.
- **The needs-human comment reads the records, not an exception.**
  `KernelRuntime._draft_deadline_evidence(paths)` now scans `agent-<role>[.N].json` the way
  `_cap_reached_evidence` does, so a build refused as `no_draft_at_cap` tells the human what
  the worker spent its turns reading - which is exactly the case the evidence was written for.
- **The prompts advise.** The `$DRAFT_DEADLINE_TURN` sentence in `test-author.md`,
  `implement.md` and `repair.md` becomes "draft ... by turn N; the kernel records a stage that
  has written nothing by then, and your turn cap ends the loop". The worker is still steered to
  draft early; it is no longer told it will be killed. Everything else in the three prompts is
  verbatim.
- **`DRAFT_DEADLINE_FRACTION` stays 0.8 and `draft_deadline_turn` stays as it is.** The
  fraction is now the observation point rather than the limit. The caps (`ROLE_MAX_TURNS`) and
  the walls (`stage_timeout_seconds`) do not move: they are what bounds the loop, and D-065
  already made a cap a productive ending.

**Not chosen.** Deleting the deadline outright. The number the analysis needs is *how far into
its budget a worker reads before it drafts*, per model and per role, and the only place the
kernel measures it is this watch; a returned stage still does not record its reads unless it
passed the deadline (D-057's "observed and left alone", still true). Nor was the fraction
raised again: 0.6 then 0.8 were both guesses at a limit, and the lesson of the third data point
is that no fraction is a safe limit, not that this one is too low.

Pinned by `tests/factory/test_factory_read_scope_and_draft_deadline.py`: the watch notes and
decides nothing (`observe` returns `None`), a write in time disarms it, a write past the
deadline leaves the observation standing, no deadline only counts; through the fake CLI a
reading-only worker runs all thirty turns to the CLI's own `error_max_turns`, returns marked
`cap_reached` with `reads=30` and `draft_deadline_turn=24`, and is refused by D-065's
`no_draft_at_cap` and not by any deadline; a worker that drafts at turn 26 completes with
`outcome=ok`, `draft_deadline_missed=true` and `reads=29` in its record, its row and its line;
a stage killed at its wall after its deadline still carries what it read; the paths are capped
at 40 and the count is not; the deadline follows the request's own cap without ending it; the
three prompts carry the advisory sentence and no other prompt names the deadline. The read
scope's own cases are untouched. Mutations `draft-deadline-kills-again`,
`draft-deadline-refuses-the-stage`, `draft-deadline-not-recorded` and (reworded, its anchor
unchanged) `draft-deadline-fires-on-a-run-that-wrote` in
`harness/factory_mutations/defects.json`, each verified by direct injection on the
maintainer's Windows host; `draft-deadline-never-fires` and
`draft-deadline-counted-as-transient` are retired with the code they targeted.

**Consequences.** A mutation worker now spends its whole turn budget or its whole wall, and
the build's verdict comes from the gates rather than from a clock reading. A stage that reads
for twenty-four turns and drafts nothing costs the full thirty turns instead of twenty-five -
roughly a fifth more on the worst case - and the run then ends on `no_draft_at_cap` with the
same list of files the old refusal printed. That is the trade: pay for the last six turns of
the rare bad run, keep the drafts of the common good one.

## D-067 — PROGRAMME.md is the approved engineering programme, and it is protected

**Status:** adopted 2026-09-06.

The repository states what the product is (MISSION.md), how the factory operates
(FACTORY_RULES.md), how code is written (CLAUDE.md) and what was decided (this file). It did not
state what is being built next or what must be true first, so the ordering lived only in a
conversation. `PROGRAMME.md` records it: the end state, the verified gap between that and the
factory as it stands on `ed16952`, the lettered phases with their dependencies, and the invariants
the programme may not trade away.

It is governance, not runtime -- no kernel code reads it -- and it joins the other three governance
documents in `factory_security.protected_path`, because an autonomous run must not be able to
reorder or delete its own roadmap. `test_governance_documents_are_trust_root` pins all four
together.

Recorded there rather than here because a decision entry is a point in time and the programme is a
living order of work; this file records that the programme exists and is protected, and
`PROGRAMME.md` records what it currently says.

**Consequences.** A phase may not start before its blockers close, and phase B (the single-path
floor) is the definition of qualified; it is not to be redefined to unblock later phases. Changing
the programme takes a maintainer PR and a decision entry.

---

## D-068 · The real cap envelope reaches the gates, and whitespace never costs a stage — because a build spent a model stage on a formatting difference and then died on the cap it was driven into

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34047586142 (issue #103), 34033360798 (D-065's point)

The `test_author` of run 34047586142 drafted correctly on its first run: `outcome=ok`, the test
file written, a valid `test-spec.json` v2.1 with two red and two guard checkpoints. The scoped
static gate then failed on ONE biome finding — `Formatter would have printed the following
content:` on a multi-line call — the kernel handed the work back, and the second `test_author` run
spent thirty turns and its whole budget and ended `error_max_turns`. D-065 exists precisely for
that ending: mark the envelope `cap_reached` and let the gates judge the draft. Instead the kernel
raised `RuntimeError: agent worker role='test_author' did not return a JSON result envelope` and
failed the build. Two defects, both measured from the run's own transcripts.

**The envelope.** A real `error_max_turns` result event carries `is_error` and **no `result` key**:

```
error   keys: duration_api_ms duration_ms errors fast_mode_disabled_reason fast_mode_state
              is_error modelUsage num_turns permission_denials queued_turn_count session_id
              stop_reason subagent_stats subtype terminal_reason total_cost_usd type usage uuid
success keys: the same, PLUS result, api_error_status, ttft_ms, ttft_stream_ms, time_to_request_ms
```

`unwrap_result_envelope`'s shape guard was `if not isinstance(raw, Mapping) or "is_error" not in
raw or "result" not in raw: raise`, and it ran BEFORE the `subtype == CAP_SUBTYPE and role in
REPO_MUTATION_ROLES` branch, so the real cap envelope was rejected as malformed and D-065's path
was unreachable for the only payload it exists for. The D-065 tests passed because the fake CLI
emitted `result` on its error envelopes; the fixture, not the rule, is why this shipped.
**Decision.** An envelope is well-formed when it is a Mapping carrying `is_error`. `result` is
required only of a **non-error** envelope, where its absence means a stage returned no text; the
cap branch is classified before that requirement, and `ResultEnvelope.content` for an error or cap
envelope is the empty string, not a crash. The fake CLI now builds error envelopes from
`error_result_event`, the measured payload key for key, and
`tests/factory/test_factory_cap_ends_the_loop.py` pins both key sets literally, so the fixture
cannot drift away from the CLI again. Nothing else about the cap changed: every non-mutation
role's cap, every budget stop and every API error is the failed stage it was, and the transient
classifier is untouched (a real `error_during_execution` envelope does carry `result` — the
committed `run-33933101233-test-author-stream-closed.json` fixture is one).

**The formatter.** The finding that cost the stage was whitespace, and the repository's own
formatter removes whitespace deterministically in the same checkout. **Decision.** When the scoped
static checks fail, `static_gate.check_files` applies the FORMATTER — `uv run ruff format <files>`
for backend files, `bun x biome format --write <files>` for frontend files, on the worker's
declared files only — and re-runs the checks. Only a finding that survives the formatter is handed
back to a worker. What the formatter rewrote is measured by file **content** before and after, not
by a tool's summary line; it rides on `StaticResult.formatted`, is written to the static-gate
artifact as `formatted: [files]`, and is printed as `FACTORY_STATIC_FORMATTED role=<role>
attempt=<n> files=<paths>`, so an edit the kernel made to a worker's draft is always visible
evidence. A formatter that is missing, times out, exits non-zero or changes nothing leaves the
first pass's verdict exactly as it was — today's behaviour, unchanged. The hand-back stays bounded
at one, and the commit authority's file-union rule (D-064) still governs what may be committed.

**Not chosen.** Lint fixes. `ruff check --fix`, `--unsafe-fixes` and `biome check --write` would
close more findings, and each of them can change behaviour — in files that are about to be
RED-hashed and immutable. Formatting is whitespace and cannot; that is the whole line, and
`test_the_source_applies_no_fix_flag_anywhere` holds it. Nor was the static gate weakened to warn
instead of hand back: a real lint or type finding in an acceptance test is exactly what D-043
exists to catch while it is still repairable.

**Consequences.** A capped mutation stage now reaches the gates for the payload the CLI actually
prints, which is what D-065 was written to do and had never once done. A failing static gate costs
one extra formatter invocation and one extra check pass — seconds — and saves a whole model stage
whenever the finding was whitespace, which in the one build measured was 100% of them. Pinned by
`tests/factory/test_factory_cap_ends_the_loop.py` (the measured key sets, the cap fixture matching
them, a cap envelope with no `result` reaching the branch for every mutation role, a non-error
envelope still required to carry its text, and the guard order read off the source) and
`tests/factory/test_factory_static_gate.py` (a formatting-only finding is fixed and never becomes
a finding and never reaches a hand-back; the reformatted file is what is committed; a finding that
survives the formatter still costs its hand-back and records the reformat; a formatter that is
missing, times out or changes nothing behaves exactly as before; only the declared files are
formatted; no lint-fix flag anywhere). Mutations `cap-envelope-refused-as-malformed`,
`result-required-before-the-cap-is-classified`, `static-gate-formatter-not-applied` and
`static-gate-applies-a-lint-fix` in `harness/factory_mutations/defects.json`, each verified by
direct injection on the maintainer's Windows host.

---

## D-069 · A refused RED is handed back once, with its evidence, because a build died on one mis-declared string with the correct test already on disk

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34054922788 (issue #103, the deepest build so far)

Every stage before RED went right. `investigate`, `contract`, `context` and `architecture`
returned. The `test_author` drafted on its first run; the static gate's new formatter pass
(D-068) fixed the whitespace deterministically instead of spending a model stage on it; the
turn-cap path (D-065) was ready and did not need to fire. Then:

```
PROOF_FAIL: AC-1 RED failed for the wrong reason
  argv: ["npx","vitest","run","src/__tests__/ChatArea-strictmode-first-send.test.tsx"]
  cwd: app/frontend   rc: 1   seconds: 4.527
  expected_failure: 'Unable to find an accessible element with the role "button"'
```

D-056's evidence makes the defect readable, and what it shows is not a wrong test. The test
failed, on the unchanged tree, for the bug. The `expected_failure` the author declared was
simply not a string that command printed: it declared the message `getByRole` produces while
the test's own query is a label query, which prints `Unable to find a label with the text
of: ...`. One mis-declared string ended a build of roughly $15 and forty-five minutes with the
correct acceptance test already committed to disk.

**The gap.** The kernel already had the right shape for exactly this, one stage earlier. Since
D-043 the scoped static gate hands its checker's own output back to the role that wrote the
files, once, and ends the build on a second failure — because the only worker that can fix a
file is the one that can still edit it. The RED gate had no such path, and since D-056 it
produces precisely the evidence a hand-back needs: the acceptance id, the argv, the cwd, the exit
code, the seconds, the declared string, the refusal reason and the output tail, on stderr and in
`red-proof-failure.json`.

**Decision.** One hand-back for a refused RED (`RED_HANDBACK_ATTEMPTS = 1`,
`KernelRuntime._red_gate`). When `scripts/factory_proof.py red` refuses, the kernel undoes the
test-author commit (`git reset --mixed` to the head that stage started from, which returns the
draft to the checkout uncommitted, where its author can still edit it), re-runs `test_author`
once with the refusal appended to its original context — the same context it had, so the
contract, the design and any `DEFERRED REPRO SYMPTOM` still reach it — and then re-runs the
**whole** gate from scratch. A second refusal ends the build exactly as before.

**What the hand-back is not.** It is not a licence to weaken the test. The brief says the two
corrections that are open: make the declared `expected_failure` a stable fragment of what the
command actually printed, or change the test's own query so it produces the failure declared.
It says every rule still holds unchanged — every AC keeps exactly one checkpoint, at least one
checkpoint is red, a guard still passes on the unchanged tree, a red checkpoint's file is still
one the author wrote or changed (D-064), a deferred repro symptom still appears verbatim — and
that the gate re-runs from scratch and will refuse again if the failure is still for the wrong
reason. Two ways of answering by proving less are refused deterministically by name before the
gate is re-run (`red_handback_weakened_spec`, `_refuse_weakened_spec`): fewer checkpoints than
the first attempt declared, and a `red` checkpoint re-declared as a `guard`, which has no
`expected_failure` and only has to exit 0. The refusal names the change.

**What is not handed back.** A hand-back is for a mis-declared `red` checkpoint and nothing
else. A `guard` checkpoint's refusal means the contract or the tree is wrong, not the
declaration. A launch or timeout `fault` means the command never produced output the author
could have mis-declared. A refusal with no `red-proof-failure.json` at all happened before any
checkpoint ran — an invalid spec, an undeclared file in the test commit — and has no checkpoint
evidence to hand back. All three end the build on the first refusal, as today.

**Nothing is lost.** Both attempts survive, on the same principle as D-058: `red-gate.log` and
`red-gate.2.log`, `red-proof-failure.json` (the first attempt, under the plain name every reader
already knows) and `red-proof-failure.2.json` (the second, suffixed exactly as
`stage_record_name` suffixes the second run of a stage), and the per-stage records
`agent-test_author.json` and `agent-test_author.2.json` with `stage_run=2` on the timing row.
The needs-human comment carries both: `_proof_failure_evidence` quotes the first refusal as
always, and `_red_handback_evidence` says the gate refused twice and quotes the second.

**Consequences.** The class of build this recovers is the one where the model was right and its
paperwork was wrong, which on the evidence of run 34054922788 is a real and expensive class. The
cost when the hand-back does not help is one extra `test_author` process and one extra gate run
— bounded at one, like every other hand-back in the kernel. `test-author.md` gains one paragraph
on what an `expected_failure` must be and what a hand-back may and may not change. Pinned by
`tests/factory/test_factory_red_handback.py` (a wrong-reason refusal triggers exactly one
hand-back and a second gate run; the brief carries the argv, cwd, rc, seconds, declared string,
reason and output tail plus the author's original context; the undo returns the uncommitted
draft and the accepted build ends with one test commit on the base; a second refusal ends the
build with both records under their own names and both attempts in the comment; a re-draft that
drops a checkpoint or downgrades a red one to a guard is refused by name before the gate re-runs;
a guard refusal, a launch fault, a timeout fault and a refusal with no record are not handed
back; per-attempt records and `stage_run` numbering). Mutations `red-refusal-never-handed-back`,
`red-hand-back-is-unbounded`, `red-hand-back-weakening-check-dropped` and
`red-hand-back-second-record-overwrites-the-first` in `harness/factory_mutations/defects.json`,
each verified by direct injection on the maintainer's Windows host.

---

## D-070 · The answered probes run daily, not on every dispatch, because six effort readings and five thinking-cap readings had already answered their questions

**Status:** recorded · **Raised:** 2026-09-06 · **Runs:** 34017959979, 34024234313, 34027157595, 34028620229, 34031504603, 34033360798, 34042566216, 34047586142, 34054922788 (every worker run whose preflight printed all four probe lines)

Four probes ran on every hourly worker dispatch. Three of them asked a question that nine
runs have now answered the same way, and the archived job logs say what asking again costs.
Per run, measured from the marker lines' own timestamps:

```
run           effort    cap     scope    total
34017959979   149.0s   117.5s   24.8s   291.3s
34024234313   196.9s   104.9s   30.0s   331.8s
34027157595    70.7s   186.8s   30.6s   288.1s
34028620229   119.3s   289.8s   29.4s   438.4s
34031504603   214.5s   268.1s   27.4s   509.9s
34033360798   163.3s   155.3s   20.8s   339.4s
34042566216   146.0s   256.6s   44.6s   447.2s
34047586142   126.5s   256.4s   39.7s   422.6s
34054922788    92.7s   148.1s   34.1s   274.8s
```

Nine runs, 275-510 s each, mean 371.5 s: six minutes of every hourly dispatch and eight model
calls, spent before the first stage starts.

**The effort probe's question is answered.** `FACTORY_PREFLIGHT_EFFORT_PROBE`, same route
(OpenRouter's Anthropic-compatible endpoint), same model (`z-ai/glm-5.3-flash`), same prompt,
the six most recent readings:

```
34028620229  low_thinking=2478  high_thinking=2527  honoured=false
34031504603  low_thinking=2552  high_thinking=3264  honoured=false  error=high:timeout_after_180s
34033360798  low_thinking=2295  high_thinking=6758  honoured=true
34042566216  low_thinking=2435  high_thinking=3668  honoured=true
34047586142  low_thinking=3014  high_thinking=4363  honoured=false
34054922788  low_thinking=2843  high_thinking=2485  honoured=false
```

`honoured` flips run to run with nothing changed between runs, and the two `true` readings sit
between four `false` ones; the three readings before these (34017959979, 34024234313,
34027157595) and D-059's four are the same picture. The route does not honour `--effort` for
the worker model, and a tenth reading of a flipping bit is not evidence, it is noise with a
price. `ROLE_EFFORT` still names a level on every request, because the CLI takes one and a
request without one is refused by the funnel (D-055); what is settled is that the level is not
a **bound** here, and that was the only thing this probe was measuring for.

**The thinking-cap probe's question is answered.** `FACTORY_PREFLIGHT_THINKING_CAP_PROBE`, the
five most recent readings:

```
34031504603  uncapped=6089  cap1024=2561  cap0=1234  honoured=false
34033360798  uncapped=2682  cap1024=3298  cap0=1947  honoured=false
34042566216  uncapped=8612  cap1024=2527  cap0=3264  honoured=false  error=uncapped:timeout_after_180s
34047586142  uncapped=8660  cap1024=2134  cap0=2800  honoured=false
34054922788  uncapped=1688  cap1024=3647  cap0=4493  honoured=false
```

`honoured=false` on every reading, and on the four earlier ones too: `cap1024` regularly
out-thinks the uncapped run, and `MAX_THINKING_TOKENS=0` — a request the CLI sends as
`thinking: {type: "disabled"}` — twice produced *more* thinking than no cap at all. The route
drops the budget. This is also the most expensive of the three, 105-290 s, because it is three
one-turn calls and the uncapped leg has hit the 180-second per-leg timeout. `ROLE_THINKING_CAP`
stays every-row-`None`, which is exactly what D-059 said would happen if the line read false.

**The read-scope probe's question is answered too, but the property is not.**
`FACTORY_PREFLIGHT_READ_SCOPE_PROBE` has read `denied_outside_scope=true
attempted_outside_scope=true read_inside_scope=true read_artifacts=true` on all nine runs, and
`grep_denied_outside_scope=true glob_outside_scope=false tools=Edit,Glob,Grep,Read,Write
tools_missing=none` on every run since the tool-surface fix (D-065). It is a genuine safety
property and it must not simply stop being checked — but a per-run live probe is not what keeps
it honest, and never was. What keeps it honest is the argv: the boundary is `ROLE_PATH_SCOPE`
rendered by `providers.path_rules` into `--allowedTools` / `--disallowedTools`, and a scope that
regresses regresses **there**, in repository code, where a deterministic test reads it. That
test runs in every gate on every PR, needs no model call and no network, and now asserts a
`Read` and an `Edit` deny rule for every one of the thirteen `TRUST_ROOT_DENY_PATHS` (not the
five the brief happens to name), that the deny list contains nothing else, that
`.factory/architecture.json` is the documented exception, and that a mutation role is told
exactly the five tools. The live probe answers a different and narrower question — does the
pinned CLI on this runner still enforce rules it documents — which changes when the CLI, the
route or the model changes, not when a dispatch happens.

**Decision.** The model **route** probe stays on every worker dispatch, unchanged. The other
three move off the per-dispatch path.

- **The worker's switch.** `dark-factory-worker.yml` gains a `workflow_dispatch` boolean input
  `run_answered_probes`, `default: false`. The effort probe becomes its own step (it had been a
  tail on the route step), and it, the thinking-cap probe and the read-scope probe carry
  `if: ${{ inputs.run_answered_probes == true || inputs.run_answered_probes == 'true' }}` — both
  forms, because a dispatch input reaches the expression as a boolean or as a string depending
  on how the run was started, and a bare truthiness test would treat the string `'false'` as
  true. On the hourly schedule `inputs` is null and none of the three runs. Nothing else in the
  workflow is on that switch, and the route probe carries no `if:` at all: an unreachable slug,
  or a new `provider.model_overrides` value nobody proved, must refuse in the preflight rather
  than close mid-build after the earlier stages have spent their budgets (D-061).
- **The daily measurement.** `dark-factory-main-regression.yml` runs all four probes against
  current `main`, ahead of the full gate, every day. Each step's shell body is the worker's
  **byte for byte** — a test asserts the equality — so the archived marker corpus stays one
  corpus and no line's format can drift between the two files. Every marker string is pinned
  literally in `tests/factory/test_factory_workflow_hygiene.py`, including the
  `error=probe-did-not-run` fallbacks, and the two measurements keep their exit-0-always
  behaviour: data, never a gate. The regression job gains the worker's `ANTHROPIC_BASE_URL`, the
  pinned Claude Code CLI at the worker's exact version, and thirty minutes of timeout headroom;
  every probe step there is `continue-on-error: true`, so a probe never decides whether the
  harness runs or whether the regression issue is filed. That job's verdict is the harness on
  `main`; the probes are evidence riding along with it.
- **Where the lines are read.** The archived corpus is already assembled from the runs' job
  logs (`gh run view --log`), which is how the tables above were built; the daily regression is
  a run whose artifacts are uploaded and whose log is kept, so the series continues without a
  new mechanism.

**What reopens these questions.** Named explicitly, because the whole decision rests on nothing
having changed: **a change to `provider.model`, to any value in `provider.model_overrides`, to
`provider.architecture_model`, to `ANTHROPIC_BASE_URL`, or to the pinned
`@anthropic-ai/claude-code` version.** Each of those is precisely the variable the three probes
hold fixed. Any of them changing means dispatching once with `run_answered_probes: true` before
trusting `ROLE_EFFORT`, `ROLE_THINKING_CAP` or the scope on the new configuration — and the
daily line will disagree with the old series within a day either way.

**Not chosen.** Deleting the three probes and their scripts. The questions are answered *for
this route and this model*, which is a fact with an expiry date, not a permanent one; a probe
kept and run daily costs one job six minutes a day instead of every job six minutes an hour,
and keeps the series comparable when the answer moves. Nor was the read-scope probe kept
per-run "because it is a safety property": a property enforced only by a probe that runs after
the trust root has already been checked out is a property with a per-run cost and no per-run
information, and the argv test is both cheaper and stricter.

**Consequences.** An hourly dispatch is 275-510 s shorter and makes eight fewer model calls
before its first stage. The three answers keep arriving, once a day, against current `main`.
Pinned by `tests/factory/test_factory_workflow_hygiene.py` (the input exists, is boolean and
defaults to false; the route probe carries no condition and still refuses an unreachable model;
each of the three carries the gate and nothing else does; their order is unchanged and still
ahead of dispatch; all four probes are in the daily regression, ahead of the gate,
unconditional, each with the route and the credential and `continue-on-error`; every regression
probe body equals the worker's; every marker format appears in both files; the two measurements
never refuse and the read-scope probe still refuses a leak; the argv assertion the scope now
leans on exists) and by
`tests/factory/test_factory_read_scope_and_draft_deadline.py::test_the_deny_rules_cover_every_protected_root`.
Mutations `route-probe-made-conditional`, `answered-probes-still-unconditional-on-dispatch` and
`regression-workflow-missing-the-probes` in `harness/factory_mutations/defects.json`, each
verified by direct injection on the maintainer's Windows host (the copy built by `run.py`, one
defect injected, the detector run: green baseline, red after each).

---

## D-071 · A retry reuses the upstream work it already certified, because fourteen builds of one issue re-derived it for $64

**Status:** recorded · **Raised:** 2026-09-06 · **Evidence:** the archived trajectory corpus
(44 runs, `stage-timings.jsonl` per run): $106.16 of model spend across 161 stages in two days,
of which **investigate $21.63 + context $21.26 + contract $10.73 + architecture $10.10 ≈ $64**
was spent re-deriving the same upstream across fourteen builds of issue #103.

Every one of those fourteen builds ran `investigate`, `contract`, `context` and `architecture`
from nothing after a *downstream* stage failed, on an **unchanged issue at an unchanged base**,
with an unchanged kernel. The upstream those stages produce had already been written and had
already passed its deterministic gate each time. Sixty percent of the factory's two-day spend
bought the same four artifacts fourteen times.

**Decision.** The kernel keeps a **carry**: the certified upstream of a build, bound to the exact
conditions that make it valid, reused by the next build of the same issue.

**Where it lives.** `refs/notes/dark-factory-carry`, a sibling of the provenance ref, keyed by a
per-issue blob (`dark-factory-carry:issue:<N>`) that `git hash-object -w` writes on demand. This
was chosen over a new ref namespace because it needs *no new machinery*: `git notes add -f`,
`show`, `remove`, `fetch` and `push` are already how the factory attaches a JSON payload to a Git
object, and `scripts/factory_provenance.py` already holds the authenticated fetch, the
kernel-identity `notes add` (D-037) and the push. The three new subcommands (`carry-write`,
`carry-read`, `carry-drop`) live in that program and reuse every one of them; a second program
would have duplicated all of it. `git notes add -f` also gives replace-in-place for free, so an
issue has exactly one carry, always the newest.

**What it records.** The issue number; the `base_sha` the build was cut from; `issue_sha256`, a
canonical hash of the issue's **number, title and body** — not of the whole `issue.json`, because
every retry comments on its issue and claims it, which moves `updatedAt` and `labels`, and a
whole-snapshot hash would mean no carry ever matched its own issue; the kernel commit that wrote
it; `policy_sha256`, a digest of `git ls-tree -r` over the whole trust root (`.factory`,
`.github`, `factory_kernel`, `harness`, `scripts`, `tests/factory` and the three governance
files); the run id; and the text and sha256 of each carried artifact — `issue.json`,
`issue-frontier.json`, `ticket.json`, `frontier.json`, `task-contract{.raw,}.json`,
`context{.raw,.enriched,}.json`, `design{.raw,}.json`, `architecture-governor{.raw,}.json`, plus
`repro-observed.json` and `repro-deferred.json` for a bug. `factory-lease.json` is run state and
is never carried.

**When it is written.** In exactly one place: after the architecture gate has returned `proceed`
and its `scope` has run, and before the `test_author` stage exists. Everything in it has passed
its deterministic authority by then; nothing uncertified can enter it. A build that dies at the
context gate writes nothing.

**When it is reused.** At the start of `build_issue`, and only when **every** condition holds,
each checked separately and each with its own refusal reason printed: `absent`, `read_failed`,
`malformed`, `different_issue`, `base_moved`, `issue_changed`, `kernel_not_ancestor`,
`policy_changed`, `artifact_hash_mismatch`, `restore_failed`, `gate_refused`,
`recompiled_mismatch`. The kernel-commit rule is ancestry — a carry from a kernel that is not an
ancestor of the kernel running now is refused — and it is backed by the far stricter
`policy_sha256` equality, so a kernel that changed a prompt, a gate or any `.factory` policy at
all re-derives. When in doubt, miss: a wrongly missed carry costs one build's upstream, a wrongly
hit one costs correctness.

**Not one deterministic authority is skipped.** On a hit the artifacts are restored into the run's
artifacts directory and `factory_protocol.py contract`, `factory_protocol.py context` (which
compiles the ticket, the frontier and the design), `factory_architecture.py compile`, the governor
decision check and `factory_architecture.py scope` all run over them, in the order and with the
arguments a fresh build uses — `build_issue` and `_carry_reuse` call the same five helper methods,
and the tests hold both paths to that. They are seconds of CPU and they are the reason a restored
artifact can be trusted. Two further rules follow from it: `issue.json` and `issue-frontier.json`
are **never** restored, because the kernel has just snapshotted the issue and every `Blocked by:`
issue with its own GitHub authority and the ticket compiler must judge the frontier GitHub reports
*now*; and every compiled artifact the gates rewrite is compared byte for byte against the carried
copy afterwards (`recompiled_mismatch`), because at an identical base with an identical policy the
compilers are functions of their inputs and a difference means something moved that nothing else
caught. Any refusal, any drift, discards the carry, wipes the restored files and runs the full
build. Nothing in the carry path can fail a build: every failure is a miss.

**What is reused, exactly.** The four model stages `investigate`/`plan`, `contract`, `context`,
`architecture`, and nothing else. **Nothing downstream of the architecture gate is ever carried**:
the test author, RED, the implementer, GREEN, both review axes, conformance, the final proof and
the quick gate run in full on every build, from the tree, as they always have.

**Nothing about validation changes.** The certifiers and the holdouts run at validation, judge the
artifacts in the PR, and are shown exactly what they were shown before; the evidence spine's
required claims are still satisfied by real artifacts with real hashes. The one visible difference
is a record: a build that reused a carry writes `carry.json` into its artifacts, `build_pack`
carries it as the pack's optional `carry` block naming the run the upstream came from, `verify_pack`
holds that block to the pack's own issue and base, and the spine binds the pack's sha256 into the
final evidence. RED/GREEN, the guard rules (D-064) and the commit file union are untouched.

**Invalidation.** The issue is edited (`issue_sha256`), the base moves (`base_sha`), the kernel or
any prompt or `.factory` policy changes (`policy_sha256`, kernel ancestry), an artifact fails its
hash, a restored gate refuses, or a gate does not reproduce its artifact. And the merge that closes
an issue drops its carry (`carry-drop` from `validate_pr`, after post-merge verification and before
`FACTORY_MERGED_VERIFIED`): a build of a closed issue is not a build.

**Out of a worker's reach.** The carry is written and read by the kernel alone, through a
trust-root program run with GitHub scope. A worker has neither Bash nor Git, so it could never run
`git notes`; and because the read boundary is the deny list and `.git` sits inside the working
directory, `.git` and `.git/**` are now denied to every tool-bearing role
(`worker_policy.TRUST_ROOT_DENY_PATHS`), which closes the object database — and with it both notes
refs — to Read, Grep and Glob as well.

**Visibility.** `FACTORY_CARRY_HIT issue=#N base=<sha7> stages=<roles> age=<minutes>` or
`FACTORY_CARRY_MISS issue=#N reason=<reason>` on every build, a `FACTORY_STAGE kind=exec
name=carry seconds=… outcome=ok` row carrying `carry=hit|miss` in `stage-timings.jsonl`, so the
saving is read in the same telemetry as the spend, and `carry.json` in the run's artifacts.

**Expected saving.** One retry of an issue at an unchanged base, from the measured corpus:
investigate/plan + contract + context + architecture. Over the fourteen builds of #103 those four
roles cost $63.72 of $106.16; thirteen of the fourteen were retries, so roughly **$4.55 of model
spend and four model stages per retry**, plus their wall time (the four stages measured 888.6 s,
280.3 s, 697.0 s and 262.4 s in run 33987381035 — about 35 minutes).

**Consequences.** Pinned by `tests/factory/test_factory_carry.py`: the carry is written after the
architecture gate and never before and from one place only; a hit skips exactly the model stages
and still runs every deterministic gate; each invalidation condition misses by its own reason; a
tampered artifact refuses at verify and again at restore; a refused restored gate, a vetoing
governor and an unreproduced artifact each fall back to a full build with the artifacts wiped; the
marker lines and the telemetry row; the kernel's fresh issue snapshot survives a hit; the pack
records the carry and may not claim another issue's or another base's upstream; the notes ref
round-trips through a real repository under the kernel identity and is keyed by issue number; and
no tool-bearing role can reach `.git`. Mutations `carry-ignores-a-moved-base`,
`carry-ignores-an-edited-issue`, `carry-skips-the-architecture-gate`,
`carry-artifact-hashes-unverified` and `carry-readable-by-a-worker` in
`harness/factory_mutations/defects.json`, each verified by direct injection on the maintainer's
Windows host.
## D-072 · The re-head knows a guard file is not a changed file, because it refused the first certified build that declared one

**Status:** recorded · **Raised:** 2026-09-06 · **Evidence:** PR #134 (issue #103), the factory's
first successful build of that issue, and its three re-head attempts between 22:30 and 22:35Z.

Main moved under the PR, validation refused `stale_base`, and the model-free re-head ran and
refused, three times in a row, with:

```
NeedsHuman: rebased test-author commit does not change exactly the RED-hashed files; re-head refused
```

Measured from the PR and its provenance note: the test-author commit `c15e61e` changes **exactly
one** file, `app/frontend/src/__tests__/ChatArea-strictmode-first-send.test.tsx`. The RED proof's
`files` map holds **two** — that file and `app/frontend/src/hooks/useStreamingResponse.test.ts`.
The proof's checkpoints are `AC-1 red` on the new test file and `AC-2`, `AC-3`, `AC-4` **guard**,
all three naming the existing hook test. So the guard's file is hashed for immutability (D-058,
correctly) and is never changed by the test-author commit (D-064, also correctly), and
`_locate_rebased_test_commit` compared the commit's changed files against the whole `files` map
with `changed != sorted(files)` and refused. This is the same class of defect D-064 fixed in the
commit authority: a rule written before guards existed, applied to a spec that now has them. It
left a correct, fully certified build with no way back onto a moved main.

**Decision.** The proof's `files` map answers "what is immutable"; the proof's **checkpoints**
answer "what did the commit change". Nothing in the re-head path may use the first to answer the
second.

- `_locate_rebased_test_commit` derives the red-checkpoint files and the guard-checkpoint files
  from the checkpoints (`kind` absent means red, per the 2.1 spec) and matches the rebased
  commit's parent diff against the **red** files exactly, by the three rules D-064 wrote for the
  commit authority: a guard file no red checkpoint declares was not changed (`rebased test-author
  commit changed guard checkpoint files [...] that no red checkpoint declares; a guard pins an
  existing test the author must not rewrite`), every changed file is declared (`rebased
  test-author commit changed undeclared files [...]`), every red file is changed (`red checkpoint
  files [...] are unchanged at the rebased test-author commit`).
- **Guard immutability becomes a check rather than a side effect.** The old equality happened to
  cover a guard file only because the map contained it; with the diff read as the red half alone,
  nothing would. So each guard file must now exist at the rebased commit and hash there to what
  the pack recorded, read as bytes (`git cat-file blob <commit>:<path>`, since `_exec` decodes and
  strips text and would not reproduce the digest). A missing one refuses naming the file and the
  commit; a moved one refuses naming the file and **both** hashes. That is strictly stronger than
  what the equality gave, and it is the property D-058 intended.
- The same conflation was one stage further down the same path: `scripts/factory_evidence.py`
  `replay_red`, which validation runs on the head the re-head produces, compared the test commit's
  diff to `proof["files"]` with the identical `!= sorted(files)`. A re-headed guarded build would
  have died there instead. It now calls `verify_test_commit_diff`, the same three rules; its
  existing hash loop over the whole `files` map inside the RED worktree already establishes both
  halves' immutability at the test commit and is unchanged. `_verify_red_unchanged` (re-head and
  resume) is also unchanged and correct: the whole map is the right question at the tip.

**Consequences.** Pinned by `tests/factory/test_factory_rehead_guard_files.py`, whose fixture is
PR #134's measured shape (one red checkpoint on the new test, three guards on the hook test, a
two-entry `files` map): it re-heads; a commit that also changed the guard file, one missing the
red file, one touching an undeclared file, a guard absent at the rebased commit and a guard whose
hash moved are each refused by name; a red-only proof behaves exactly as before and asks for no
guard hash. `harness/rehearsal.py` grew `pack_checkpoints`, `test_commit_changed` and
`blob_hashes` so a guarded pack can be rehearsed at all. Mutations
`rehead-compares-the-whole-file-map`, `rehead-guard-hashes-unverified`,
`rehead-changed-guard-file-accepted` and `evidence-replay-compares-the-whole-file-map` in
`harness/factory_mutations/defects.json`, each verified by direct injection on the maintainer's
Windows host.

## D-073 · The mutation rung's budget comes from a measurement, because the first validation run to reach it died on a number nobody had ever measured

**Status:** recorded · **Raised:** 2026-09-07 · **Evidence:** PR #134 (issue #103), worker run
34066724127 — the factory's first build to pass every judge and the browser journey.

That run passed security, provenance, all five blinded judges, static, unit, the holdout and,
for the first time, the whole browser journey (`E2E_PASSED steps=21`,
`HOLDOUT_PASSED scenarios=3 assertions=9`, the streaming UI observed as
`[send-button, stop-button+assistant-text, send-button+assistant-text+citation]`). Then:

```
TIMEOUT after 900s
GATE_FAILED: mutations
```

`harness/ci.py` ran the rung as `run("mutations", [...], timeout=900)`. Nothing recorded where
900 came from. Underneath it the application catalogue had gone from four defects to nine, the
factory trust-root catalogue to 391, and the suite each channel re-runs to 2184 tests.

**Measured, before deciding anything.** The maintainer's Windows host cannot run the rung at
all — `uv` and `bun` are not installed there, so the `quick` channel can never be green and the
runner refuses at its baseline (`python -m unittest discover -s tests/factory` on clean
`origin/main` there: 1576 tests, 216.5 s, 32 failures and 120 errors). So the parts were
measured where each part actually runs:

- **The quick channel, on the validation host:** 179.027 s (run 34066408781, `rehead-quick-gate`),
  203.383 s (run 34061371205, `quick-gate`), and 207.4 s measured off the timestamps of the
  `quick-authority` job of run 34066129225 (`23:10:39.7` → `STATIC_OK` `23:11:56.99` →
  `UNIT_PASSED tests=2184` / `GATE_OK` `23:14:07.08`). p100 **207.4 s**.
- **The rest of the ladder, on the validation host:** run 34066724127's evidence stage was
  `seconds=1191.719 outcome=refused`, of which 900 s was this timeout — so static, unit, app
  start, the browser journey and the holdout together cost **291.7 s**.
- **The deterministic channels:** `scripts/factory_security.py --worktree` 0.39 s and
  `harness/immunity.py` 0.23 s on the maintainer's host; the guard's `--pr` form 1.4 s in CI.
- **The factory family's unit of work, file by file:** one copy of the trust root costs 0.13 s
  to build and **235.3 s** to run, across 72 test modules — median 0.59 s, p90 7.81 s, max
  60.7 s (`test_factory_bootstrap.py`). There are **391 defects**.

Which gives, for the rung as it stood: **2674 s** for the application family (ten iterations of
one baseline plus nine defects, each paying the quick channel) — 2.97× the budget it was given,
and the family the clock was still inside when it expired — and **23,000 s** (383 minutes) for
the factory family behind it. No budget that covers that is a budget; it is a work day.

**And the factory family had never run at all.** `TEST_FILES` selected every copied path under
`tests/`, which includes the recorded JSON fixtures the tests read, and `run_tests` executed
each of them with `python <path>`. Three are not valid Python, so `FACTORY_MUTATIONS_REFUSED
focused baseline is red` was the outcome every time that family was reached — on any platform.
Nobody saw it because the application family exhausted the 900 s first, and because
`run()` discarded the timed-out rung's partial output.

**Decision.** The clock is derived from recorded measurements, and it never selects what runs.

- `harness/mutations/budget.json` holds the measurements; `harness/mutation_budget.py` derives
  `ceil(p100(totals for a scope) × headroom / 60) × 60` from them, with headroom ≥ 1.5 and every
  entry required to carry its date, environment, kind (`measured` / `projected`) and source.
  `harness/ci.py`, the nested factory call and `scripts/factory_evidence_spine.py`'s independent
  re-observation (which carried the same unmeasured shape as `timeout=1200`) all read it. p100
  and not the mean: a rung has to finish on its worst run.
- **The cheapest honest reduction, not a bigger number**, for the family that made the total
  unreasonable. The 391 copies are independent by construction, so they are evaluated
  concurrently; each copy's focused suite stops at its first red file, in the order the baseline
  measured those files to cost, cheapest first. Both are verdict-identical: a suite is red as
  soon as one file is red, a green suite still runs every file, and the order is always a
  permutation of the whole suite. Against the measured cost distribution that is 30.3 s per
  caught defect instead of 235.3 s (12.9%; declaration order would be 86.1 s), so the family
  projects to 391 × 30.3 / 4 = **2962 s** and the rung to **5636 s**, budgeted at **8460 s**
  (141 min) with the family alone at **4500 s**. No defect was dropped, no defect was narrowed
  to its own detector, and the rung is not skipped in validation.
- **Drift is visible while the run still passes.** `MUTATION_TIMING id=<id> seconds=<n>
  outcome=caught|escaped` per application defect, `seconds=` on every factory defect line,
  `MUTATIONS_SECONDS` / `FACTORY_MUTATIONS_SECONDS` totals, `MUTATIONS_OK defects=<n>
  seconds=<n> budget=<n>`, and `MUTATIONS_BUDGET_WARNING` above `warn_fraction` (0.75) of the
  budget. Every other timed rung of the ladder prints `RUNG_SLOW step=<name> seconds=<n>
  timeout=<n> fraction=<f>` on the same threshold, and a rung that does time out now keeps the
  output it had already produced, so the next timeout names the defect it reached.

**Consequences.** Both entries in the record are `kind: projected` and say so: this host cannot
produce an end-to-end number, and the first full run on the validation host publishes real ones
that should replace them — the timing lines exist for exactly that. Two facts are left standing
for whoever reads them next. The factory family runs **twice** in validation, once nested in the
ladder and once in the spine's independent re-observation, and only `harness/observe.py`'s
post-merge transcript parser now requires it from the ladder; measuring each family once, in the
stage that owns it (the rule genesis already follows), would halve the rung again. And 391
defects × a whole trust-root suite is a cost that grows with the catalogue and will need
answering again. Pinned by `tests/factory/test_factory_mutation_budget.py` (41 tests: the
derivation, p100, the rounding, the provenance requirement, the warning threshold, the timing
and closing line formats, the ladder's timeout and its partial output, the concurrent runner's
completeness and cleanup, the fail-fast verdict, and the spine's derived deadline). Mutations
`mutation-budget-warning-removed`, `mutation-budget-is-a-bare-literal-again`,
`mutation-budget-ignores-its-headroom`, `mutation-budget-takes-the-mean-not-the-worst`,
`factory-mutations-skip-defects-when-short-of-time`,
`factory-mutations-lose-an-escape-under-concurrency`, `factory-mutations-order-narrows-the-suite`,
`factory-mutations-run-fixtures-as-tests`, `factory-mutations-stop-before-a-green-suite-finishes`,
`mutation-timing-line-loses-its-outcome`, `mutations-ok-line-drops-its-clock`,
`ci-timeout-discards-what-the-rung-said`, `ci-slow-rung-warning-removed` and
`spine-factory-authority-keeps-its-literal` in `harness/factory_mutations/defects.json`, each
verified by direct injection into a real copy on the maintainer's Windows host.

## D-074 · The factory mutation family's first real run found a test that was not hermetic, because a suite that needs the tree around it cannot be run from a copy

**Status:** recorded · **Raised:** 2026-09-07 · **Evidence:** validation run 34073357593 of
PR #134 — the first run in which the factory mutation family ever executed.

D-073 gave the family a budget derived from measurement, and the family reached the end of it
for the first time: `MUTATIONS_SECONDS=1055.1`, inside its bound. It still reported

```
MUTATIONS_FAILED - an application or factory defect can currently escape
```

and the reason was not a defect that escaped. It was the baseline:

```
File "/tmp/dark-factory-meta-baseline-p2dsxx2r/root/tests/factory/test_factory_worker_throughput.py",
  line 262, in test_worker_stage_writes_text_telemetry_and_timing
File ".../factory_kernel/worker_runtime.py", line 437, in _refuse_literal_artifacts_dir
File ".../factory_kernel/runtime.py", line 3419, in _git
ToolRefused: git status --porcelain --untracked-files=all failed rc=128:
  fatal: not a git repository (or any of the parent directories): .git
```

`WorkerControlledRuntime._agent` finishes a non-mutation role by running `git status` in the
directory it was handed, and the test handed it `ROOT` — the tree the test file itself lives
in. In a checkout that works, because the checkout happens to be a repository. The family
copies the trust root into a plain temporary directory, so there is no `.git` above `root/`,
and the ambient dependence the test had always carried became an error.

**The decision.** A test in `tests/factory/` may not depend on the tree around it being a
repository. The stage gets a repository the test created
(`git_repo()` in `tests/factory/test_factory_worker_throughput.py`), and every other place in
the suite that named the ambient checkout as a stage's working directory now names a directory
the test owns instead — `test_factory_authority_bounds.py` (2), `test_factory_prompt_paths.py`,
`test_factory_read_scope_and_draft_deadline.py` (2) and `test_factory_failed_stage_telemetry.py`,
none of which was failing, all of which were one deleted stub away from failing.

**What the sweep found.** The whole 84-file suite was run twice on the maintainer's Windows
host at `origin/main` 3d1873f, once from the worktree and once from a copy of it with `.git`
removed, comparing failure sets file by file and, inside every red file, test id by test id.
Exactly one file differs: `tests/factory/test_factory_worker_throughput.py`. With
`FACTORY_WORKDIR` set to an absolute path the baseline is a single known red
(`test_factory_bootstrap.py`, the genesis driver's `sha256` under CRLF), identical in both
trees, so nothing is hidden behind an earlier failure. After the fix, both trees are that same
single red. No test in the suite genuinely needs the real repository.

**Why the detector is static.** The honest behavioural check — build a non-repository copy and
run the suite in it — is precisely what the family already does 391 times, and putting it
inside the suite would make each of those 391 copies build and run a copy of its own.
`tests/factory/test_factory_suite_hermetic.py` parses each `tests/factory/test_*.py` instead
and refuses any call that runs `git` in the checkout the file lives in: `_agent`'s and `_git`'s
working directory, or a `git` subprocess, given the module's `__file__`-derived root or
anything assigned from it. `_exec` is deliberately outside that set — it runs whatever argv it
is handed, its subprocess boundary is mocked everywhere the suite points it at the checkout,
and a rule over it would flag tests that execute nothing. The check costs about 0.2 s, runs in
every copy, and is exact about the mechanism that actually broke. It is also applied to
itself: `PositiveControlTests` feeds the analyser sources it must flag, so a scan reduced to
"find nothing" fails there rather than passing quietly.

**Consequences.** Suite hermeticity is a precondition for the factory mutation family running
at all, not a property of one test: every one of the 391 copies runs this suite, so a single
non-hermetic test costs the whole family, and it costs it as `focused baseline is red` —
a refusal that names no defect. The family had never run before D-073, which is why a
dependence this old surfaced only now. Mutations
`worker-throughput-stage-borrows-the-ambient-repository` and
`suite-hermeticity-scan-stops-reading-agent-call-sites` in
`harness/factory_mutations/defects.json`, each verified by direct injection into a copy that
is not a repository on the maintainer's Windows host.

## D-075 · Every timeout comes from one budget, and a wrapper bounds what it contains, because the same defect ended three runs in two days

**The third instance, and the one that named the class.** D-073 derived the mutation rung's
clock from measurement after run 34066724127 of PR #134 died on `TIMEOUT after 900s` with every
other gate green. It gave the rung 8460 s and the factory family 4500 s. The next validation
run of the same PR, 34081507222 on 2026-09-07 04:43Z, passed security, provenance and all five
judges, and then:

```
FACTORY_STAGE kind=exec name=evidence seconds=1819.168 outcome=refused
subprocess.TimeoutExpired: Command '[python, harness/ci.py]' timed out after 1800 seconds
```

`scripts/factory_evidence.py` ran the whole ladder as one subprocess with a literal
`timeout=1800`. An inner budget now exceeded its wrapper by a factor of 4.7. The 1800 lived one
file further in than the report placed it: `scripts/factory_evidence_spine.py` bounds
`factory_evidence.py` at 3000 s, and `factory_evidence.py` bounds `harness/ci.py` at 1800 s.
Both were literals, and both were too small.

Three instances of one defect in two days: the mutation rung's 900, the spine's 1200, the
ladder's 1800. Each a duration written as a literal, each outgrown in silence, each discovered
as a timeout on the run that mattered. The rule that ends it has two halves, and D-073 shipped
only the first.

**A duration comes from a measurement.** `harness/mutation_budget.py` becomes
`harness/budget.py` and `harness/mutations/budget.json` becomes `harness/budgets.json`, because
they no longer describe the mutation rung: they describe every clock in the ladder. Eighteen
scopes, each with an observation behind it and a `kind` that says `measured` or `projected` out
loud, each derived as `ceil(p100 x headroom / 60) x 60`.

**A wrapper bounds what it contains.** Every scope also declares the scopes whose clocks run
inside it (`contains`), the contained work whose deadline is set elsewhere (`bounded_seconds`,
added as it stands, because a bound given headroom twice is how a tower inflates), and the
measured work with no deadline at all (`unbudgeted_seconds`, which gets the headroom).
`harness/budget.py:validate()` **refuses** a record in which any wrapper's budget is below that
sum -- from `load()`, not only from a test, so an inconsistent record stops every runner that
reads it. Raising an inner budget past its wrapper now fails the suite in the change that
raises it, instead of arriving as a `TimeoutExpired` on a future validation run.

**What the audit found once the rule existed.** Every wrapper in the ladder, checked against
what it contains:

| Wrapper | Was | Contained | Now |
|---|---|---|---|
| `harness/ci.py` static rung | 300 (implicit default) | `harness/static.py`, 5 x 600 = 3000 | 360, one shared deadline |
| `harness/static.py` per check | 600 | -- | what is left of 360 |
| `harness/ci.py` unit rung | 300 (implicit default) | `harness/unit.py`, 3 x 900 = 2700 | 360, one shared deadline |
| `harness/unit.py` per suite | 900 | -- | what is left of 360 |
| `harness/ci.py` e2e watchdog | 360 (in `harness.config.json`) | the two stream probes | 360, from the record |
| `harness/ci.py` holdout rung | 300 (implicit default) | -- | 120 |
| `harness/mutations/run.py` per channel | 900 | `ci.py --quick`, 360 + 360 = 720 | min(780, family remainder) |
| `factory_kernel/runtime.py` quick gate, x2 | 900 | `ci.py --quick`, 750 | 780 |
| `dark-factory-ci.yml` | 20 min | 1080 s = 18 min | 20 min, unchanged and now checked |
| application family | nothing bounded it at all | 40 channel runs | 4020, one shared deadline |
| `harness/factory_mutations/run.py` per file | 180 | -- | min(120, family remainder) |
| factory family | 4500 | one file, immunity | 4500, unchanged |
| `harness/ci.py` mutation rung | 8460 | 4020 + 4500 = 8520 | **8580** -- D-073's own number was 60 s short of its two parts |
| `scripts/factory_evidence.py` ladder | **1800** | 9900 | 10020 |
| `harness/observe.py` ladder | 3600 | 9900 | 10020 |
| `harness/post_merge.py` ladder | 3600 | 9900 | 10020 |
| `scripts/factory_evidence_spine.py` -> bundle | 3000 | 10517 | 10620 |
| `scripts/factory_evidence_spine.py` -> provenance | 240 | -- | 240, from the record |
| `factory_kernel/runtime.py` evidence-spine stage | 2400 | 15450 | 15540 -- it was already below the 3000 it allowed its own child |
| `factory_kernel/worker_runtime.py` post-merge | 4800 | 11400 | 11400 |
| `dark-factory-main-regression.yml` | 150 min | 11385 s = 189.8 min | **190 min** |

Wrappers checked and left alone: `dark-factory-trust-root.yml`'s
`timeout-minutes: 10` and `dark-factory-branch-cleanup.yml`'s `timeout-minutes: 10` bound
deterministic guards measured in single-digit seconds; the kernel's `provider.timeout_seconds`
(2700) already has a containment check of exactly this shape in
`worker_policy.assert_caps_fit_timeout`, which refuses any role whose wall (max 2025 s at 30
turns) would not fit under it; the remaining `subprocess` timeouts in `scripts/` and `harness/`
bound single `git` and `gh` calls and contain nothing that carries a budget.

**The one wrapper no number can close, recorded rather than papered over.**
`dark-factory-worker.yml` allows a dispatch 300 minutes. A validate-and-merge dispatch contains
the evidence spine (15540 s) and then post-merge validation (11400 s), plus five judge and
certifier stages at their worker-policy walls (5 x 675 s) and about 900 s of setup: a 31665 s,
528-minute floor. GitHub caps a hosted job at 360 minutes, so no value of `timeout-minutes`
bounds it, and raising 300 to 340 would buy the appearance of a bound without one. 8580 s of
the ladder's 10020 s is the mutation rung, and that number is a projection with no observation
behind it: 2674 s of arithmetic for the application family and 2962 s of arithmetic for the
factory family, neither ever timed end to end. **What closes this wrapper is the first real
measurement of the mutation rung, not a bigger number in a workflow file.** The arithmetic is
written into `.github/workflows/dark-factory-worker.yml` beside the value it cannot justify, and
into `harness/budgets.json` under `_the_one_wrapper_this_record_cannot_close`, so the next
person meets it as a stated open question rather than as a cancelled job.

**Two repairs that fell out of the audit.** `harness/mutations/run.py` called
`subprocess.run(..., timeout=900)` with no handler, so a channel that ran long would have taken
the whole runner down with a traceback and no verdict for any defect; a timeout is now a red
channel named `<channel>-timeout`, never mistaken for a real catch. And
`harness/harness.config.json` no longer carries `e2e_timeout_s`: it was the only rung deadline
that lived in configuration while the other four lived as literals in code, and one record for
all five is the whole point.

**Detection.** `tests/factory/test_factory_ladder_budget.py` (23 tests) pins the containment
rule for every declared scope, the connectedness of the chain from the job down to one test
file, the refusal of a record whose wrapper stopped containing its parts, the labelling of a
projected budget, the absence of any literal `timeout=<number>` in the six ladder files and in
the ladder call of the five files that also bound plumbing, and both job limits (`dark-factory-main-regression.yml` and
`dark-factory-ci.yml`) against the gates they run. The no-literal detector reads the syntax tree rather than the text, so
`timeout=901` is caught exactly as `timeout=900` was; a substring rule would not have been. The
containment walk is a function whose coverage is asserted separately, so narrowing it to a
subset fails there rather than passing quietly. Mutations
`ladder-budget-inner-raised-past-its-wrapper`, `ladder-budget-containment-check-made-vacuous`
and `spine-literal-timeout-reintroduced-for-the-evidence-bundle` in
`harness/factory_mutations/defects.json`, each verified by direct injection on the maintainer's
Windows host, along with the four D-073 mutations whose anchors moved with the rename.

## D-076 · Every mutation defect must be injectable, and the static rung says so, because twenty-three had quietly stopped being detectors

**The gate that blocked the whole factory.** Validation run 34088776764 of PR #134 (issue #103)
on 2026-09-07 05:57Z was the first run since D-075 to get past the trust-root currency check and
actually reach the mutation rung. It passed security, provenance and all five blinded judges, ran
409 factory defects in 1943.6 s, and refused:

```
FACTORY_MUTATIONS_TOTAL=409
FACTORY_MUTATIONS_CAUGHT=383
FACTORY_MUTATIONS_NOT_INJECTED=23
FACTORY_MUTATIONS_FAILED - factory trust-root bypass survived
GATE_FAILED: mutations
EVIDENCE_SPINE_FAIL: core Evidence Bundle authority rejected the PR
```

Twenty-three defects could not be injected at all, and three escaped. None of the twenty-three
had anything to do with #134, whose diff is frontend product code: the trust root in that PR is
byte-identical to `main`'s, so `main` was failing its own mutation catalogue. Nothing could merge
until it stopped.

**What "not injected" actually means.** A mutation defect is an exact anchor into source. The
runner copies the trust root, requires `text.count(find) == 1`, rewrites that one occurrence, and
asserts the focused suite goes red. When a refactor moves the anchor, the count becomes 0 (or 2)
and the defect is reported `not_injected` — the catalogue still lists it, the run still prints a
total, and **the property it stood for has had no detector since the refactor**. It is not a
failing test. It is a test that quietly stopped existing.

Reconstructed with a local replica of `inject()` against `main` at `574857f`, which reproduced
the runner's twenty-three exactly. Every one was drift, not deliberate removal:
`--bare` had become `ISOLATION_FLAGS` (D-065); `timeout=300` had become
`CHECKPOINT_TIMEOUT_SECONDS` (D-075); the RED credential scope had moved inside `_red_gate`;
`blind=BUILDER_BLIND_PATHS` had gained two more call sites so the anchor was no longer unique;
the `.spec.` predicate had moved to the one shared `scripts/factory_shapes.test_shaped` (D-064);
the failed-stage record had moved from `worker_runtime` to `runtime`'s single agent funnel
(D-050); the re-head's file-shape check had moved into `_verify_test_commit_files` (D-072). Each
anchor is re-pointed at the code that carries the property today, and two `why` texts are
rewritten where the mechanism itself changed rather than merely moved.

**Why it took ten merges to notice.** The only thing that runs the mutation rung is the full
harness. The full harness runs in factory validation — roughly fifty minutes in, after five model
judges — and in the daily main regression, which since #119 fails at the E2E rung and returns
before it ever reaches mutations. `quick-authority` and `trust-root-authority`, the two checks a
maintainer pull request actually meets, run `harness/ci.py --quick`: static and unit. So every
maintainer PR from #126 to #138 was free to move an anchor, and thirteen of them did.

**The check belongs where the anchor moves.** Injectability is pure text: read the file, count the
anchor. `harness/mutation_anchors.py` does it for all 414 factory defects and all 9 application
defects in under a second, and it is now the first check in the static rung, which means it runs
in `--quick`, which means it runs on every maintainer pull request. The maintainer who moves an
anchor is told on their own PR, by name, instead of a validation fifty minutes deep being told a
number three days later.

It mirrors each runner's own rule rather than inventing a third. The factory family copies the
trust root and requires a unique anchor, so a file outside the copy set fails even when the anchor
matches — the copy would not contain it. The application family mutates the live worktree and
requires only presence, because `uuid-normaliser-dropped` deliberately changes the first of two
identical call sites to make two entry points derive different lock keys; an ambiguous application
anchor is a printed note, not a failure.

**It must never run inside a mutation copy.** A copy has exactly one anchor deliberately removed.
A check of every anchor would go red in all 414 copies and report all 414 defects as caught,
converting the family's entire signal into noise. The static rung is not run by any copy; the
copies run the test files in `COPY_FILES` and nothing else. `tests/factory/test_factory_mutation_anchors.py`
is in that set and therefore does run in every copy, so every fixture in it is synthetic and no
test in it reads the real catalogue.

**A failure now names its members.** The second half of the diagnosis cost as much as the first.
`validation-refusal.json` stores the tail of a refused tool's output, and 409 per-defect lines
pushed the three `ESCAPED` rows out of it; the workflow log holds the exception, not the stream.
The refusal reached the overseer as "three survivors" with no names, and identifying them needed a
local re-run of the whole catalogue. Both runners now print `FACTORY_MUTATIONS_ESCAPED=<ids>` /
`FACTORY_MUTATIONS_UNINJECTED=<ids>` (and `MUTATIONS_ESCAPED` / `MUTATIONS_UNINJECTED`) at the
tail, beside the failure marker, where truncation cannot reach them. This is the same principle as
D-041 and D-054: a failure that records only a count is a failure that has to be reproduced before
it can be read.

**Detection.** `tests/factory/test_factory_mutation_anchors.py` (21 tests) pins copy-set membership
including the prefix near-miss, all four ways a factory defect fails to inject, the application
family's weaker rule and its ambiguity note, the reporting of every failing defect rather than the
first, the `MUTATION_ANCHORS_OK`/`_FAILED` markers with both totals, the static rung's call, and
both runners' tail markers with the factory one ordered before its failure line. Five mutations —
`mutation-anchor-check-dropped-from-the-static-rung`, `mutation-anchor-uniqueness-made-vacuous`,
`mutation-anchor-copy-set-check-skipped`, `mutation-anchor-failures-reported-as-a-count` and
`factory-mutation-failures-leave-their-members-unnamed` — each verified caught by direct injection
on the maintainer's Windows host, because the full focused suite is red there for unrelated
platform reasons and CI is the authority on the rest.

**Still open: the three escapes.** This change does not fix them; it makes the next run say which
they are. Twenty-three of the twenty-six failures are closed here, and the escapes are the
remaining work, tracked as its own change once a validation run names them.

## D-077 · A base move is cheap to notice and free to recover from, because five judges kept being paid to discover a `git diff`

**What a maintainer merge costs an open factory PR.** Validation run 34112301646 of PR #134 on
2026-09-07:

```
FACTORY_STAGE kind=agent name=holdout               seconds=238.4 cost_usd=0.300865
FACTORY_STAGE kind=agent name=architecture-holdout  seconds=  8.5 cost_usd=0.108295
FACTORY_STAGE kind=agent name=contract-certifier    seconds=211.3 cost_usd=0.298865
FACTORY_STAGE kind=agent name=design-certifier      seconds=150.7 cost_usd=0.450620
FACTORY_STAGE kind=agent name=governor-certifier    seconds=260.6 cost_usd=0.478875
FACTORY_STAGE kind=exec  name=evidence              seconds=  0.4 outcome=refused
EVIDENCE_FAIL: PR trust root is not current with origin/main; rebase required: ...
```

869 s and $1.64 of blinded judgement, then a refusal in 0.395 s by a `git diff`. That was the
fifth time this one PR paid for it (01:04, 01:24, 03:51, 05:48, 10:29Z), and the answer had been
knowable before the first judge started every time.

**Why D-042's early check does not catch it.** `validate_pr` already compares the provenance
pack's declared base with GitHub's current base before it fetches the pack, and refuses
`stale_base` there. That is a different question. `trust_root_drift` compares the PR head's
*trust-root files* with `origin/main`; a pack cut from what GitHub reports as the current base
can still carry a trust root `main` has moved past, which is exactly the state a re-headed PR is
in when another maintainer PR lands during its validation. Both questions are cheap; only one
of them was being asked early.

**The check moves, the authority does not.** `scripts/factory_evidence.py --currency-only` runs
the same three checks the Evidence Bundle opens with — the worktree is the PR head, the PR has
not touched the trust root, the trust root is current — and exits. The kernel runs it right
after the pack-base comparison, before the code holdout, as its own `trust_root_currency` stage.
The Evidence Bundle asks all three again and is still the authority. **An added refusal point
can only refuse more, never authorise**: if the pre-check were wrong in the permissive
direction, the bundle refuses exactly as it did before, so this cannot widen what merges.

Its refusal keeps its class. `is_stale_base` classifies by message text before any stage or tool
rule runs, so the same sentence from the same program is a `stale_base` refusal wherever it is
raised — which is what makes the re-head follow automatically. Only the *non*-stale failures of
the pre-check needed a new code, because recording them as `evidence_spine` would name an
authority that never ran.

**The budget counted the wrong thing.** `rehead_eligible` allowed one re-head per pull request.
Its reasoning was sound — bound a loop nobody has data to size — but a base that moved because a
human merged is not that loop. A re-head is model-free: it rebases, replays RED at the rebased
test-author commit, re-runs conformance and every downstream gate, and re-publishes provenance.
How many happen is decided by how often a maintainer merges. PR #134 hit the wall three times in
nine hours, and each time a maintainer deleted the marker comment by hand so the factory could
continue. That is precisely the shepherding the budget was never meant to create.

The budget now counts base moves. A second re-head is allowed only when the PR's head is exactly
the head the last re-head produced — nothing has happened to this pull request since, except
that main moved again. A PR that *changed* after its re-head and failed again is still refused,
because that is the loop. A caller that cannot say what the head is gets the old strict rule: a
budget that cannot check its own condition must refuse rather than assume.

**Found by the check shipped one hour earlier.** Inserting the currency call between
`_builder_pack` and the holdout broke the anchor of `pack-verified-after-holdout`, and rewriting
`rehead_eligible` broke `rehead-cap-removed`. `harness/mutation_anchors.py` (D-076) named both
before this change was committed, which is the first thing that check has caught and exactly the
silence it was built to end. The currency call moved one step earlier so the D-048 ordering
anchor is untouched, and `rehead-cap-removed` is re-anchored onto the cap that exists today
rather than being duplicated by a new defect.

**The budget has two askers, and one of them cannot ask the predicate.** The refusal path
escalates a repeat stale base to `factory:needs-human`, and it decides that WHILE it composes
the refusal comment — so the marker for the refusal it is describing is not in the comments
yet, and `rehead_eligible` would answer False for the wrong reason. It was asking
`rehead_count(...) >= 1` and posting "the re-head budget is one per PR", which this change makes
untrue: a message that contradicts what the next dispatch does is worse than no message. The
budget clause is now `rehead_budget_allows`, called by the predicate and by the refusal path
with that refusal's own head, so both answer the same question from the same code.

**One program, two calls, two failure modes.** `harness/rehearsal.py` records `_exec` by tool
name, so both calls of `factory_evidence.py` were one step: `argv.index("--architecture-verdict")`
raised `ValueError` on the currency call and took seven test files down with it, and
`fail="factory_evidence.py"` would have aimed the Evidence Bundle's own scenario at the
pre-check that now runs first. The currency call gets its own step name, exactly as
`merge_verify.py`'s two phases do, and two scenarios exercise it: one refusing for its own
reason, one refusing for a stale base.

This was found locally only because `FACTORY_WORKDIR` was set. `.factory/kernel.json`'s
`work_root` is `/tmp/dark-factory`, which is not absolute on Windows, so fourteen test files die
at `setUpClass` and a baseline taken without it reports them red on clean `main` too -- hiding
seven real regressions inside a pre-existing red. A local baseline that does not set it is worse
than none.

**Detection.** `tests/factory/test_factory_base_move.py` (36 tests) pins the budget across a
first, second and third base move, the changed-head refusal, the missing-head strict fallback,
every non-stale reason code, the budget clause answering with no refusal marker posted and
agreeing with the predicate once one is, the ordering of the currency call before both holdouts
and the certifiers, its credential scope, the dispatch passing the head, the refusal path asking
the budget rather than the old count, the early return's position between the drift check and
the contract parse, and the bundle's three questions still being asked in full. Six of those run
the whole validate path through the rehearsal: the check is a step of a healthy run, it precedes
the holdout and the bundle, refusing it pays for no judge and never merges, its own failure
names its own stage, a stale base found early is still a stale base and still re-head eligible,
and the bundle's scenario still refuses at the bundle. Seven mutations -- `rehead-cap-removed`
(re-anchored), `rehead-budget-assumes-a-head-it-was-not-given`,
`stale-base-escalation-ignores-the-budget`, `currency-check-runs-after-the-judges`,
`currency-only-falls-through-to-the-bundle`, `currency-refusal-recorded-as-the-evidence-bundle`
and `rehearsal-conflates-the-two-evidence-calls` -- each verified caught by direct injection on
the maintainer's Windows host.

## D-078 · Every detector a defect names must actually run, because a green test file that was never wired in let four defects escape

**The first run to name its escapes named these.** Validation run 34114507758 of PR #134, the
first since D-076 taught the mutation runners to print their failures by name:

```
FACTORY_MUTATIONS_TOTAL=414
FACTORY_MUTATIONS_CAUGHT=411
FACTORY_MUTATIONS_NOT_INJECTED=0
FACTORY_MUTATIONS_ESCAPED=rehead-guard-hashes-unverified,rehead-changed-guard-file-accepted,evidence-replay-compares-the-whole-file-map
```

`NOT_INJECTED=0` is D-076 holding. The three escapes were the other half of that refusal, and
before D-076 they were a bare count that cost a local re-run of the whole catalogue to identify.
Named, they took one query to diagnose: **all three `why` fields name the same detector**,
`tests/factory/test_factory_rehead_guard_files.py`.

**The file exists. It is green. It was never in `COPY_FILES`.** A mutation copy runs the test
files in `COPY_FILES` and nothing else, so the detector written for those defects has never once
run against them. It was added by D-072 together with the four defects it proves, and the copy
set was not updated with it. Three escaped silently from that day; the fourth,
`rehead-compares-the-whole-file-map`, happened to be caught by an unrelated file, which is why
the count was three and not four — and which is exactly how this stays hidden.

**This is D-076's silence one field over.** An anchor that no longer matches and a detector that
never runs both leave a defect in the catalogue with nothing behind it, and both are invisible
until a full harness run fifty minutes into a validation. A defect's `why` routinely says
"caught by tests/factory/test_x.py"; that sentence was the only record of which test is supposed
to notice the defect, and it was prose. `harness/mutation_anchors.py` now reads those references
and refuses any that is not in the suite the copies run, naming the defect, the file, and
whether the file is missing from the copy set or missing from the repository — because the fix
differs. It costs a regex over 295 `why` strings and runs in the same static rung.

The audit found exactly one such file across 24 named detectors and 96 references. Adding it to
`COPY_FILES` catches all four defects: verified by injecting each into a real `build_copy` and
running the detector there (1.1 s each). The file is hermetic under D-074 — it runs green from a
copy that is not a repository.

**Detection.** `tests/factory/test_factory_mutation_anchors.py` grows to 29 tests: a named
detector inside the suite passes, one outside it fails, a `why` that names no detector is not a
failure, a defect with no `why` is not a failure, every named detector is reported rather than
the first, one defect naming two detectors reports both, and the message distinguishes "exists
but is not in the copy set" from "does not exist".

One of them reads the REAL catalogue, which the anchor check may never do. The distinction is
load-bearing: a copy has one anchor deliberately removed, so asserting every anchor injects
would go red in all 422 copies and report every defect as caught. The detector-reference check
reads `why` strings and `TEST_FILES`, and injecting a source anchor touches neither — it answers
the same in every copy as on `main`, except in the copies that mutate the copy set itself, where
red is the correct answer. That is what makes
`rehead-guard-detector-dropped-from-the-copy-set` catchable at all.

Two mutations, `mutation-detector-reference-check-made-vacuous` and
`rehead-guard-detector-dropped-from-the-copy-set`, each verified caught by injection into a real
`build_copy` on the maintainer's Windows host, along with the four re-head defects this change
re-arms.

## D-079 · A wrapper must accept every call the kernel makes of the program it wraps, because the first production run of a new check refused a real PR for a reason that had nothing to do with it

**The check added by D-077 could not run.** Run 34121299336, the first dispatch after D-077
merged:

```
FACTORY_STAGE kind=exec name=currency seconds=0.081 outcome=refused
factory_evidence_spine.py: error: the following arguments are required:
  --verdict, --architecture-verdict, --output
```

0.081 s is the argument parser. PR #134 was refused, correctly recorded as a durable refusal,
and relabelled — for a defect in the factory, not in the pull request.

**Why the call went somewhere else.** `factory_kernel/worker_runtime.py` rewrites every
`python scripts/factory_evidence.py ...` to `scripts/factory_evidence_spine.py`. That rule is
deliberate and blanket: production CLI commands instantiate that class, so no autonomous merge
can fall back to the legacy Evidence Bundle path — the outer authority must close all protected
spine claims first. D-077 added a *second* call of that program, and the routing caught it
exactly as designed. The wrapper's argument surface had never needed to cover more than one call
shape, and nothing checked that it covered all of them.

The fix keeps the invariant rather than exempting the call from it. The wrapper learns
`--currency-only` and forwards it to the program that owns those three checks, adding nothing:
the exit code passes through, and so does the text, because `is_stale_base` classifies by the
sentence the inner program prints. A wrapper that swallowed either would turn an early stale
base into something that is not a stale base, and no re-head would follow — the refusal would sit
on the pull request waiting for a human, which is the outcome D-077 exists to prevent.

**Nothing local could have seen it.** The rehearsal records `_exec` before routing, and the
routing lives one class above the runtime the rehearsal drives. All 78 test files were green.
The gap was between two files that never met in a test.

`tests/factory/test_factory_spine_routing.py` closes it generally rather than for this one flag.
It reads the kernel's `runtime.py` with `ast`, finds every `_exec` argv literal whose program is
the wrapped one, substitutes a placeholder for each non-literal value — the shape is what is
under test, not what the kernel puts in it — and requires the wrapper's parser to accept every
one. A third call added later is covered without editing the test. `build_parser()` is split out
of `main()` so the surface can be asked without running anything.

**Detection.** Eleven tests: the routing rule is still in place, the kernel makes at least two
calls of the wrapped program, the wrapper accepts every one of them, one of them is the currency
check, both call shapes parse, a bundle call missing its arguments is still refused, the forward
goes to `factory_evidence.py` with `--currency-only` under the `github` scope, a refusal by the
forwarded program is a refusal by the wrapper, and the refused text reaches the caller so the
stale-base class survives. Three mutations — `spine-refuses-the-currency-call`,
`spine-currency-forward-swallows-the-refusal` and
`spine-currency-forward-takes-validation-credentials` — each verified caught by injection into a
real `build_copy`.

The second of those escaped the first version of its detector, which asserted the source
contained `raise SystemExit(proc.returncode)`: the mutation left the line and changed the `if`
above it. A string that is still present is not a behaviour that still happens. It is now a
behavioural test with a stubbed subprocess, and the escape is why.

## D-080 · One budget, three askers, and only two of them were changed, because a default that fails closed also hides a caller that forgot

**The pull request had no way forward.** Run 34122778543, dispatched immediately after the
currency check finally reached its program (D-079) and correctly refused PR #134 as a stale base:

```
KERNEL_DISPATCH kind=rehead-pr number=134
factory_kernel.runtime.NeedsHuman: PR #134 is not a first stale-base refusal;
  re-head is not a repair
```

The dispatcher chose a re-head under the D-077 rule. `rehead_pr` then refused that same re-head
under the pre-D-077 rule. The two disagreed about the same question, about the same pull request,
in the same process, one function call apart.

**Why D-077 missed it.** `rehead_eligible(bodies, *, head=None)` treats a missing head as the old
strict rule, deliberately: a budget that cannot check its own condition must refuse rather than
assume. That default is right, and it is also why this was silent. An asker that simply does not
pass `head` does not fail, does not warn, and does not look wrong at the call site — it quietly
applies the previous policy. D-077 updated the dispatcher and the refusal path, and both are in
`_record_validation_failure`'s neighbourhood; `rehead_pr`'s own guard is 400 lines away and was
not.

The guard now asks with the head, and its message no longer says "first": the budget has not
counted pull requests since D-077 and a refusal that describes a rule nobody applies is the same
defect one layer up.

**The check is mechanical, so it should be checked mechanically.** Every call of
`rehead_eligible` or `rehead_budget_allows` in the kernel must pass `head=`. That is an AST
question with a yes/no answer, it covers askers that do not exist yet, and it would have caught
this before the run. `tests/factory/test_factory_base_move.py` grows to 39 tests: the kernel asks
the budget from at least three places, every one of them passes a head, and the re-head's guard
no longer claims a first refusal.

**What this run also proved.** The two changes it was blocking both work. The currency check
refused in **0.539 s** at the `currency` stage, before `provenance-fetch` and before any judge,
and its refusal classified as `stale_base` with the authority `base moved under the PR
(model-free re-head)`. The five preceding runs of the same PR each spent 869 s and about $1.64 of
blinded judgement to reach the same conclusion.

**Detection.** Two mutations, `rehead-guard-asks-the-budget-without-a-head` and
`dispatcher-asks-the-budget-without-a-head`, each verified caught by injection into a real
`build_copy`. Both express the same defect from opposite ends, which is the point: the property
is that the askers agree, not that any one of them is right.
