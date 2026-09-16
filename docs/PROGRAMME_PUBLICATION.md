# Programme publication boundary

Status: private request, authenticated observation and protected admission foundation. The HTTP
routes are disabled by default. The guard recognizes a separate exact-data publication lane,
but no publisher workflow or App write capability exists yet to produce its required provenance.

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

- Connect the authenticated currency exchange to the protected publisher.
- Recompile encrypted approved payloads under protected main and verify owner dispatch,
  first attempt, source revision and durable artifact identity before minting App effects.
- Observe idempotency before every effect; persist uncertain outcomes and reconcile them
  without blindly retrying branch creation, PR creation or merge.
- Require normal checks, fresh stop and fresh owner currency immediately before exact-head
  merge. Record the resulting main identity; do not treat publication as product completion.

The current citation programme is already active. Its synthesized review is not permission
to publish it again. Issue #189's human hold and the existing continuation counter remain.

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
the eventual publisher must still recheck stop/currency and exact head immediately before merge.
