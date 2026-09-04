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

**If any authority fails.** A failed `trust-root-authority` or `quick-authority` is a red required check: the ruleset refuses every merge of that head, by anyone. The unattended-merge job is skipped whenever the trusted verdict is not `pass`; if auto-merge was armed for an earlier head, the red check on the new head still blocks it. A failed autonomous validation records `factory:needs-fix` and the issue is rebuilt; a failed post-merge verification pulls the stop button.

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

Every worker is launched with a per-role turn cap (`ROLE_MAX_TURNS`, `factory_kernel/worker_policy.py`) and `--output-format json`, so the CLI returns one result envelope with `num_turns`, `duration_ms` and cost; the kernel unwraps it and refuses error envelopes as failed stages. Each run writes `transcripts/agent-<role>.log` (the worker's text), `transcripts/agent-<role>.json` (its telemetry and wall time) and `transcripts/stage-timings.jsonl` (one row per model stage and per deterministic gate that keeps a transcript). The worker workflow uploads those, the gate logs and the run's JSON artifacts as a 7-day artifact, on success and on failure; logs of programs that hold credentials are excluded. This is observability only; nothing reads it to decide anything (D-020).

### Daily regression on `main`

`.github/workflows/dark-factory-main-regression.yml` runs the full canonical harness against current `main` once a day (03:41 UTC) and on manual dispatch, with the worker's pins, postgres service and disposable validation environment copied verbatim (`tests/factory/test_factory_workflow_hygiene.py` asserts they agree). It holds `contents: read` and `issues: write` only. Success prints `MAIN_REGRESSION_OK head=<sha>`. Failure files one `priority:high` / `type:bug` issue for ordinary triage, comments on an existing one instead of duplicating, and adds `factory:needs-human` on the second consecutive failure. It never merges and never applies any other `factory:*` label.

### Merged-branch cleanup

GitHub's `delete_branch_on_merge` setting does not delete branches merged by GitHub's own auto-merge on behalf of the Actions app, and a `closed`-event job cannot see that close either: events caused by `GITHUB_TOKEN` start no workflows (D-020). `.github/workflows/dark-factory-branch-cleanup.yml` therefore runs hourly (and on dispatch) with `contents: write` and no checkout: it lists this repository's `human/*` and `factory/*` branches and deletes a branch only when a merged PR from this repository has it as head and the branch tip is exactly that PR's head commit. `main` is never a candidate; a branch with commits past its merged PR is kept and reported. Drafts are judged by the trust-root workflow but never armed for auto-merge.

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
