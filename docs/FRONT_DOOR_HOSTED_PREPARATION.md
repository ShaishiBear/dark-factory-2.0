# Front Door preparation through GitHub Actions

Status: encrypted hosted preparation is deployed on the existing Lightsail host. One real
proposer call completed and its result was decrypted; the compiler refused its invalid draft.
Independent audit, scope approval and synthesis have not yet completed as a real Front Door flow.

The owner keeps `OPENROUTER_API_KEY` in GitHub Secrets. The Lightsail Front Door dispatches
one protected-main Actions job per proposal stage. It holds the existing owner GitHub
credential and a dedicated encryption identity, but no model API key or GitHub App private key.
The existing intent and programme adapters still validate every returned proposal locally.
Drafting never approves scope, activates a programme, changes code or qualifies a candidate.

## Private exchange and authority

Only an opaque random request ID and compressed, encrypted prompt enter workflow inputs.
The standard [age CLI](https://github.com/FiloSottile/age) encrypts with a dedicated native
X25519 identity shared by the private host file and `FRONTDOOR_AGE_IDENTITY` Actions secret.
The repository is public, so logs and artifacts must contain no plaintext intent, prompts,
model output or exception details. The job uploads exactly one encrypted result file.
GitHub retains that encrypted artifact for seven days; private local records retain the
validated result, call ID, run ID, request hash, commit, model and reported cost.

The secret-bearing job requires the repository owner, first run attempt and protected main.
The entry point independently checks platform run provenance and request freshness before
calling the API. Duplicate dispatch IDs and incomplete replay inventories refuse. No
caller-supplied model, tools, environment or budget reaches the API worker. The existing
central intake roles each retain no tools, five turns, $1, 338 seconds and no transient retry.
An intent draft uses two separate jobs (proposer and auditor); programme synthesis uses one.

On return, the host checks repository, owner, event, workflow ID/path, main branch, exact
commit, first attempt, successful run, artifact identity and encrypted request/result hash.
The ordinary draft/audit or deterministic programme compiler then runs, and rechecks the
current local intent/approval version. A newer clarification prevents stale publication.

## Bounds and failure behavior

The local intent-version reservation precedes any call. Each stage also writes its opaque
dispatch identity before POST. A network error after POST triggers observation of that same
ID; it never repeats the POST. Each stage has a ten-minute polling window, including queue
and setup time, plus bounded GitHub request timeouts. Oversized requests refuse without truncating the owner's original wording.
Commands remain concurrent with refresh, clarification and emergency stop. Closing the
browser does not cancel the server's in-progress call.

The host does not automatically restart a pending preparation after a process restart, or
retry a failed/uncertain paid attempt. Its durable record identifies any known run for
inspection and future bounded recovery. A queued job may finish after local observation
times out; that result is not silently treated as a draft. This initial adapter does not yet
reconcile such late results after restart. Product programme continuation remains the separate
canonical factory workflow and does not depend on the Front Door process or the owner's laptop.

### One bounded format recovery

An authenticated owner can request one replacement for a completed proposal that was retained
and refused by the deterministic programme compiler, before any audit ran. The request names
the current intent version, exact failed-record hash, reason and idempotency key. Its separate
durable reservation precedes all calls and retains the original record unchanged. The UI shows
the maximum additional spend: two fixed $1 calls, one proposer and one independent auditor.
The replacement uses the original intake history and current committed repository context.
It receives the same validation, audit and stale-intent checks as the first preparation.

This operation never runs automatically. A pending call, timeout, missing result, audit failure
or previous replacement does not qualify. Replaying the request observes its record; a different
request key cannot create a second replacement. Scope approval is still a separate owner action.
The recovery endpoint is `/api/prepare-recovery`, with the same bearer and same-origin checks.

## Activation sequence

Keep delivery separate from activation while the citation qualification is running. After
the required full real product cycle and normal trust-root delivery checks, install `age`
from supported Ubuntu packages on the existing host. Generate a dedicated identity with
mode 0600 outside the repository; securely set the same identity as the named repository
secret. Never print it or reuse the model API key as an encryption key.

Configure `--hosted-preparation-identity` instead of `--enable-preparation`. These modes are
mutually exclusive. Verify the host credential resolves to the configured owner. Verify
real encrypted drafting, audit, explicit scope approval, synthesis and current evidence
before marking the service observed working. The shared encryption identity, workflow and
host deployment are not provisioned by committing this implementation.

## Local evidence

Canonical quick passed 2,569 unit tests and seven static checks. The focused preparation,
synthesis, HTTP, policy and hosted suite passed 64 tests. Eight hosted causal mutations were
caught with a green copied baseline. The copied fixture includes this workflow explicitly;
its initial missing-file failure was corrected before the mutation run. Standard age tests
exercised real encryption/decryption, tampering, wrong keys and private file permissions.
A mocked paid provider exercised the actual encrypted Actions entry point and confirmed only
fixed status messages and encrypted result files escape it. No paid API call or remote
preparation workflow was dispatched by these tests.
