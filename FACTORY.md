# Dark Factory

**Autonomy level: 4.** A human supplies issues; the factory can triage, build, validate and merge them without a human reading the product diff. The factory does **not** invent its own roadmap or write its own issues. Level 5 is deliberately out of scope.

The orchestration authority is this repository. Archon is no longer a runtime dependency.

## Control plane

The canonical entrypoint is:

```bash
python -m factory_kernel dispatch --once
```

The canonical unattended scheduler is `.github/workflows/dark-factory-worker.yml`. It invokes one dispatch at minute 17 of every hour and also supports manual `workflow_dispatch`. The workflow is deliberately thin: all dispatch semantics live in `factory_kernel/` and protected policy in `.factory/`. The checked-in `deploy/systemd/dark-factory.service` + `dark-factory.timer` are an optional self-hosted scheduling alternative; they do not contain independent workflow policy.

One dispatch cycle is deterministic in this order:

```text
emergency stop
  ↓
stale-lease reaper
  ↓
oldest PR carrying factory:needs-review
  ↓ otherwise
oldest accepted issue without factory:in-progress
  ↓ otherwise
bounded triage batch
  ↓ otherwise
idle
```

PR validation deliberately has priority over starting new build work.

## Emergency stop

`scripts/factory-stop.sh` remains the two-channel fail-closed stop authority and is called by the Python kernel before dispatch and again immediately before merge.

1. Local: `${FACTORY_WORKDIR}/.factory-stop`. This works even if the network is down.
2. Remote: any open issue carrying `factory:stop`.

If the GitHub stop state cannot be read, the factory stops. An unreadable stop button is not treated as permission to continue.

## What the kernel owns

| Area | Authority |
|---|---|
| Runtime policy | `.factory/kernel.json` |
| Worker prompts | `.factory/prompts/*.md` |
| Engineering methods injected per role (pinned text; workers load no plugins) | `.factory/methods/manifest.json`, `factory_kernel/methods.py` |
| Lifecycle (the only executable lifecycle definition) | `.factory/evidence-spine.json` `required_claims`, closed in order by `scripts/factory_evidence_spine.py`, sequence enforced by `harness/merge_verify.py` |
| Dispatch/build/validate/merge orchestration | `factory_kernel/runtime.py` |
| Bounded triage + flood control | `factory_kernel/triage.py` |
| Model worker boundary | `factory_kernel/agents.py`, `factory_kernel/providers.py` |
| Git/GitHub adapter | `factory_kernel/worktree.py`, `factory_kernel/github_cli.py` |
| Provenance manifest | `factory_kernel/manifest.py` |
| Evidence-spine policy/compiler | `.factory/evidence-spine.json`, `factory_kernel/spine.py` |
| Deterministic engineering authorities | `scripts/factory_*.py`, `harness/` |
| Canonical unattended scheduler | `.github/workflows/dark-factory-worker.yml` |
| Optional self-hosted scheduler | `deploy/systemd/dark-factory.*` |
| PR quick authority (runs from the PR head) | `.github/workflows/dark-factory-ci.yml` |
| Trust-root authority + unattended merge (runs from the base) | `.github/workflows/dark-factory-trust-root.yml` |
| Daily full-harness regression on `main` | `.github/workflows/dark-factory-main-regression.yml` |

The model provider is replaceable. The checked-in default is the Claude Code CLI. Provider output is untrusted reasoning; it never directly authorizes a merge.

There is exactly one lifecycle definition and it is executable. The ordered `required_claims` in `.factory/evidence-spine.json` are what a PR must close, one artifact per claim, before it may merge; `scripts/factory_evidence_spine.py` closes them and `harness/merge_verify.py pre` refuses any spine that is not at 100 percent with the exact required claim sequence. Control-plane states outside that sequence (in progress, needs review, needs fix, needs human, stopped) are GitHub labels applied and read by `factory_kernel/runtime.py`. An earlier `factory_kernel/state.py` described an abstract stage machine that nothing executed; it was retired on 2026-09-04 (`.factory/decisions.md` D-006) because a second representation of the lifecycle that the runtime does not consume is a claim, not an authority.

## Build path

For an accepted issue the kernel creates a dedicated exact-SHA Git worktree and a fresh branch from `origin/main`. Model stages run as separate CLI processes rather than one long hidden session.

```text
issue
 ↓
plan OR investigate
 ↓ bugs: kernel executes the proposed repro (test-runner shape, allowlisted env, unchanged tree); must fail with the named symptom
raw execution contract
 ↓ deterministic factory_protocol.py
validated contract
 ↓
context + design
 ↓ deterministic context/design compilation
architecture governor
 ↓ deterministic factory_architecture.py
independent acceptance-test author
 ↓ deterministic RED replay/proof
implementation worker
 ↓ deterministic GREEN replay/proof
fresh two-axis code review (Spec and Standards, separate processes, deterministic aggregator)
 ↓ optional one fresh-context repair + GREEN replay
architecture conformance
 ↓ deterministic conformance compiler
final GREEN proof
 ↓
quick gate
 ↓
push + PR + attached canonical contract/proof
 ↓
factory:needs-review
```

The acceptance tests recorded in RED are immutable during implementation and repair. The deterministic GREEN authority re-hashes and replays them.

## Validation and merge path

Validation starts from the exact GitHub PR head in a separate worktree. The validator does not trust the builder's narrative or prior review discussion.

```text
exact PR head
 ↓
deterministic security/dependency guard (base-anchored: runs from the kernel's main checkout, reads the head as data)
 ↓
blinded holdout outside the source checkout
 ↓
independent architecture holdout
 ↓
Evidence Bundle v5
   ├─ attached contract is revalidated
   ├─ attached final proof is revalidated
   ├─ RED is reconstructed and replayed independently
   ├─ GREEN is replayed independently
   ├─ architecture policy/bindings are recomputed
   ├─ deterministic security result must pass
   └─ full canonical harness must reach GATE_OK mode=full
 ↓
exact-head/tree merge pre-authorization
 ↓
second emergency-stop check
 ↓
squash merge with expected head SHA
 ↓
post-merge exact-tree verification on origin/main
```

The model never executes the final logical decision “this evidence permits merge”. `scripts/factory_evidence.py` and `harness/merge_verify.py` do.

## Trust-root authority and unattended merge

Two questions are decided for every PR, and they are decided by different programs running from different commits.

**What runs from the trusted base.** `.github/workflows/dark-factory-trust-root.yml` is a `pull_request_target` workflow: GitHub executes the workflow definition that is on `main`, not the PR's copy. Its `trust-root-authority` job checks out `github.sha` (the base tip), asserts that it is not the PR head and is on `origin/main`, fetches the PR head as a Git object without checking it out, and runs `scripts/factory_security.py --pr N --trusted-base --expect-base <base> --expect-head <head>`. In that mode the guard refuses to run from the PR head, refuses if the base or head differ from what the event promised, refuses if `refs/pull/N/head` is not the head GitHub reports, judges the diff, and emits a verdict bound to repository, PR, base SHA, head SHA, changed paths and lane. The kernel's own validator (`KernelRuntime.validate_pr`) runs the same mode from its `main` checkout before any model sees the PR. A PR that rewrites the guard, or the workflow that invokes it, is judged by the copies on `main`; its own copies are data in a diff.

**What runs from the PR head.** `.github/workflows/dark-factory-ci.yml` (`quick-authority`) checks out the exact head and runs config validation, locked dependency installs, the guard again as defence in depth, and `harness/ci.py --quick`. That is where proposed code executes. Nothing it produces can grant trust-root permission.

**What grants trust-root permission.** The base-run guard's lane: a PR opened by a GitHub user account holding a repository role (OWNER, MEMBER, COLLABORATOR) whose commits all resolve to user accounts is `human-maintenance` and may change protected paths. A PR opened by a Bot, by a user without a role, or with unresolved or Bot commits is `autonomous` and fails on any protected path. Identity is what GitHub reports about the PR, never commit text. A Claude Code session acting through a maintainer's GitHub credentials appears as that maintainer's delegated authority; the lane proves that the PR was opened under a maintainer's account, not that a human typed each commit.

**What grants merge permission.** The `main-protection` ruleset, which requires a pull request, linear history, and both `trust-root-authority` and `quick-authority` green on the exact head being merged, with no bypass actor. Nothing in this repository can merge around it. For autonomous PRs the kernel adds the whole evidence ladder above and `harness/merge_verify.py pre` before it will even attempt a merge.

**Who performs the merge.**
- Maintainer-lane PR: the `unattended-merge` job (runs only when the trusted verdict is `pass`, the lane is `human-maintenance`, and the emergency stop is clear) executes one GraphQL mutation with the Actions token and no checkout: `enablePullRequestAutoMerge(expectedHeadOid: <judged head>, mergeMethod: SQUASH)`. GitHub then squash-merges the PR itself once every required check is green on that head. If the head moved, the mutation is refused; a new push re-runs the workflow, which re-arms against the new head or leaves it unarmed if the new head fails. The owner does not press Merge.
- Autonomous PR: never armed. The kernel merges with `gh pr merge --squash --match-head-commit <evidenced head>` after re-reading the head and the stop button, then verifies the merged tree byte-for-byte.

**What remains impossible for the autonomous factory.** Its workers have no Git and no Bash; the kernel commits only inside the acceptance-test and design envelopes, which exclude every protected path; its PRs are opened by a Bot and land on the autonomous lane, where the base-run guard refuses any protected path; and even a passing autonomous PR is never armed for auto-merge, because eligibility is computed as `lane == human-maintenance and verdict == pass` inside the guard that runs from `main`. The factory cannot rewrite its judge, cannot run its judge from its own copy, and cannot merge without the judge.

**If any authority fails.** A failed `trust-root-authority` or `quick-authority` is a red required check: the ruleset refuses every merge of that head, by anyone. The unattended-merge job is skipped whenever the trusted verdict is not `pass`; if auto-merge was armed for an earlier head, the red check on the new head still blocks it. A failed autonomous validation records `factory:needs-fix` with a typed `reason_code` in a PR-comment marker and a scrubbed `validation-refusal.json` in the run's artifacts (`factory_kernel/refusal.py`); the issue is rebuilt, except for `stale_base`, where the kernel instead re-heads the certified branch onto current `main` without a model (`KernelRuntime.rehead_pr`: blinded worktree, rebase, RED files byte-identical, GREEN and conformance replayed, `--force-with-lease` on the judged head, provenance republished, back to `factory:needs-review`; once per PR). A failed post-merge verification pulls the stop button.

**Conflicts are visible, not silent.** A PR that conflicts with `main` gets no `pull_request` run, so the head-based quick gate never reports. The base-run `trust-root-authority` job asks GitHub for `mergeable_state` after judging the diff and fails the required check on `dirty` with `TRUST_ROOT_REFUSED pr is not mergeable (conflicting with base); rebase`; `unknown` is retried and then tolerated. A red required check is what the ruleset and a human both see; a rebase re-runs everything.

**Exact-head binding.** The verdict names the head it judged; the merge job refuses if the judged head differs from the event head; GitHub refuses to arm auto-merge if the PR head differs from `expectedHeadOid`; required checks are per commit, so a head pushed after the checks ran has no green checks and cannot merge until it is judged in turn; and the kernel's merge passes `--match-head-commit`. There is no window in which a head other than the one that was checked can be the one that merges.

**Bootstrap (done 2026-09-04).** A `pull_request_target` workflow runs only once it exists on `main`. PR #40, which introduced this workflow, could not be judged by it, so a delegated maintainer session merged its exact head `3ddf0b0` with `gh pr merge --squash --match-head-commit` under the owner's account, producing `dfa3d96` with a tree byte-identical to the judged head. `trust-root-authority` was added to the ruleset's required checks immediately afterwards, with the bypass list still empty. Every later PR merges without a click; the first to do so was the PR that added this sentence.

## Canonical harness

Quick developer/PR gate:

```bash
python harness/ci.py --quick
```

Full merge-authority gate:

```bash
python harness/ci.py
```

The full gate includes static analysis, unit tests, real browser E2E, protected holdouts, application mutations, factory trust-root mutations, immunity/ratchet checks and their counted positive markers. Environment-dependent E2E prerequisites remain explicit; missing credentials or browser infrastructure are a failed prerequisite, never a silent skip.

## Triage

When there is no PR to validate and no accepted issue to build, `factory_kernel.triage.TriageEngine` handles a bounded batch. It preserves the previous anti-flood rule: non-owner accounts are capped at three issues per UTC day. Blocked issues are filtered through `scripts/frontier_filter.py` before a model sees the batch.

The triage worker returns only `accept` or `reject`. The kernel validates that there is exactly one well-formed decision for every candidate before applying any mutation to GitHub.

## Stale work recovery

`scripts/factory_lease.py` remains the canonical lease/heartbeat format. Every dispatch runs its reaper immediately after the stop check and before choosing new work. A linked PR carrying the handoff label wins over redispatching the original issue.

No external `/opt/.../orchestrator.sh` contains hidden policy anymore.

## Protected trust root

Ordinary autonomous product PRs may not modify the machinery that judges them. `scripts/factory_security.py` blocks changes to, among other things:

- `factory_kernel/`
- `.factory/kernel.json`
- `.factory/evidence-spine.json`
- `.factory/prompts/`
- `.factory/methods/`
- `.factory/holdout/`
- `harness/`
- `scripts/factory_*`
- `.github/`
- `deploy/systemd/`
- architecture and ratchet policy
- environment/deployment-secret surfaces

Changes to that set are human-reviewed trust-root work, like the rewrite that introduced this runtime.

The holdout programs under `.factory/holdout/` are additionally **absent from every build worktree**: `build_issue` creates the worktree as a sparse checkout excluding `BUILDER_BLIND_PATHS` (`factory_kernel/worker_policy.py`), and `factory_kernel/worktree.py` refuses the worktree if the blind did not take. The validator worktree is never blinded; the full harness runs the holdout there.

## Archon history

The earlier experiment used Archon YAML workflows and command files. Active `.archon/workflows/dark-factory-*.yaml` files have been removed. Historical benchmark material and legacy prompt sources may remain temporarily for provenance/comparison, but the kernel does not load or execute them. `THIRD_PARTY_NOTICES.md` keeps the Archon MIT attribution for ideas/code reviewed during the migration.

## Operations

### Canonical GitHub-hosted worker

Before unattended Level-4 dispatch is enabled, repository configuration must satisfy the same fail-closed preflight enforced by `.github/workflows/dark-factory-worker.yml`:

- GitHub Issues are enabled. Issues are the intake, state and remote emergency-stop surface.
- `main` is protected by GitHub branch protection/rules so a direct push cannot bypass the in-repo evidence and exact-tree merge authority. The `main-protection` ruleset requires a pull request, linear history, and the status checks `quick-authority` and `trust-root-authority`, with an empty bypass list. The repository setting **Allow auto-merge** is on; without it the unattended-merge job cannot arm a merge and maintainer PRs would wait for a click.
- every label the kernel can apply exists: the eight `factory:*` control labels from `.factory/kernel.json` (`factory:accepted`, `factory:rejected`, `factory:rate-limited`, `factory:in-progress`, `factory:needs-review`, `factory:needs-fix`, `factory:needs-human`, `factory:stop`) plus the `priority:{critical,high,medium,low}` and `type:{bug,enhancement,chore,docs}` labels triage attaches on accept. The preflight reads that list from `factory_kernel.triage.label_vocabulary` so the code cannot outrun the check.
- GitHub Actions is allowed to create pull requests (Settings > Actions > General > Workflow permissions > "Allow GitHub Actions to create and approve pull requests"). The kernel opens every product PR with the Actions token; with the setting off, `gh pr create` fails after the whole build has succeeded (D-022). The preflight reads `actions/permissions/workflow.can_approve_pull_request_reviews`: an explicit `false` refuses the run; `true` passes; a token that cannot read repository settings (the default `GITHUB_TOKEN` lacks `administration:read`) prints `FACTORY_PREFLIGHT_PR_PERMISSION_UNVERIFIED` and continues, so the requirement is documented here rather than only proven at run time.
- repository Actions secrets `OPENROUTER_API_KEY` and `SUPADATA_API_KEY` are configured. Model
  calls are routed to OpenRouter's Anthropic-compatible Messages endpoint, so no separate
  Anthropic credential is required. `ANTHROPIC_BASE_URL` is `https://openrouter.ai/api` (the SDK
  appends `/v1/messages`; a versioned base doubles the segment, D-010). Before dispatching, the
  preflight runs the pinned Claude Code CLI exactly as the kernel launches a worker against
  every configured model and refuses the run unless the CLI itself returns a non-error result;
  the raw curl probe is only an earlier, cheaper signal.

The worker checks out current `main` without persisting checkout credentials, runs one global dispatch at a time, and refuses to start if any prerequisite above is missing.

Application validation state is disposable per run rather than a persistent secret surface. The worker provisions local `postgres:16` database `dark_factory_validation`, a random JWT secret, a synthetic E2E account/password, and `DARK_FACTORY_E2E_BOOTSTRAP=1`. The locked browser fixture is ingested through the real Supadata/OpenRouter application path.

The GitHub-hosted toolchain is pinned to Ubuntu 24.04, Python 3.12.14, Node 24, uv 0.12.5, Bun 1.4.0, Claude Code 2.1.245 and agent-browser 0.35.0. The uv wheel cache and the Bun package store are cached between runs keyed on the lockfiles; every install still runs frozen against the lockfile, so the cache only avoids re-downloading hash-verified artifacts.

### Run transcripts and per-stage timing

Every worker is launched with a per-role turn cap (`ROLE_MAX_TURNS`, `factory_kernel/worker_policy.py`) and `--output-format stream-json --verbose`, so the CLI prints one JSON event per line as the session runs and ends with a `result` event carrying `num_turns`, `duration_ms` and cost (the same fields the single json envelope carried); the kernel unwraps that event and refuses error results as failed stages. Each run writes `transcripts/agent-<role>.log` (the whole stream-json transcript of every attempt, appended as it arrives, D-055), `transcripts/agent-<role>.json` (its telemetry and wall time) and `transcripts/stage-timings.jsonl` (one row per model stage and per deterministic gate that keeps a transcript). A stage the kernel runs again in the same run (the static-gate hand-back re-runs `test_author`; the review path can run `repair` twice) writes `agent-<role>.2.log` and `agent-<role>.2.json`, then `.3`, never over the first run's files; each record and timing row says which run it is (`stage_run`) and how many CLI processes the stage took (`attempts`; `wall_seconds` spans all of them, so it can exceed the role's per-process wall), and a stage that hung once and completed on the retry is flagged `hang` with the count in `hangs` (D-058). Every model stage goes through one funnel (`KernelRuntime._agent_stage`), so the validator's blinded holdout, architecture holdout and three pre-code certifiers are recorded exactly like build workers; a stage with no record is a stage that never ran. As each stage ends the kernel also prints one flushed line to stdout, `FACTORY_STAGE kind=agent|exec name=<role or gate> [stage_run=<n>] seconds=<n> [attempts=<n>] [turns=<n>] [cost_usd=<x>] outcome=ok|failed|refused [events=<n>] [timed_out=true] [hang=true] [over_budget=true] [thinking=<n>] [effort=<level>]`, so a run's progress is readable in the Actions log while it happens. `over_budget` marks a model stage whose wall clock exceeded its turn cap at the per-turn ceiling (`worker_policy.stage_budget_seconds`); it is data for tuning the caps and changes nothing (D-050). The worker workflow uploads those, the gate logs and the run's JSON artifacts as a 7-day artifact, on success and on failure; logs of programs that hold credentials are excluded. This is observability only; nothing reads it to decide anything (D-020).

A worker's timeout is measured on its event stream. The kernel reads the CLI's stdout line by line as the process runs (`system`/`init`, `assistant` messages, `user` tool results, then the final `result`), and two clocks bound every process. The **wall** is the role's own: `worker_policy.stage_timeout_seconds(role) = ceil(max_turns × OBSERVED_SECONDS_PER_TURN_CEILING × 1.5)`, 2025 s for a 30-turn role and 675 s for a 10-turn judge, carried on the `AgentRequest` and required by the `_agent_stage` funnel like the other three bounds; `provider.timeout_seconds` in `kernel.json` (2700) is the maximum every wall must fit under (`assert_caps_fit_timeout`), not a wall itself. The **idle** clock, `provider.idle_timeout_seconds` (420), kills a process that has printed no event for that long: a working CLI prints an event per model turn and per tool call, so a longer silence is a hang, not a slow turn. A hang is retried once through the transient path (the worktree is restored first; a second hang is terminal); a wall timeout is terminal. Either way the stage record, its timing row and its `FACTORY_STAGE` line carry what the stream had shown: `turns`, `events`, tokens, `last_event_age_s`, the last event lines as `partial_output`, and `timed_out=true` or `hang=true`, so a stage that dies at its wall is not the empty record `test_author` of run 33987381035 left; the cost is reported only when a `result` event stated it. The ceiling (45 s/turn) is stated from that run's four measured stages, and `over_budget` still means the wall clock exceeded `max_turns × ceiling` (D-054).

Every worker runs at a stated effort, and every stage keeps its whole transcript. Turns bound iterations and dollars bound the resent conversation; neither bounds how long one turn thinks, and the `test_author` of build run 33992451400 (issue #103) thought for 2025 s and 76,248 stream events over 14 turns of a three-criterion design because the kernel named no level and the CLI's default is `high`. `worker_policy.ROLE_EFFORT` gives every role a level from the pinned CLI's scale (`low`, `medium`, `high`, `xhigh`, `max`): workers (every role that edits or drafts against a checkout, the three mutation roles included) at `medium`, one notch below the default so thinking is bounded but not disabled; judges (the five validation authorities and triage, tool-less single-prompt calls where reasoning is the whole job) at `high`. The provider renders it as `--effort <level>` on every launch, `provider.effort_overrides` in `kernel.json` (`{role: level}`, validated at load against the policy's roles and the CLI's levels) overrides it per deployment, and the `_agent_stage` funnel requires it as the fifth bound. Whether the OpenRouter route honours the level for a non-Anthropic model is measured, not assumed: the worker workflow's preflight runs `scripts/factory_effort_probe.py`, the worker model twice on one fixed reasoning prompt at the policy's lowest and highest level, one turn each, and prints `FACTORY_PREFLIGHT_EFFORT_PROBE model=<slug> low_thinking=<n> high_thinking=<m> honoured=true|false ...`; the line is data for tuning the levels and never a gate. The provider also tees every stdout line of every attempt to `transcripts/agent-<role>.log` as it arrives, under an `--- attempt N role=<role> started=<utc> ---` header and over an end marker saying how the process ended, so a stage killed at its wall or its idle clock leaves its whole stream where run 33992451400 left 1500 characters of tail and no log at all; the record still keeps that tail as `partial_output` for quick reading, and a provider that does not stream (the rehearsal fakes) still gets the worker's text written there afterwards. The thinking the stream showed (`providers.thinking_tokens`: the CLI's `thinking_tokens` events, summed as the high-water mark of every monotone run of `estimated_tokens`, so the count is right whether the counter restarts each turn or never) is recorded as `thinking_tokens` in `agent-<role>.json`, in the timing row, and on the stage line as `thinking=<n>`, beside `effort=<level>` (D-055).

A builder reads only its product tree, and must draft before its turns run out. The fourth build of issue #103 (run 34002520477) died in `test_author` at its 30-turn cap after 46 Read calls and no Write: kernel source, the harness, biome and tsconfig, and an attempt to "verify the kernel's deferred-repro check"; issue #49's author (run 33999901008) wrote at turn ~5 of 15. Every tool-bearing role now carries a `PathScope` from `worker_policy.ROLE_PATH_SCOPE` on its `AgentRequest`, and the provider renders it as the CLI's permission rules instead of bare tool names: `Read(./app/**)`-style allow rules for the product tree (`app/`, `docs/`, the root docs) and `Edit(./app/**)` for what a mutation role may write, `Read`/`Edit` rules for the run's artifacts directory as an absolute pattern, and `Read`/`Edit` deny rules for the trust root (`factory_kernel/`, `harness/`, `scripts/`, `tests/factory/`, `.github/`, the protected `.factory/` files) in `--disallowedTools`. The CLI reads any file inside its working directories without a rule, so the deny list is what keeps the trust root out of reach, and it needs an allow rule to write under `--permission-mode dontAsk`, so the `Edit` list is what lets a worker write product files; `.factory/architecture.json` stays readable to the three roles whose prompts name it. The `_agent_stage` funnel refuses a mutation-role request without a scope, and the worker preflight proves the enforcement on the runner with the exact argv the kernel renders (`scripts/factory_read_scope_probe.py`, `FACTORY_PREFLIGHT_READ_SCOPE_PROBE ... denied_outside_scope=true|false`), refusing the run only when the trust-root file's contents came back. Beside the scope, the three mutation roles have a draft deadline: `draft_deadline_turn(role) = ceil(cap × 0.8)` (turn 24 of 30; 0.6 until D-063) is rendered into their prompts as `$DRAFT_DEADLINE_TURN`, the provider's reader counts turns as distinct assistant message ids as it streams, and a turn past the deadline that begins with no Write/Edit tool_use seen kills the process; the stage is refused as `no_draft_by_turn` (`DraftDeadlineMissed`, never retried) and its record, its `FACTORY_STAGE ... draft_deadline_missed=true reads=N` line and the issue's needs-human comment carry the Read calls it made and the paths it read (capped at 40) (D-057).

The route is probed for a thinking budget, and a worker can carry one per role. Six builds of issue #103 died or overran in `test_author` (runs 33987381035 through 34013852733; the sixth: 2025 s, 15 turns, 81,231 events, 121,065 thinking tokens at `--effort medium`, against 15-46 s per turn for the other stages of the same build), and the effort probe read honoured=false, true, true, false on the same route from run to run, so `--effort` is not a bound on OpenRouter's Anthropic-compatible route to this model. The CLI's thinking budget, `MAX_THINKING_TOKENS` in its environment (above zero `thinking: {type: "enabled", budget_tokens: N}`, N raised to 1024 if lower; exactly zero `thinking: {type: "disabled"}`; unset adaptive), is the lever not yet tried, and it is measured before it is used: the worker workflow's preflight runs `scripts/factory_thinking_cap_probe.py` right after the effort probe, the worker model three times on the effort probe's prompt, one turn each, uncapped, at `MAX_THINKING_TOKENS=1024` and at `MAX_THINKING_TOKENS=0`, and prints `FACTORY_PREFLIGHT_THINKING_CAP_PROBE model=<slug> uncapped=<n> cap1024=<m> cap0=<k> honoured=true|false cap1024_honoured=... cap0_honoured=... ...`; a cap is honoured when its run stayed within 1.5× the cap and clearly below the uncapped run, and the line is data, never a gate. `worker_policy.ROLE_THINKING_CAP` has a row for every role, every row `None` until that line says the budget is honoured; the provider's `environment_for` exports the variable to the CLI's environment only for a role whose cap is set, the runner's own value never reaches a worker, and `provider.thinking_cap_overrides` in `kernel.json` (`{role: cap}`, 0 or at least 1024, validated at load) is the per-deployment override (D-059).

A role can run on its own model, and the route probe covers it. Six builds of issue #103 died in `test_author` on the workhorse model over a route that honours neither `--effort` (D-055) nor `MAX_THINKING_TOKENS` (D-059) for it, and that one role reasons 5-10x more per turn there than any other stage; the remaining lever is the model per role. `provider.model_overrides` in `kernel.json` is `{role: model_slug}`, validated at load against the policy's roles (`worker_policy.ROLE_MAX_TURNS`) and a non-empty slug, checked in empty. The provider resolves every request's model in one order: the request's own explicit model (the kernel's own requests name none), else the override row, else `architecture_model` for the architecture holdout, else `model`; the resolved slug is written to `agent-<role>.json` and the timing row and printed on the stage line as `model=<slug>`, for a stage that returned and one that died alike. To route a role to another model, set its row (`"model_overrides": {"test_author": "<slug>"}`) and nothing else: the worker workflow's preflight reads every distinct model a run can use from the same policy (`scripts/factory_models.py --list`: the worker model, the architecture holdout's, every override value, each once), runs the pinned CLI once against each, prints `FACTORY_PREFLIGHT_MODEL_ROUTE_OK model=<slug>` per model and refuses the run if any is unreachable, so a role's new route is proved before any stage spends a budget on it (D-061).

A turn cap ends a mutation worker's loop, and the gates judge its draft; and the worker has the tools its policy names. Build run 34033360798 (issue #103) ended `test_author` at its 30-turn cap (`error_max_turns`, 289 s, $2.63) with `ChatArea.test.tsx` written and edited on disk, and the kernel failed the build on the CLI's exit code without the static gate, the commit authority or RED ever seeing the file; the same transcript shows the model calling Glob (and Bash) and being told `No such tool available`, though the policy granted Read, Glob, Grep, Write and Edit. Two causes, two changes (D-065). First, `--bare` sets `CLAUDE_CODE_SIMPLE=1`, and in that mode the pinned CLI registers only Read and Edit whatever `--tools` says; the provider now launches every worker with `--safe-mode --setting-sources ""` instead (the same isolation: no CLAUDE.md, skills, plugins, hooks, MCP servers or settings file; built-in tools and permission rules work as documented; both measured on 2.1.245), the three mutation prompts name the five tools in one sentence and say there is no shell and no test runner, and the read-scope preflight also asks for a Grep over the tree and a Glob for the probe files and prints `grep_denied_outside_scope=`, `glob_outside_scope=`, `tools=` and `tools_missing=` beside its old fields (the `Read` deny rules reach Grep and Glob on 2.1.245: a tree-wide Grep omits the trust-root line, a Grep into the trust root is refused by name, a Glob omits the path). Second, for `test_author`, `implement` and `repair` only, an `error_max_turns` envelope is returned by the provider marked `cap_reached` instead of raised; the stage record, its timing row and its `FACTORY_STAGE` line say `cap_reached=true` (`outcome=ok` there says the worker returned), and the kernel runs exactly the gates a returned worker gets on what is in the checkout: the scoped static gate with its one hand-back, the commit authority's D-064 rules, then RED or GREEN. A checkout with no change is refused as `no_draft_at_cap`; a test author's dirty checkout without a usable `test-spec.json` (absent, malformed, or declaring a file not on disk) as `no_spec_at_cap`, naming the files it wrote; and when a gate refuses a capped draft the needs-human comment says the cap was reached. Every other role's cap, and every budget stop, is the failed stage it was. The caps do not change.

### Daily regression on `main`

`.github/workflows/dark-factory-main-regression.yml` runs the full canonical harness against current `main` once a day (03:41 UTC) and on manual dispatch, with the worker's pins, postgres service and disposable validation environment copied verbatim (`tests/factory/test_factory_workflow_hygiene.py` asserts they agree). It holds `contents: read` and `issues: write` only. Success prints `MAIN_REGRESSION_OK head=<sha>`. Failure files one `priority:high` / `type:bug` issue for ordinary triage, comments on an existing one instead of duplicating, and adds `factory:needs-human` on the second consecutive failure. It never merges and never applies any other `factory:*` label.

### Merged-branch cleanup

GitHub's `delete_branch_on_merge` setting does not delete branches merged by GitHub's own auto-merge on behalf of the Actions app, and a `closed`-event job cannot see that close either: events caused by `GITHUB_TOKEN` start no workflows (D-020). `.github/workflows/dark-factory-branch-cleanup.yml` therefore runs hourly (and on dispatch) with `contents: write` and no checkout: it lists this repository's `human/*` and `factory/*` branches and deletes a branch only when a merged PR from this repository has it as head and the branch tip is exactly that PR's head commit. `main` is never a candidate; a branch with commits past its merged PR is kept and reported. Drafts are judged by the trust-root workflow but never armed for auto-merge.

The uploaded transcripts artifact records every model stage, including one that failed: a failed stage's `agent-<role>.json` carries `outcome: failed`, the scrubbed error, the attempt count and whatever the provider had already spent (D-041).

### Optional self-hosted scheduler

The systemd files remain available when an operator deliberately chooses a self-hosted scheduler. Expected layout for those checked-in units is:

```text
/opt/dark-factory/repo        repository checkout
/opt/dark-factory/factory.env optional non-secret runtime overrides/CLI environment
/opt/dark-factory/.factory-stop emergency local kill file
```

A self-hosted service account needs authenticated `gh`, the configured model CLI, Git push rights to the repository, and the validation environment required by the full harness. Secrets stay on the host; they are not committed into `.factory/kernel.json`.

Useful commands:

```bash
python -m factory_kernel config-check
python -m factory_kernel stop-check
python -m factory_kernel reap
python -m factory_kernel triage
python -m factory_kernel build --issue 123
python -m factory_kernel validate --pr 456 --no-merge
python -m factory_kernel dispatch --once
```

`--no-merge` is the controlled validation mode. Production dispatch omits it only when the full Level-4 autonomous loop is intentionally enabled.

### Resuming a build that died after opening its PR

A build that pushed its branch and opened its PR, then failed before provenance publish and handoff, is finished from the artifacts that run uploaded, not rebuilt (`KernelRuntime.resume_pr`, FACTORY_RULES §7). The canonical worker takes the two dispatch inputs and does it on GitHub-hosted infrastructure, with the same toolchain, credentials and validation environment as a dispatch:

```bash
gh workflow run dark-factory-worker.yml -f resume_pr=<PR> -f resume_run_id=<run id of the build>
```

The run downloads that run's artifact (`actions: read`), requires exactly one build inside it, runs `python -m factory_kernel resume` in place of `dispatch --once` (never both), and then the hourly dispatch validates the PR like any other. One input without the other refuses at preflight.
