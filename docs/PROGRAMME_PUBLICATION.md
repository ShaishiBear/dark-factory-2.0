# Programme publication boundary

Status: local request and observation foundation. No HTTP publication route, publisher
workflow, App write capability or trust-root admission exception is enabled by these modules.

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

## Remaining activation work

- Authenticate and nonce-bind the private request currency exchange to protected workflows.
- Recompile encrypted approved payloads under protected main and verify owner dispatch,
  first attempt, source revision and durable artifact identity before minting App effects.
- Admit only an exact active-programme data diff through an old-base-judged publication lane;
  leave the human-maintainer predicate unchanged.
- Observe idempotency before every effect; persist uncertain outcomes and reconcile them
  without blindly retrying branch creation, PR creation or merge.
- Require normal checks, fresh stop and fresh owner currency immediately before exact-head
  merge. Record the resulting main identity; do not treat publication as product completion.

The current citation programme is already active. Its synthesized review is not permission
to publish it again. Issue #189's human hold and the existing continuation counter remain.
