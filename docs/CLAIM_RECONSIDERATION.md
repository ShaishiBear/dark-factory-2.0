# Factory feedback and bounded reconsideration

## Delivery plan and acceptance boundary

The next missing connection is from retained factory evidence to the assumptions behind
a selected strategy. A failed run does not establish that its strategy is wrong. Missing
proof is not a failed product claim. The existing evidence spine remains the proof source;
the existing IntentStore remains the decision history source.

This slice implements an owner-reviewed return path:

1. A host-configured catalogue identifies a retained run, exact base/head, item and compiled
   strategy programme. Request JSON selects a catalogue ID, never a filesystem path.
2. A read-only preview reuses `claim_explanation.explain_run`, binds the programme to its
   actual historical exploration handoff, and exposes evidence gaps and a content digest.
3. An explicit owner review records implementation failure, strategy failure or inconclusive
   attribution. The classification is an owner judgment, never an authenticated authority
   verdict. A strategy failure requires explicit causal claims and retained evidence references.
4. One atomic event retains evidence and judgment separately, invalidates only the named
   exploratory assumptions and records the transitive affected recommendation frontier.
5. Reopening cites that event, checks current approval, stop, pending effects and round bounds,
   refreshes repository context and retains all candidates, historical evidence and spend.
6. The replacement uses the existing compiler and v1.1 handoff. Publication, admission,
   completed-work accounting and fresh qualification retain their existing authority.

Acceptance tests cover A selected, evidence reviewed, only A's dependencies invalidated,
unrelated selection unchanged, B selected from retained alternatives and freshly compiled
with identical approved scope and no proof reuse. This is a deterministic integration
scenario, not an observed independent rejection or a live B qualification.

Negative cases include incorrect programme/recommendation/item/revision, stale policy,
missing/tampered evidence, fabricated authority, out-of-scope claims, foreign principals,
new intent, stop, concurrent version changes, replay, duplicate feedback and exhausted rounds.
Meaningful causal mutations must demonstrate that detectors catch removed guards.

## Service contract

Construct `Reconsideration(exploration, catalogue)` with the existing `Exploration` service
and a host-owned mapping from opaque source IDs to `RetainedRun` values. Each source supplies
the original v1.1 programme input, item, Actions run/attempt, PR, retained artifact directory,
policy path and exact base/head. These are configuration, not fields accepted from request JSON.
The catalogue is copied on construction. Source metadata establishes a historical association;
it does not authenticate an Actions artifact issuer or prove that the programme was admitted.

`preview(project, session_id, source_id, principal=...)` reads only after owner authorization
and stop checks. It returns the project version, observation and its SHA256. The observation
includes the existing claim explanation and the existing `validation-refusal.json`, checked
against the configured PR/base/head and protected reason/authority vocabulary. Refusal content
is scrubbed again. Filesystem paths are not exported. Missing or malformed records remain gaps.
Even an intact refusal is labelled `issuer_authentication: not-established`.

`review(project, command, principal=...)` uses the normal exploration command envelope. Its
request contains `source_id`, `observation_sha256`, `classification`, `claim_ids`,
`evidence_claim_ids`, `rationale`, `alternatives_considered` and `supersedes` (initially null).
Classification is one of `implementation-failure`, `strategy-failure`, `inconclusive`.
Only strategy failure names exploratory assumptions to invalidate, all from the original
selected causal ancestry. Non-inconclusive reviews must cite `validation-refusal` and may
also cite intact spine claim IDs. Operational currency/identity refusals and unknown causes
cannot become implementation or strategy failure through this operation. A missing manifest
does not hide an intact refusal, and no missing proof obligation becomes a failed obligation.
When retained proof reports policy/subject drift, only an inconclusive review is permitted.

The evidence is reread and hashed before append. Existing IntentStore authorization, CAS,
idempotency, size limits and atomic replacement govern the event. A different key or catalogue
alias cannot repeat an attributed run attempt. An inconclusive review may be followed up by
explicitly naming its latest feedback ID in `supersedes`; both observations and judgments stay
immutable. Attributed judgments cannot be silently retracted or restore invalidated claims;
a revised exploratory assumption needs a new claim. At most 80 feedback events are retained
per project within the existing history size bound.

`reopen(project, command, principal=...)` accepts `feedback_sha256` and `reason`. It checks that
the feedback frontier still identifies this question's current recommendation. It then uses
the same round/pending-effect/stop/context checks as ordinary exploration reopening. The
canonical `reopened` event and projected `reopenings` retain the feedback link. It neither
refunds exploration spend nor touches factory budgets or historical completed work.

The existing owner-only `decision_history.explain_history` API exposes `exploration.feedback`,
including separate observation, owner judgment, affected claim IDs and recommendation frontier.
The history's ordinary `events` retain the actor, timestamp, version and hash chain. No new
HTTP mutation endpoint or hosted activation is installed by this slice.

## Subsequent integration

Hosted adaptive authoring and authenticated outcome ingestion need separate transport and
operational integration with the Front Door owner. General automatic causal classification
requires an authority contract that distinguishes strategy impossibility from an ordinary
implementation defect; it cannot be inferred from workflow conclusion or model agreement.
Governed programme replacement must preserve verified completed work and cumulative factory
budgets. Only then can a real A-rejected/B-freshly-qualified cycle be claimed. No new proof
reuse, paid calls, product dispatch, deployment or completed-issue operation is part of this
delivery.
