# Programme admission: first executable slice

This change connects a protected specification and decomposition to the existing kernel's
GitHub App route. It does not implement the complete target architecture or autonomous
trust-root migration. Its operational state remains inactive until an approved programme
is installed through the maintainer lane.

## Execution

1. A maintainer records explicitly approved scope in `.factory/programmes/active.json` on
   protected main. This is the bootstrap approval transport; a future Front Door will expose
   approval/versioning without requiring a person to edit JSON. Structural validity never
   substitutes for the user's approval of the specification.
2. The scheduled worker's `programme-sync` reads that file from the current protected branch,
   resolves one immutable Git tree/blob snapshot and deterministically compiles it.
3. The compiler checks complete, exclusive acceptance ownership, known references, bounded
   decomposition, repository identity, version/hash binding and acyclic dependencies. The
   proposal has no freeform task instructions: task scope is rendered from approved acceptance
   criteria, constraints and non-goals. This cannot prove the natural-language spec is sensible;
   normal triage and the existing proof pipeline remain necessary.
4. The kernel inventories open **and closed** issues through paginated REST, verifies exact
   issue content and App authorship, then creates at most one ready candidate using the App's
   installation token. It neither accepts issues nor clears rejection/needs-human labels.
5. Normal triage decides whether the candidate belongs in the product lane. Dispatch and
   direct builds recheck programme membership before model work; both merge routes recheck
   it before spending merge authority. Existing leases, independent review, evidence spine,
   exact-head merge checks and emergency stops still apply.
6. Both inline and deferred merges run the same production post-merge gate. Previously only
   the inline `validate_pr` wrapper invoked it; `merge_authorized` inherited the base method
   and returned after merged-tree verification. The deferred workflow now also supplies the
   application validation credentials to that wrapper.
7. Only after successful post-merge validation does the kernel append a completion receipt to
   the programme issue. The next scheduled sync checks the receipt, exact merged PR identity,
   linked issue, successful canonical worker run and run attempt before releasing a dependent
   candidate. A closed/rejected issue, failed run, edited receipt or partial lap cannot do this.

The receipt lives with the issue instead of disappearing with seven-day diagnostic artifacts.
Its trust boundary is the protected kernel/workflow and GitHub-authenticated, unedited Actions
comment, not the text of an arbitrary report. This is a narrow attestation, not the target's
general signed claim graph or independently replayable long-term evidence archive.

## Input contract (version 1.0)

The input has exactly `version`, `app_login`, `spec` and `proposal` fields.

`spec` has exactly:

- `id`: stable identifier; `revision`: positive integer; `repository`: exact `owner/repo`.
- `title`: short title; `outcome`: approved purpose.
- `requirements`: nonempty list of `{id, acceptance: [{id, text}]}`. Every acceptance criterion
  is required, globally identifiable and owned by exactly one executable item.
- `constraints` and `non_goals`: nonempty lists of approved text.

`app_login` is the installation's REST login, for example `shaishibear-dark-factory[bot]`.
It is protected deployment configuration, not a value the synthesizer chooses.

`proposal` has exactly `spec_sha256` and `items`. The hash is
`factory_kernel.canonical.sha256_value(spec)`. Each item has exactly `id`, `acceptance` (IDs)
and `blocked_by` (item IDs). No item can introduce an unapproved acceptance criterion or
carry arbitrary instructions. IDs have bounded syntax; duplicate JSON keys, IDs, ownership,
unknown fields, cyclic/self/missing blockers and stale spec hashes refuse compilation.

There are at most 50 acceptance criteria and 50 items, a 250 kB input limit, and each rendered
issue must fit inside triage's existing input window. These are explicit bootstrap limits,
not measured capacity claims. Changing the programme changes its canonical identity.

`programme-check PATH` has no remote effects or model calls. The test fixture in
`tests/factory/test_factory_programme.py` supplies a complete two-item example. It is a
synthetic navigation scenario, not an approved live task list.

## Serialisation and recovery

The existing worker workflow's `dark-factory-worker` concurrency group is the single writer.
The repository's App installation needs Issues write permission for the new projection effect;
verify that permission before activation. No additional repositories or administrator scope
are required, and a denied spend fails rather than switching identity.
Do not start additional independent `programme-sync` writers. GitHub issue creation has no
atomic idempotency key: this is crash reconciliation, not an exactly-once distributed claim.
The POST is never retried within an invocation. After an uncertain response, the next run
inventories GitHub and adopts the exact existing issue. Duplicates refuse rather than choosing
one. Exhausting the inventory bound refuses rather than pretending it saw every object.

Edited/forged current issues and App issues that lost their binding refuse. Replacing a
programme while an earlier programme still has open work also refuses; reconcile/cancel that
work through the existing maintainer path first. Rejected or closed-without-proof work stays
waiting and requires a reasoned revision, not automatic relabeling or repeated model attempts.

An API outage, expired/missing App token, stale programme or missing completion receipt stops
that transition. No personal-token fallback exists. The new issue spend occurs early, before
model work, and enforces the existing identity age limit. Existing push/PR handoff age debt is
unchanged. A failure after merge but before receipt publication leaves dependents blocked;
this version has no evidence-backed receipt reconstruction command.

## Frontend sequence and remaining work

First demonstrate this route with an explicitly approved product canary and retain the actual
run evidence. Then build a thin frontend for intent, spec review/approval, state/evidence and
stop control. The Front Door outputs approved intent; it does not write executable GitHub
issues itself. Its backend should reuse this compiler/admission boundary rather than add a
second queue. A full dashboard before a working handoff would expose the same missing backend.

Do not wait for unrestricted recursive improvement to build that thin interface. Autonomous
changes to factory authorities remain a separate governed migration: current trusted judges
must evaluate replacement judges without letting the candidate grant itself authority.

Still absent: conversational intake/approval storage, an API programme synthesizer, automatic
replanning from observed failures, resource-scoped leases, general durable trajectories and
lesson admission, a CLI-free provider adapter, and the governed self-maintenance lane. The
existing Claude executable still hosts the API worker tool loop; no Max session is required.

The documented hosted-job worst-case duration still exceeds GitHub's job limit. Restoring the
missing post-merge gate makes its cost real; it does not resolve that budget/decomposition
problem. Do not reduce proof, inflate timeouts or call this an unlimited autonomous factory.
