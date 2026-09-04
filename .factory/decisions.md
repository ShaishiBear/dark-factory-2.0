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
