# HC-01: bounded worker feedback

The Harness Club assessment identified a useful question: does running checks
between drafts improve completed tasks enough to justify its cost? This tool
implements the experiment without changing production worker tools or judges.

## Run the no-model control

Use Python 3.11+, Git and a Linux Docker engine. Pull a reviewed official Python
image, resolve its immutable local ID, then run:

```bash
docker pull python:3.12-slim
image=$(docker image inspect python:3.12-slim --format '{{.Id}}')
python3 harness/feedback/verify.py --image "$image"
python3 -m factory_kernel.feedback_lab_cli \
  --image "$image" --output /tmp/feedback-control-new
```

The dedicated `harness-feedback-experiment` GitHub workflow runs both commands
without provider credentials. It verifies a successful real repair, nonroot
execution, denied root writes/network, absence of a host canary and Docker socket,
output limits, candidate failure, timeout, cancellation and container cleanup.
Ordinary quick tests cover orchestration and policy; they do not silently count
as executing these container checks.

The workflow uploads the experiment directory for 30 days, including incomplete
journals when available. Download it before expiry for longer retention; this
experiment does not silently write into the production trajectory archive.

The six-case control deliberately starts with a failing draft and then memorizes
only public examples. Every feedback arm should turn public checks green and still
fail independent final examples. That proves the plumbing rejects this overfit;
it does not show whether a model benefits from feedback.

## Optional model comparison

With an explicitly authorized spend reservation and the existing configured
Claude CLI route available, add `--live --total-usd 1.20`. Defaults reserve
USD 0.10 per arm for 12 assigned arms. `--per-arm-usd`, `--per-arm-seconds` and
`--rounds` make those choices explicit. This command uses the configured worker
model, not a newly selected model. No automatic retry is permitted.

The existing CLI's dollar flag is a backstop using provider-reported estimates,
**not a guarantee of billed expenditure**. Reported cost remains separate from
reserved spend; unknown cost remains unknown and a failed call consumes its full
reservation. A reported overrun stops subsequent calls. Real dollar-saving claims
require reconciling provider billing. Set an account-side cap if a hard billing
ceiling is required. Never pass credentials into the candidate container.

Each task has a single-draft arm and a feedback arm, alternating order between
tasks. They receive the same task, public examples, total turn allowance, total
dollar reservation and wall budget. The feedback arm divides those allowances
between at most three fresh calls. It stops when public checks pass. The final
examples run once against the last draft and never inform a repair. If public
checks remain red, passing final examples alone cannot count as acceptance.

The worker has no tools and returns one JSON `code` field defining `solve(value)`.
This is intentionally a small function-task study, not an end-to-end repository
benchmark. The shipped tasks are public development fixtures, with no claim of
statistical power or private holdout secrecy. IDs, groups, final examples and
other arms' drafts are omitted from model requests. Confirmation requires new
tasks grouped by incident, selected and frozen before viewing candidate results.

## Execution and evidence boundary

The host sends only candidate source and JSON arguments to each new container.
It compares returned values against frozen expected values outside the container;
a candidate cannot pass by printing a success marker. Candidate stdout is capped
at 64 KB, source at 24 KB. Public and final checks use separate containers.

Containers use an immutable local image ID, no network, no host mounts, no Docker
socket, a read-only root, unprivileged UID, dropped capabilities, no-new-privileges,
128 MB memory, one CPU, 32 processes and a 16 MB temporary filesystem. Only trusted
images without implicit volumes are permitted. The default engine seccomp profile
stays enabled. These settings use the documented [Docker execution controls](https://docs.docker.com/engine/containers/run/).
They constrain generated Python; they do not claim protection against a kernel or
container-engine vulnerability. Use a dedicated disposable host for adversarial
code. The Docker client and daemon are trusted parts of this experiment boundary.

Cancellation/timeout removes the exact named container, including descendants;
killing the attached client alone is insufficient. A failed cleanup is an error.
Cleanup/control calls have their own bounded deadlines. An in-flight live provider
call stops at its allocated provider deadline; cancellation prevents later calls.

Every new output directory preserves the task corpus, source-file inventory and
hashes, image ID, provider configuration, limits, reservations before calls,
prompts' hashes, drafts, public observations, final results and a flushed/fsynced
journal. Source is rechecked after execution. Never overwrite or resume an old
experiment implicitly. A crash after reservation remains visible as incomplete
work; there is no automatic retry. Store artifacts outside source directories.

All assigned arms remain in the denominator. Infrastructure errors, invalid
output, timeouts and cancellation are errors, not accepted refusals. Candidate
execution errors may inform the next public repair, but cannot count as passing
checks. Exit 0 means a complete, source-stable experiment, not that its candidate
succeeded or should be promoted. A recorded control always reports model quality
unmeasured. All reports set qualification authority false and promotion unauthorized.

## Decision after a run

Compare accepted tasks over all assigned arms, errors, reported cost completeness
and latency. Keep paired task identities; repeated attempts are not independent
tasks. Inspect failures and then use untouched screening/confirmation tasks before
proposing integration. A six-task smoke cannot establish production superiority.

Production integration, learned lesson injection, optimized prompts and model
routing remain conditional on that evidence. Existing capture/analytics and the
separate claim-evidence task supply observations and reviewed causal reasoning;
this tool does not duplicate them or turn model prose into a causal authority.
Persistent workers, swarms and self-rewriting judges are not justified by this
experiment. The current production harness remains the incumbent.
