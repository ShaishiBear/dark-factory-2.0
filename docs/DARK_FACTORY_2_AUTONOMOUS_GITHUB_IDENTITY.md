# Dark Factory 2.0 — Autonomous GitHub Identity

**Status:** owner-approved target decision.  
**Canonical branch while current qualification is in flight:** `architecture/dark-factory-2-target`

## Decision

Dark Factory should use a **dedicated GitHub App installation token** for autonomous GitHub mutations that must trigger normal GitHub workflow events.

Do not use the repository `GITHUB_TOKEN` for creating/updating autonomous product PRs once this migration lands, because GitHub deliberately suppresses or approval-gates workflow events caused by `GITHUB_TOKEN`.

Do not use a long-lived personal access token as the target design.

## Why this is required

The current worker uses `${{ github.token }}` and opens product PRs with that same token. The protected `main` ruleset requires two contexts on the latest PR head:

- `quick-authority`
- `trust-root-authority`

Current workflow topology is intentionally split:

- `dark-factory-ci.yml` uses `pull_request` and executes the exact PR head;
- `dark-factory-trust-root.yml` uses `pull_request_target` and executes trusted workflow/code from the protected base while treating the PR head only as data.

With PRs created/updated by `GITHUB_TOKEN`:

- `pull_request` runs are created in approval-required state;
- `pull_request_target` is not an exception to event suppression and therefore does not provide the trust-root check automatically;
- branch rules have no bypass actors, so the kernel cannot merge without both required contexts.

Therefore the present merge lane cannot be fully unattended.

## Why GitHub App rather than PAT

A dedicated GitHub App provides:

- a distinct machine identity rather than impersonating the owner;
- short-lived installation tokens;
- repository-scoped installation;
- narrowly selected permissions;
- auditable authorship;
- future compatibility with the capability-broker architecture.

A fine-grained PAT would be simpler initially but is long-lived and user-bound. It is acceptable only as an emergency temporary bridge if the owner explicitly approves one. It is not the target architecture.

## Why not solve this with workflow_dispatch + custom statuses

`workflow_dispatch` is an exception to `GITHUB_TOKEN` event suppression, but using it as the primary fix creates a worse trust shape.

To keep the trust-root authority on trusted `main` while satisfying a required context on the exact PR head, Dark Factory would need to add custom status/check publication semantics. Running the dispatched workflow directly on the PR branch would instead execute the workflow definition associated with that ref and weaken the protected-base authority model.

That custom reporting path would enlarge the trusted computing base and create another identity/binding mechanism to verify.

Prefer normal GitHub PR event semantics driven by a narrow GitHub App identity.

## Minimum app permissions

Install the app on **this repository only** initially.

Expected repository permissions:

- **Contents: Read & write** — push/update autonomous branches.
- **Pull requests: Read & write** — create/update PRs and perform the existing exact-head merge operation where allowed.
- **Issues: Read & write** — labels/comments/issues used by the factory control plane.
- **Metadata: Read** — implicit/basic repository metadata.

Do not grant unless a concrete implementation proves it is required:

- Administration;
- Secrets;
- Environments;
- Members;
- Checks write;
- Commit statuses write;
- Actions write;
- Workflows write.

Observation-only Actions operations such as reading/downloading prior workflow artifacts should continue using the ordinary `GITHUB_TOKEN` where sufficient rather than broadening the App installation permissions.

The autonomous product path must remain unable to modify protected workflow/trust-root surfaces under the normal factory capability policy even though the GitHub identity can push ordinary branch content.

## Credential storage

Repository configuration:

- repository variable: `DARK_FACTORY_APP_CLIENT_ID`
- repository secret: `DARK_FACTORY_APP_PRIVATE_KEY`

Generate an installation token at runtime using the reviewed/pinned `actions/create-github-app-token` action. The currently selected action revision uses `client-id`; its `app-id` input is deprecated, so the canonical repository variable is the **Client ID**, not the numeric App ID.

Never commit the private key.

The installation token should exist only for the job/stage that needs GitHub mutation authority.

## Migration shape

Do not globally replace every use of `${{ github.token }}`.

Separate identities by capability:

```text
read/observe GitHub state
    -> ordinary GITHUB_TOKEN where sufficient

create/update autonomous branch or PR
    -> GitHub App installation token

model worker
    -> no GitHub credential

independent authorities
    -> their existing minimum read-only GITHUB_TOKEN unless a write is explicitly required

merge broker
    -> GitHub App token, bound to a valid MergeAuthorization and exact expected head
```

The key design principle is:

> **The App token is a capability, not a global environment variable.**

Do not hand it to model workers or unrelated subprocesses.

## Required implementation proof

Before running another expensive full autonomous qualification, prove the production GitHub event shape with the cheapest bounded test possible.

Create a disposable/autonomous test PR using the GitHub App token and prove on its exact head:

1. `pull_request` workflow is created automatically with no manual approval requirement;
2. `quick-authority` executes and reports on the exact head;
3. `pull_request_target` fires automatically;
4. `trust-root-authority` executes from the protected base workflow/code and binds the exact PR head as data;
5. both required contexts appear on the latest PR head;
6. branch rules would allow merge once all required checks are green;
7. head movement invalidates/re-runs the appropriate authorities;
8. no manual approval is required anywhere in the path.

Use a harmless maintainer test shape; do not spend model budget or run the full factory ladder to test event delivery.

## Regression test requirement

The maintainer fix must include a deterministic/structural regression that prevents the worker from silently returning to `GITHUB_TOKEN` for autonomous PR creation/update.

Also test that:

- observation-only operations may still use `GITHUB_TOKEN`;
- the App token is not exposed to model-worker environments;
- merge remains exact-head bound;
- trust-root workflow continues to execute trusted base code rather than PR-proposed workflow code.

## Relationship to the future TCB

This decision aligns with the target architecture:

```text
untrusted orchestrator
        |
        | requests GitHub mutation
        v
capability / mutation broker
        |
        | short-lived scoped App token
        v
GitHub
```

A future GitHub adapter may remain largely untrusted only if it cannot use the App token except through the narrow mutation broker.

The App itself is not proof. Exact revision identity, required authorities, merge authorization and GitHub ruleset enforcement remain mandatory.

## Immediate sequencing

1. Owner creates and installs the dedicated GitHub App on `ShaishiBear/dark-factory-2.0`.
2. Owner stores Client ID/private key as repository variable/secret.
3. Claude creates one bounded maintainer PR implementing token generation and capability-scoped use.
4. Run the cheap event-shape proof above.
5. Only when both required checks are proven unattended on an App-created PR should #134 receive another expensive validation.
6. Re-head #134 if the maintainer merge moved `main`.
7. Resume full factory qualification.

## Locked conclusion

The target choice is:

> **Dedicated GitHub App, repository-scoped, least privilege, short-lived installation tokens, used only through narrow GitHub mutation capabilities.**

Do not reopen PAT vs App unless concrete GitHub constraints make the App route infeasible.
