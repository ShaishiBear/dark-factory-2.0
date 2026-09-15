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

1. Connect bounded API intent auditing/preparation, ownership routing and the architecture's
   scenario/feasibility audit. The UI currently states that specification preparation is not
   connected. Prepared drafts can be supplied through the tested proposal boundary; this is not
   a working conversational intake engine yet.
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
