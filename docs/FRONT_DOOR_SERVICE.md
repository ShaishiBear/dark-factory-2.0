# Thin Front Door: implementation and activation boundaries

The isolated Front Door provides an owner-authenticated browser for original intent,
exploration, prepared scope review, explicit approval, actual programme progress/evidence and
requesting the existing remote stop. It is not deployed. Browser verification uses disposable
fixtures, not real programme outcomes. Complete real programme execution remains an activation
prerequisite. No new cloud resource or budget allowance is included.

## Authentication and hosting

`python -m factory_kernel.frontdoor_http` is a loopback-only WSGI service. Configuration names
one repository (from `.factory/kernel.json`), project and owner. A 32-byte randomly generated
owner token, encoded as 64 lowercase hex characters, is read from a private file. POSIX startup
rejects a token file accessible to group/other accounts. On Windows, configure an equivalent
service-account ACL. Keep this file and the private intent directory outside every worker
sandbox. The token is never put in the process environment or passed to GitHub or model workers.

For remote access, a separately configured HTTPS reverse proxy must serve the configured exact
origin, preserve its Host header and enforce bounded request bodies. The service binds only
127.0.0.1, accepts at most eight connections concurrently, and bounds socket waits. Stop requests
can run alongside slow GitHub observations. This is a small single-host service, not a distributed
control plane. Network-level rate limiting and deployment supervision belong to the hosting setup.

The browser keeps the bearer only in tab memory, sends it only to same-origin endpoints, and
does not use cookies, local storage, redirects or URL credentials. Reload locks access. Private
reads require authentication; writes additionally require the exact Origin. JSON requests have
a 250 KB limit, reject duplicate keys and retain domain CAS/idempotency rules. Static assets
contain no private state. All user text is rendered as text, with a restrictive CSP and no caching.

## Commands and observation

- `GET /api/snapshot`: stored intent plus the existing ProgrammeQueue status/receipt verifier.
  GitHub read failure leaves stop/completion unknown while original intent remains accessible.
- `POST /api/commands`: closed-shape intent-store commands. The transport supplies the configured
  authenticated owner; request JSON cannot choose an actor or role.
- `POST /api/programme-review`: compile a decomposition against the latest exact stored approval
  and return a review artifact. It does not publish protected files or executable issues.
- `POST /api/stop`: dispatch only `dark-factory-owner-stop.yml` on main with a bounded reason and
  unique request ID. A 202 response means requested, not stopped. Refresh must observe a remote
  open `factory:stop` issue before the UI reports a stop.

The service's GitHub CLI observation/dispatch credential must resolve to the configured repository
owner. The stop workflow independently verifies the platform actor, repository, event and main
reference. Its concurrency group is separate from product execution. The existing pinned GitHub
App action mints an Issues-write capability immediately before one atomic labelled issue POST;
the private App key never leaves that action. Missing/stale App identity refuses. Existing stops
or repeated recorded IDs do not create another issue; uncertain POST/dispatch outcomes are not
retried automatically. There is no stop-clear, resume, merge or product-dispatch endpoint.

## Still required before calling this an operating Front Door

1. Observe the optional API preparation adapter on its deployed host with real owner intent.
   Local tests use deterministic responses. Deployment and a live API preparation cycle are
   still required before calling the conversational intake path operational.
2. Connect reviewed programme delivery to the protected-main authority. Stored approval and an
   exported JSON object cannot bypass current trust-root governance. Re-read exact approval at
   delivery; an old export is not a capability.
3. Pass maintainer delivery checks after the real product cycle; observe the App stop workflow
   without interrupting useful qualification. No live stop test has been performed by this change.
4. Prepare and authorize the concrete hosting payload/destination. The earlier rejected AWS
   archive transfer remains unapproved. Reuse only the existing $7/month instance if authorized;
   no new instance, upgrade or paid add-on is authorized by this document.
5. Add durable trajectories, bounded replanning and governed self-maintenance through separately
   tested authority changes. None is implied by these UI controls.

Local launch shape (requires a private token file and configured GitHub owner authentication):

```text
python -m factory_kernel.frontdoor_http --state-dir /private/frontdoor/state \
  --token-file /private/frontdoor/owner-token --owner OWNER --project PROJECT \
  --origin https://APPROVED-HOST --app-login APP-BOT-LOGIN --port 8765
```

Do not expose the loopback development service directly or display fixture progress as live proof.

## Bounded specification preparation

`--enable-preparation` connects the UI to two separate API processes: intent drafting and
intent auditing. The host must explicitly configure `ANTHROPIC_BASE_URL=https://openrouter.ai/api`
and `ANTHROPIC_AUTH_TOKEN`; interactive subscription fallback is refused. Each process has no
tools, an empty temporary working directory, five turns, a $1 cap and a 338-second wall.
Provider retries are disabled. These are operational limits, not proof or approval criteria.

The drafter receives original wording, exploration, the previous approved specification and
bounded committed MISSION/README/API facts. It cannot read the host's private state. The auditor
binds the exact draft and covers all fourteen architectural readiness topics and seven scenario
categories. An unresolved product decision yields one justified question; technical uncertainty
is deferred. Missing, inconsistent or failed audits cannot publish a ready draft. Semantic
correctness still requires owner review; an intent audit is not product qualification.

A durable private record precedes the first API call. Replaying its exact request returns that
record; another request key cannot repeat preparation for that intake version. A crash leaves
pending evidence for inspection. There is no automatic retry. Newer owner intent causes the
final draft write to refuse rather than overwrite it. The proposal principal can propose scope,
but cannot approve it. Explicit owner approval and protected-main programme admission remain
separate. A failed/revise result currently needs inspection or clarified intent; bounded automatic
draft repair is not yet connected.

## Scope shown beside execution

The progress view names the active specification and revision, with programme and scope
hashes in expandable details. It compares that scope hash to the latest local owner approval
so approving a newer scope cannot make older running work look newly activated. A successful
GitHub observation carries a UTC timestamp; failed reads retain unknown completion/stop state
and no observation timestamp. Observations remain read-only and are not atomic authority.

## Preparation validation (15 September 2026)

Local canonical quick passed 2,547 tests and seven static checks, with 141 focused
preparation/transport/worker-policy checks and six causal preparation mutations caught.
The final JavaScript-only changes were then browser-verified against a disposable slow
provider: clarification saved during preparation, stop remained available, refresh observed
the synthetic stop, and an old preparation could not overwrite or represent the newer intent.
The fixture uses no model API or GitHub effects. Refresh and intent editing remain available
throughout preparation. No live deployment or preparation cycle is claimed.
