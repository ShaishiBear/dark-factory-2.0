# Programme publication boundary

Status: private request, authenticated observation, protected admission and publisher workflow
implemented, including the host dispatch journal and explicit Front Door consent. The HTTP routes
remain disabled by default. No real programme has used this publication workflow.

`PublicationRequests.reserve` accepts an authenticated owner, a compiler review request and
consent naming the exact programme input hash, repository and repository visibility. Private
interview wording and approval wording are excluded. `observe_publication_source` obtains
the repository identity, visibility, protected main, compiled active programme and stop state
from GitHub. The remote reads are fenced by a second main/metadata observation. They are not
an atomic transaction, and must be repeated before an effect.

Records live in the existing private service directory. Atomic writes are fsynced, requests
are scoped to the validated project and opaque ID, and the existing intent lock serializes
same-project reservations. One project version can have one publication request. A retry of
that exact request returns its prior record; another ID cannot reset it. A worker cannot
reserve a request. Service-directory integrity remains a trust boundary.

An identical approved specification already active returns `already-active`, including when
the proposal partitions it differently. This must never create duplicate work or reset the
continuation budget. Any different active specification returns `requires-governed-replacement`.
This first publication path does not implement replacement, replanning or completion reuse.

`current` rechecks the exact intent head and version, one-hour lifetime, protected main,
visibility, active input, clear stop, payload hash and compiled programme hash. It refuses
both terminal dispositions. Its response contains identities only, without scope wording.
A change in owner decisions, including exploration, invalidates the reservation. A historical
request returned by an idempotent retry is not fresh authorization.

## Authenticated currency transport

`--enable-publication-requests` requires the existing `--hosted-preparation-identity`. A
domain-separated HMAC key is derived from that private native age identity. No new credential
is created, and the original identity never appears in a request, response or log. The key
belongs to trusted host/protected workflow code only; it is never supplied to a model.

Owner-bearer `POST /api/programme-publication` reserves exact consent without dispatching any
workflow. `POST /api/publication-currency` accepts only an authenticated bounded challenge,
then re-reads private owner currency and protected GitHub state. The latter route cannot
create a reservation, change scope, stop/start work or obtain an App capability. A bearer
alone cannot impersonate that publisher, and a publisher message cannot authorize owner writes.

Challenges bind repository, configured project, request hash, main SHA, effect phase and a
random128-bit nonce. Request and response MACs use different domains. Both sides enforce a
60-second freshness bound, and consumption refuses a reservation that expired in transit.
The caller must use a new challenge immediately before each effect and retain its own durable
effect/idempotency record. This read-only protocol is not a one-use capability or an atomic
transaction with GitHub; a response alone grants no merge authority.

## Remaining activation work

- Deploy and enable the currency endpoint only with the protected workflow delivered.
- Observe the first appropriate real App publication, including the platform's resolved
  contents-API commit author/committer, normal required checks and exact merged-tree receipt.
- Add separately governed replacement/replanning before a different active programme can be
  published. An existing active programme cannot be cleared to make the initial path usable.

The current citation programme is already active. Its synthesized review is not permission
to publish it again. Existing issue dispositions and continuation counters remain authoritative.

## Owner publication connection

`--enable-programme-publication` requires currency, the existing age identity, and the protected
project/origin. The owner previews the exact public input, repository and visibility before
explicit consent. Private intake and approval wording stay on the host. Existing active scope
shows its disposition without a publish control. Workflow submission and completion are displayed
separately from active-programme observation and independently verified product completion.

One private, fsynced journal per project version reserves the dispatch before its single encrypted
POST. Concurrent or repeated requests return the historical pending, submitted or uncertain result;
none repeats a POST. A failure known to precede POST may reuse only the same current consent.
Changed owner decisions or expired consent require reconciliation. Workflow observation rechecks
owner, repository, protected source and first attempt. Unavailable publication evidence does not
hide independent stop or product progress. The host receives no App or model credential.

## Independent publication admission

`publication_admission` re-derives the compiled programme and exact input hash, requires a single
regular active-programme file added to an empty base, and checks complete App PR/commit identity.
Exactly one commit must have the approved source as its only parent. Human-maintainer identity
is unchanged. A separate publication verdict is never eligible for unattended maintainer merge.

`publication_observation` independently reads the platform run, completed validation job, complete
bounded artifact and commit inventories. A PR marker only locates a run. The owner must have
dispatched its first attempt from protected main; the validation job must finish before PR
creation. The artifact's platform digest is checked against downloaded ZIP bytes before reading
its sole bounded manifest in memory. No paths are extracted. Foreign, expired, ambiguous or
truncated evidence refuses admission.

Head CI checks public provenance with a read-only token. It never receives the age identity.
The old-base guard also calls the fixed HTTPS currency endpoint and verifies nonce, MAC, source,
phase, expiry and payload identities. Redirects, oversized responses and unreadable currency fail
closed. Only the active-programme path veto is discharged; every secret, dependency, ratchet and
other path finding survives. A guard pass is an observation, not a durable publication capability:
the publisher still rechecks stop/currency and exact head immediately before merge.

## Protected publication effects

The owner-only workflow runs four jobs from the same protected-main source. Validation decrypts
only the approved public input, recompiles it, checks current consent, screens for high-confidence
secrets, and uploads the manifest before any App write. Subsequent jobs independently verify the
artifact digest and completed validation job. A complete bounded run inventory refuses duplicate
dispatches across the full request lifetime; reruns are refused.

The publication job mints an installation token restricted to this repository and contents/PR
writes. Each branch, single-file commit and PR POST has a fresh source/stop/currency observation.
Existing branches or PRs require reconciliation, never reset or reuse. A private local journal
fsyncs pending state before each POST, records only bounded outcome identities, and treats a lost
response as uncertain. No POST is retried. The host journal must separately prevent dispatch replay
if a runner disappears before uploading its local journal; that host connection is not yet enabled.

A separate read-only job waits at most 22 minutes for required checks. The merge job then mints
a fresh installation identity and requires both named authorities, all required checks passing,
current owner consent, clear stop, unchanged main and exact PR head. It makes an immediate squash
merge without native auto-merge or bypass flags. The receipt independently checks the merged tree,
single approved parent and current main. It explicitly says publication is not product completion.
An uncertain or unverified merge requests the existing App stop control once. If containment cannot
be confirmed, the workflow fails and reports that uncertainty; it never claims a successful stop.

This remains a sequence of fresh observations and conditional GitHub effects, not an atomic
transaction across the host and GitHub. Owner decisions can change after the last observation.
