# Bounded continuation of approved work

## Observed problem

The canonical worker is enabled with `17 * * * *`, but actual scheduled starts on 15 September
2026 were 01:25:54, 07:41:29 and 13:30:32 UTC (runs 34917228085, 34942989742 and 34975441488).
That is scheduled execution independent of the laptop, but not an observed hourly response time.
GitHub documents that scheduled work can be delayed or dropped under load; this observation
does not establish the particular cause of these gaps.

Source: [GitHub workflow troubleshooting](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows).

## Transition

Each run still performs one canonical factory action. After successful dispatch and successful
or skipped merge/post-merge, a small control job can request one successor run when the action
advanced a bound programme. A pulse starts with at most eight further continuations. Every
request decreases the counter and includes the exact programme hash and parent run ID.
Before candidate creation or model preflight, `programme-pulse` validates and logs those inputs
as one JSON record, so parent/child runs and their decreasing limits can be traced directly.

The next run checks that hash before materializing a candidate. It repeats normal triage,
admission, stop, lease, attempt, credential and proof checks. The whole workflow retains its
single concurrency group, so its queued successor starts only after the predecessor finishes.
The same approved DAG remains the source of executable work.

Idle/no triage progress, failure, cancellation, stop, missing/changed programme or exhausted
counter ends the chain. A rejected triage decision can cause one further pulse, which observes
no progress and stops. Failed qualification is not an automatic repair request. Operator
resume runs do not carry a programme projection output and therefore do not start a chain.

This is a count bound per scheduled/manual pulse, not a new dollar budget or a reliability
guarantee. Existing role budgets, issue attempt limits and proof requirements are unchanged.
The existing schedule remains the fallback for unfinished approved work.

## Credentials and failure semantics

Only the continuation job receives Actions write permission. It receives no dedicated App
token, private key or model credential, and runs no model, browser or database setup. Its
repository-scoped Actions token requests the same protected-main workflow; it cannot publish
product code or create product candidates through this command. GitHub expressly allows
`workflow_dispatch` events from that token to start runs.

Source: [GitHub token event behavior](https://docs.github.com/en/actions/concepts/security/github_token).

Only this optional scheduling job uses `continue-on-error`. A failed wake-up request must not
invalidate a completed proof run's receipt. The job's failure stays visible, and it sends no
retry after an uncertain response. Dispatch and merge/post-merge jobs never ignore errors;
their failure still fails the whole run and prevents continuation. This is neither exactly-once
dispatch nor a substitute for current completion evidence.

## Evidence before delivery

Local focused tests cover decreasing counters, failed/cancelled/unfinished predecessors,
stops at both checks, changed/retired scope, canonical run identity, idle, no retry after an
uncertain dispatch and separation of scheduler errors from proof failures. Five targeted
mutations pass a copied green baseline and then fail the intended assertions. No model call
or cloud continuation has been exercised by these tests.

Keep this change isolated while main regression 34979054098 runs on 0937041. Deliver through
normal base-anchored maintainer and exact-head quick checks before using it for the approved
citation programme. Observe actual parent/child run IDs and decreasing inputs before claiming
the continuation works in production.
