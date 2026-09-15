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

`programme-status` reads the current protected input and verified issue inventory without
constructing an execution runtime, making model calls, creating issues or changing labels.
It reports each item's acceptance IDs, issue number, labels, unmet predecessors and observed
state. `completion_verified` uses the same receipt verifier as successor admission; a closed
issue without that proof remains explicitly unverified. Edited bindings, unreadable evidence
and a programme that changes during observation refuse instead of returning partial progress.

This command remains available while execution is stopped. Its output is a progress view,
not permission to execute: `ready-for-candidate` describes dependency readiness only. GitHub
reads are not atomic, and every effect still enters through current admission and stop checks.
Use the reported issue's comments to inspect its retained completion receipt and run evidence.

## Serialisation and recovery

The existing worker workflow's `dark-factory-worker` concurrency group is the single writer.
After a successful action on a bound programme, an optional control job may dispatch the same
worker again. A pulse permits at most eight further continuations, decreasing the counter on
each request and carrying the exact programme hash into the next admission. Idle, failed,
cancelled, stopped, unbound or changed-scope work does not continue. No dispatch POST is retried
after an uncertain response. The ordinary schedule remains the fallback after a stopped chain.

Only that small control job has Actions write permission, with no App private key or model
credentials. It holds the same workflow lock until it finishes, so the successor cannot start
before its predecessor run's completion receipt becomes observable. Scheduling failure is
visible but does not change the completed proof jobs' result: only the scheduling job uses
`continue-on-error`. Dispatch and merge/post-merge failures still fail the whole run. Each
successor repeats all existing admission, stop, lease, attempt, budget and proof checks.

This bounds scheduling latency without a laptop supervisor or a second executable queue.
It is not replanning, automatic repair of failed proof, a new spend budget or exactly-once
dispatch. The bound limits continuations per pulse; existing per-role and per-issue limits
still bound model attempts. A later scheduled pulse may continue unfinished approved work.

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
model work, and enforces the existing identity age limit. Builds prepare a byte-bound publication
handoff after their gates; a separate step mints a fresh token, rechecks the exact clean revision,
artifact hashes, current issue scope and lease, then pushes and publishes through the existing
single provenance publisher. Push and PR creation now enforce identity age too. Other callers
(re-head, resume and containment) must supply a fresh token or refuse. A failure after merge
but before receipt publication leaves dependents blocked;
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

Qualification and merge/post-merge now use separate hosted jobs within the existing workflow
concurrency lock. The dispatch job has a 360-minute ceiling; merge/post-merge has 210 minutes.
The existing proof budgets remain unchanged. These are execution ceilings, not measured run
durations or a guarantee that every possible build path fits.

The producer exports the original evidence and authorization bytes in one bounded envelope.
Its job output binds the envelope digest and kernel revision. The consumer downloads only that
run/attempt's named artifact, checks its digest and repository/run/attempt/kernel/PR identity,
then re-runs the existing pre-merge authority before any merge. A missing artifact, changed
base, changed evidence or changed head refuses. Artifact transport is not a new proof authority.
The full post-merge ladder still runs and completion requires the whole workflow to succeed.

Completion also checks that the dedicated App actually merged the PR and that both PR branches
belong to this repository. App authorship alone no longer qualifies a human-merged PR.

## Early validation and rehead admission

Both entry points read the linked issue and use the current protected programme admission
before fetching a candidate or creating a worktree. Retired programmes, changed scope, edited
App issues, removed bindings and closed items refuse before proof or model work starts. A
fresh stop read follows admission so a stop arriving during its remote reads is honored.
Ordinary issue admission remains unchanged. These early refusals grant no proof authority;
the existing independent qualification and fresh final merge admission still run in full.

Focused tests exercise real programme admission against an in-memory GitHub service, with
current-programme and ordinary-issue controls reaching the original work boundary. Four
causal mutations cover removing either admission or its subsequent stop check.
