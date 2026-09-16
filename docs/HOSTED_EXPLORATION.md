# Hosted adaptive exploration

The authenticated Front Door can run bounded adaptive investigations over an approved
scope. An owner opens a question, reviews its frozen comparison criteria and cumulative
budget, and explicitly requests 1–8 reasoning steps. Opening a question spends nothing.
Each step chooses the next useful analysis, registered probe, assessment, recommendation,
handoff or honest stop. The model may generate and retain different causal approaches;
it cannot approve scope, qualify work, publish a programme or execute arbitrary probes.

The current probe registry contains only `lookup-workload-v1`. Other architectural
questions can receive qualitative reasoning and explicit uncertainty, but unavailable
experiments must remain missing evidence. A recommendation is not a global optimum or
proof that unbuilt alternatives would perform worse. Only a selected, separately
reviewed strategy proceeds through normal independent factory qualification.

## Activation

Deployment remains with the Front Door service owner after protected maintainer delivery.
This change does not deploy a service or authorize a paid investigation. Opt in using
`--enable-hosted-exploration` with the existing `--hosted-preparation-identity` and one or
more repeated `--exploration-source <repository-relative-file>` arguments (at most 40).
Paths are host configuration, never supplied by a model or HTTP caller. Context is read
from current protected main, including policy and selected committed source, and checked
again before accepting results. The deployment must remain a single service process
for this state directory, with the existing private directory permissions and HTTPS.

The existing encrypted preparation workflow carries a separate
`dark-factory/hosted-exploration-v1` envelope. It accepts only the two tool-less preflight
roles and a narrowed call cap of at most $1, five turns and 338 seconds. Model credentials
remain in GitHub Actions. Owner identity, protected workflow/ref/head, first attempt,
unique dispatch, expiry, ciphertext/result bounds and exact request identity are still
checked. The worker checks stop before calling its provider; the host checks before
dispatch, while observing, and before accepting a result. Existing intake calls retain
their original protocol and role policy.

## Commands and recovery

`GET /api/exploration` returns the private question history, comparisons, cumulative
reservations and operational jobs. The authenticated `POST /api/exploration/{open,start,
recover,reopen,abandon}` routes accept only typed exploration commands with a project
version and idempotency key. The server supplies the owner principal. Starting records
`adaptive-run-started` in the existing project event log before creating a background
thread and returns 202 immediately. Paid reservations precede provider effects.
Short transactions on the shared store instance serialize within the service, with a
five-second acquisition bound; the OS lock still refuses a competing service process.
This prevents ordinary progress polling from invalidating an in-flight completion.

A replay does not create another thread. A second job cannot start while a recorded
job remains unfinished. Restarted services display these jobs as interrupted and never
resume them automatically. Explicit recovery closes an interrupted job without calling
a provider, refunding spend, accepting an old result or granting qualification. A live
job cannot be closed this way; the owner can request factory stop. Unknown paid-call
spend freezes further reasoning across all questions for the same approved specification.
Closing a pending reservation does not remove that freeze. New approval is still required
when intent changes; recovery cannot approve it. If an approval change prevents recording
a terminal job event, it remains interrupted until owner reconciliation after approval.

The browser polls observations only while a local job is running. Failed or uncertain
commands are observed without automatic POST retries. Stop and publication remain
separate controls. A current unproven handoff can enter the existing explicit strategy
publication review; replacing an active programme remains governed separately.

## Validation boundary

`tests/factory/test_frontdoor_exploration.py` covers real event-log reservations,
concurrent starts, interrupted recovery, stop, stale source, unknown spend, encrypted
role/cap transport, worker limits, authenticated HTTP and an adaptive fixture sequence
that changes strategy after a measured counterexample and compiles a v1.1 handoff.
The registered `hosted-exploration-*` mutations exercise these refusal boundaries.
Fixture evidence is not a live paid run, product qualification or deployment evidence.

This slice connects hosted execution. Automatic authenticated strategy rejection,
governed programme replacement and a live A-rejected → B-freshly-qualified cycle remain
separate implementation and validation work.
