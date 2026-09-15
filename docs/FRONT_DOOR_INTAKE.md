# Front Door intent foundation

This is the domain/storage foundation for a thin factory Front Door. The isolated browser and
owner-authenticated transport are described in `FRONT_DOOR_SERVICE.md`; hosted service,
API intent preparation, programme synthesis delivery and activation are not connected yet.
The existing programme execution must finish its real proof cycle before Front Door activation.

## Contract

`factory_kernel.frontdoor_intent.IntentStore` accepts commands with an idempotency key, expected
project version, operation and payload. The transport must supply an authenticated `Principal`;
it must never construct that principal from fields in an incoming command. Only the configured
owner can record intent, add exploration or explicitly approve a draft. A proposal worker can
propose a specification but cannot approve it. This module is not an authentication service.

Operations:

- `record-intent`: preserve the owner's original wording and invalidate the unapproved draft.
- `add-exploration`: preserve an optional question without changing approved scope.
- `propose-spec`: structurally validate a draft through the same specification compiler used
  by programme admission; record assumptions, blocking product questions and deferred technical
  questions. The draft refers to the latest original-intent event.
- `approve-spec`: explicitly approve the current draft version and exact specification hash.
  Blocking product questions refuse approval. Technical uncertainties remain visible and may
  be resolved downstream. A replacement specification has a new revision; old approvals remain.

Structural validation and an empty question list are not semantic proof of complete intent.
The future intent auditor supplies proposals; the owner reviews and approves the observable
scope. Approval records intent only and does not qualify software or create executable issues.

## Durability and concurrency

Each accepted command appends a versioned event to canonical history. An exact retry returns
its original outcome without another event, even after subsequent commands. Reusing its key
with different content or actor refuses. Stale versions refuse instead of overwriting state.
Per-project OS locks exclude simultaneous writers; contention refuses so the caller can reload.

The small single-host store atomically replaces a complete history file after flushing and
fsyncing it. On POSIX it also fsyncs the directory. A failed replacement leaves the old history
intact. Historical events carry predecessor hashes; these are integrity checks, not signatures
or protection against someone who can rewrite the service directory. The directory must belong
to the trusted service account, outside worker sandboxes. This is not distributed storage or
resource-scoped executor fencing. Histories are bounded to 10,000 events and 16 MiB per project.

## Integration still required

The thin browser must show original intent, reviewable draft scope, explicit approval, actual
programme progress/evidence and stop control. Use the existing programme status/receipt verifier
for execution facts; intake approval must never be displayed as qualification or deployment.
Authenticate the owner before reads or commands. Keep all App credentials out of the browser
and proposal worker. Stop must reach the existing kernel stop authority.

The synthesis/activation adapter must bind the approved specification hash and preserve protected
main admission. It cannot turn these stored drafts directly into executable issues or grant
the App authority to rewrite its judges. Hosting/authentication and this adapter remain unwired.
No new cloud resource, credential, transfer or budget allowance is created by this foundation.

## Preparing a programme for review

`frontdoor_programme.prepare_programme` now joins the stored approval to an untrusted proposed
decomposition. An authenticated owner names the current project version, latest approval event
and exact specification hash. The adapter obtains scope from the private store; callers cannot
supply a replacement specification, actor or App identity. The existing programme compiler
checks hash binding, acceptance coverage, dependency structure and the closed proposal shape.

The returned review artifact preserves original intent, exact approval wording and actor, input
and programme hashes, and dependency order. It does not append history, create issues, publish
files or activate execution. An unapproved new draft does not alter existing approved scope;
once a newer approval exists, an older approval reference refuses. The protected-main delivery
adapter must re-read approval before publishing. An exported JSON object is not a capability
or evidence that the programme has run. HTTP owner authentication is implemented in the isolated
service; delivery remains unwired.
