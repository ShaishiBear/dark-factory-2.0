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
| State machine | `factory_kernel/state.py` |
| Dispatch/build/validate/merge orchestration | `factory_kernel/runtime.py` |
| Bounded triage + flood control | `factory_kernel/triage.py` |
| Model worker boundary | `factory_kernel/agents.py`, `factory_kernel/providers.py` |
| Git/GitHub adapter | `factory_kernel/worktree.py`, `factory_kernel/github_cli.py` |
| Provenance manifest | `factory_kernel/manifest.py` |
| Evidence-spine policy/compiler | `.factory/evidence-spine.json`, `factory_kernel/spine.py` |
| Deterministic engineering authorities | `scripts/factory_*.py`, `harness/` |
| Canonical unattended scheduler | `.github/workflows/dark-factory-worker.yml` |
| Optional self-hosted scheduler | `deploy/systemd/dark-factory.*` |
| PR quick authority | `.github/workflows/dark-factory-ci.yml` |

The model provider is replaceable. The checked-in default is the Claude Code CLI. Provider output is untrusted reasoning; it never directly authorizes a merge.

## Build path

For an accepted issue the kernel creates a dedicated exact-SHA Git worktree and a fresh branch from `origin/main`. Model stages run as separate CLI processes rather than one long hidden session.

```text
issue
 ↓
plan OR reproduce/investigate
 ↓
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
fresh code review
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
deterministic security/dependency guard
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
- `.factory/holdout/`
- `harness/`
- `scripts/factory_*`
- `.github/`
- `deploy/systemd/`
- architecture and ratchet policy
- environment/deployment-secret surfaces

Changes to that set are human-reviewed trust-root work, like the rewrite that introduced this runtime.

## Archon history

The earlier experiment used Archon YAML workflows and command files. Active `.archon/workflows/dark-factory-*.yaml` files have been removed. Historical benchmark material and legacy prompt sources may remain temporarily for provenance/comparison, but the kernel does not load or execute them. `THIRD_PARTY_NOTICES.md` keeps the Archon MIT attribution for ideas/code reviewed during the migration.

## Operations

### Canonical GitHub-hosted worker

Before unattended Level-4 dispatch is enabled, repository configuration must satisfy the same fail-closed preflight enforced by `.github/workflows/dark-factory-worker.yml`:

- GitHub Issues are enabled. Issues are the intake, state and remote emergency-stop surface.
- `main` is protected by GitHub branch protection/rules so a direct push cannot bypass the in-repo evidence and exact-tree merge authority.
- all eight factory control labels exist: `factory:accepted`, `factory:rejected`, `factory:rate-limited`, `factory:in-progress`, `factory:needs-review`, `factory:needs-fix`, `factory:needs-human`, `factory:stop`.
- repository Actions secrets `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` and `SUPADATA_API_KEY` are configured.

The worker checks out current `main` without persisting checkout credentials, runs one global dispatch at a time, and refuses to start if any prerequisite above is missing.

Application validation state is disposable per run rather than a persistent secret surface. The worker provisions local `postgres:16` database `dark_factory_validation`, a random JWT secret, a synthetic E2E account/password, and `DARK_FACTORY_E2E_BOOTSTRAP=1`. The locked browser fixture is ingested through the real Supadata/OpenRouter application path.

The GitHub-hosted toolchain is pinned to Ubuntu 24.04, Python 3.12.14, Node 24, uv 0.12.5, Bun 1.4.0, Claude Code 2.1.245 and agent-browser 0.35.0.

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
