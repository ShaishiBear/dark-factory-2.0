# Governed programme replacement

Replacement must preserve the approved outcome, verified completed work, cumulative
cost and execution ownership. A changed strategy does not inherit product qualification.
The existing publication lane continues to refuse replacement until the full transition
protocol below is connected.

## Implemented review boundary

The authenticated, same-origin `POST /api/programme-replacement-review` accepts the
existing canonical programme review request. It resolves current owner approval and,
for an exploration handoff, regenerates the stored current recommendation. It accepts
no caller-supplied completion records, cost totals, findings or execution state.

The observer reads the protected active input and validates App-created issues. It
separately reports verified completed items with their original programme/run/PR receipt,
and pending items (including closed issues without verified completion). Changed names,
acceptance coverage or dependencies cannot silently become preserved completed work.
Open pending issues, open App PRs and queued/running/waiting/requested/pending canonical
worker runs are explicit blockers. Incomplete or changed inventories refuse the review.

Two matching reads detect observed changes; **they are not a concurrency lock**. Even
an empty blocker list grants no activation permission. The report says review-only,
UNPROVEN and no proof reuse. It cannot close work, publish, dispatch, resume or merge.
The owner interface exposes this distinction and the exact review record.

The private event-log head binds all scope decisions and exploration reservations.
Review retains charged calls, reserved dollars, work units and uncertainty, including
failed or interrupted effects. Missing budget history is unknown, not zero. The existing
execution retry budget is per issue; a replacement must not gain a fresh allowance by
creating a different issue. The report therefore requires a cumulative execution ledger
instead of claiming that the exploration budget also covers factory execution.

## Persistent execution fence

Protected main may contain `.factory/programmes/execution-fence.json`. Its presence stops
new work at the existing kernel stop checkpoints, the common paid-stage funnel (including
independent judges and provider retries), triage, build publication, continuation/recovery
and final merge. Hosted preparation/exploration and ordinary programme publication also
refuse while fenced. Malformed content, symlinks, directories, incomplete observations or
changing source cannot make the fence permissive. A missing local file has no effect.

The reader cannot create or remove a fence. No fence is created by deploying this change.
It remains independently observable in the owner interface; historical progress remains
readable. Presence is global and conservative, matching current single-worker ownership.
An already running model call may finish; its next checkpoint stops further work. This
is not an activation capability. Publication and the canonical worker now share the same
non-cancelling workflow concurrency group, including publication's validation, wait and
merge jobs. Queued consent may expire; waiting does not extend or replay it. The transition
still must observe drained execution and reconcile all uncertain effects before switching.
Fence removal needs protected governance.

## Activation protocol still required

1. Freeze an immutable transition intent bound to old programme, proposed input, approved
   scope, owner event-log head, source revision and completion/budget observations. Scope
   changes require separate current owner approval; this same-scope operation refuses them.
2. Publish the persistent execution fence and observe it before draining old work.
   Use publication's shared workflow ownership for activation. A quiet observation outside
   that ownership cannot substitute for serialization.
3. Drain old execution and reconcile every pending or ambiguous external effect. Retire
   pending work through exact identity-bound, journaled effects; never retry an uncertain
   POST as if it had failed. A crash leaves the fence in place, not an unfenced half-swap.
4. Preserve an immutable lineage archive and original verified completion receipts.
   Completed scope and dependency obligations must be unchanged. Historical completion
   is recorded work history; it does not qualify B's new code or current product head.
5. Carry a cumulative execution reservation ledger across all replacement generations.
   Charge before paid work, retain uncertain spend, and bind approved limits to the scope.
6. Publish the exact replacement through protected provenance, current owner consent and
   required checks. Activate with a compare-and-swap against the fenced old generation.
   Fence stale continuations and delayed recoveries; only one generation may dispatch.
7. Release the fence only after independently observing the exact activated lineage.
   Retained B receives all fresh factory qualification. Observe an actual hosted bounded
   A-rejected to B-qualified cycle before calling the milestone complete.

This review implementation is a prerequisite, not completion of governed replacement.
