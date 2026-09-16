# Adaptive Preflight: investigate before committing a programme

This layer explores technical questions against already approved scope. It can generate
and challenge alternatives, execute a bounded experiment, revise a recommendation and
compile a programme after exploration. It neither qualifies code nor publishes work.

`Exploration` is the deterministic service interface. `ExplorationReasoner` can drive its
available actions through bounded, tool-less proposer/challenger calls. Existing direct
and reasoning-only Preflight APIs remain compatible; no HTTP endpoint or hosted worker is
activated by this delivery.

## Connected behaviour

The integration test registers a lookup work limit and two causal strategies. Initial
predictions prefer the existing linear lookup. A fixed experiment actually executes both
algorithms against the same declared integer workload. Linear lookup performs 2,000
comparisons, exceeding the preregistered 1,000-comparison limit; indexed lookup performs
20 logical lookups. The declared falsifier invalidates the linear strategy's exploratory
assumption. The recommendation changes to indexed lookup, and a proposed programme is
compiled against the unchanged approved acceptance criteria.

This is a real finite experiment with fixture reasoning responses. It does not measure
production latency, establish representative workload coverage or demonstrate model
selection quality. The initial implementation supports one deliberately narrow experiment
family. A request for an unsupported experiment must stop with an evidence gap.

## Causal history and authority

Exploration events append to the existing `IntentStore` project log using its lock,
monotonic version, hash chain, idempotency and atomic replacement. No second event store
or proof lifecycle is introduced. `decision_history.explain_history` exposes the events
and their rebuildable exploration projection to the authenticated owner.

The regular intake `execute` operation refuses `preflight-event`. Only the internal
exploration service writes that event shape after validating a transition. An HTTP host
must authenticate the `Principal` and call a named service method; it must not expose the
internal append callback, take principals from JSON or accept fabricated probe receipts.
The service directory is trusted storage. Hashes do not authenticate an attacker able to
rewrite that directory.

Exploratory claims are assumptions or strategy hypotheses, linked to existing approved
acceptance IDs and previously recorded claims. Forward/cyclic references, duplicate IDs
and dependencies from a different approved scope are refused. Candidates record their
causal family, implementation, validation, likely repair, migration/reversal, assumptions
and predicted criterion values. User alternatives retain their origin and use the same
validation and comparison rules. Models cannot label their own proposals as user input.

A challenged assumption flags dependent recommendations. An invalidated assumption
marks the transitive dependent recommendation frontier for reconsideration. Unrelated
recommendations stay unchanged. An experiment can invalidate an assumption only through
an explicitly preregistered criterion/ceiling/claim mapping. A passing probe provides
support within its declared scope, never a CURRENT factory attestation.

Approved intent, exploratory support and trusted factory proof remain different things.
No module in this delivery changes the spine, manifest, authorities, merge gates, proof
reuse policy or blinded worker inputs.

## Entry points

Construct the service with the existing owner `IntentStore`, a trusted committed-context
callback, an enforcing `check_stop` callback that raises on stop/unreadable state, and the
configured App login. `inspect_repository(root, selected_paths)` supplies the context
from 1–40 explicitly selected product source files at exact Git HEAD. It parses Python
imports and definitions, records lexical JavaScript imports, and includes the protected
architecture policy and factory rules. Per-file/total read limits apply; candidate code
is never imported or executed. Analysis is explicitly incomplete for dynamic imports,
runtime dispatch and unselected files. A dirty checkout cannot supply source facts.
Full bounded policies are retained with the context; reasoning receives at most 12,000
characters per policy, labelled complete or prefix-only with the full source hash.
Missing policy coverage is an evidence gap, never permission. Prompt snapshots omit prior
reservations to avoid recursively embedding earlier prompts.

Every mutation takes this command envelope:

```json
{
  "idempotency_key": "unique-operation",
  "expected_project_version": 3,
  "session_id": "strategy-question",
  "request": {}
}
```

Methods and request bodies:

| Method | Request |
| --- | --- |
| `open` | question, optional parent_session, frozen policy |
| `add_candidates` | acyclic claims, materially different candidate records |
| `assess` | revised predictions/judgments for every existing candidate, basis |
| `experiment` | registered probe, targets, question, would_change_decision_if, claim_ids |
| `recommend` | stop_reason, rationale, remaining_uncertainty, next_useful_experiment |
| `observe_claim` | claim_id, challenged/invalidated status, observation, source |
| `reopen` | reason |
| `abandon_pending` | reservation_id, reason |
| `handoff` | programme proposal |

`inspect` is read-only. It returns the comparison, session history, shared spend and next
action class. `observe_claim` records owner-reported evidence as such: a source string is
not authenticated factory provenance. It cannot assert qualified success.

`ExplorationReasoner.advance` reserves one call, obtains one untrusted action proposal,
records it and invokes only a named checked service operation. `run` repeats this for at
most the caller's 1–8 steps, stopping on handoff, refusal, missing evidence or failed work.
It cannot autonomously approve scope, alter policy, invalidate an owner claim, dispatch
the factory, publish, merge or run arbitrary commands. The hosted intake transport has
not gained these roles.

## Selection and stopping

Before generation, policy fixes criteria, their priority ordering, any numeric ceilings,
candidate/round limits and cumulative budgets. Numeric criteria are lower-is-better in
this version. Values are either uncalibrated prediction intervals, observations from the
registered experiment, or labelled qualitative judgments. No universal score is produced.
Predicted interval midpoints establish a tentative investigation order only.

`sufficient-support` requires separation under the registered lexicographic priorities,
without an unresolved constraint interval or challenged selected claim. It is still an
UNPROVEN recommendation, conditional on the stated predictions and probe coverage.
Qualitative judgments cannot establish measured separation. A `bounded-decision` can
retain uncertainty with explicit rationale and the next useful experiment. This is a
recorded bounded preference, not a mathematical certificate that further search has no
value. Hard factory obligations remain mandatory regardless of search stopping.

New candidates and reassessments can enter an open question within its bounds. Original
predictions and losing alternatives remain in history. Revised model opinions never
overwrite measured observations. Reconsideration creates a new round; prior observations
remain historical, and changed repository context makes original predictions stale until
reassessed. This conservative implementation does not reuse measurements across rounds.

## Executable experiment boundary

`lookup-workload-v1` accepts only bounded integer arrays and the fixed `linear`, `binary`
and `hash` algorithms. There is no source code, argv, query language, import path, file,
network or subprocess parameter. Keys are limited to 2,000, queries to 1,000, and there
are at most three strategies. The runner checks stop and elapsed time during execution.
Metrics are comparisons, build_items, retained_items and matches. Logical hash lookups
are not CPU comparisons; build_items does not measure sorting complexity. These limits
and distinctions accompany the receipt.

Before execution, targets bind each candidate, criterion, metric/unit and optional claim
falsifier. The experiment's question and outcome interpretation are persisted alongside
the exact input digest. Results record actual observations, the fixed runner version and
repository context. A setup failure, exception or interruption is not a measurement or a
successful falsification. New experiment families require reviewed code and their own
execution-boundary verification; a model cannot register an executable runner.

## Spend, recovery and concurrency

All questions under one approved spec hash share the first frozen budget. Opening a
child question, changing an idempotency key, reconsidering or restarting does not reset
it. Limits are at most eight calls, $8 reserved model spend and ten million conservative
probe work units. Individual model invocations reserve at most $1 and are further capped
at five turns and 338 seconds, within existing role policy. Underlying provider automatic
retries are refused.

Reservations persist before execution; failures and abandoned work do not refund them.
Reported model cost is recorded separately. Missing, invalid or over-reservation reported
cost freezes further paid exploration. Completed request replay returns recorded state.
Interrupted work stays pending and cannot silently rerun. Explicit owner abandonment can
close pending work without refund or fabricated evidence. Unknown paid spend remains
blocked even after abandonment.

Repository identity, owner approval and stop are rechecked before consequential steps.
Concurrent project changes can refuse completion; the pending reservation then requires
reconciliation. The service does not claim distributed exactly-once execution. A new
unapproved intent invalidates the older approval for exploration.

## Programme handoff and remaining boundaries

Handoff uses the existing compiler after exploration, preserving all approved acceptance
criteria, coverage, dependency and cycle checks. It records the exact standard programme
input and hash, candidate/strategy, recommendation hash, exploratory claim IDs, approval
binding and repository context. It is explicitly `UNPROVEN`, with proof reuse false and
normal publication/admission/fresh qualification required.

`prepare_handoff(project, session_id, expected_project_version=..., principal=...)`
revalidates the stored current recommendation and invokes the existing `prepare_programme`
adapter. It returns that ordinary programme-review shape with a separate `exploration`
sidecar. Publication still needs its normal exact-input owner consent and fresh checks.

The default version `1.0` programme input remains `{version, spec, proposal, app_login}`.
Its partition and ordering can reflect exploration. The richer selected-strategy record
is a separate advisory sidecar. The staged [worker advice adapter](PREFLIGHT_WORKER_ADVICE.md)
adds opt-in `include_strategy=True` export of version `1.1`, binding the selected mechanism
and causal assumptions to programme identity and delivering them to plan/investigate and
context/design. Hosted version-aware publication still needs coordinated integration;
the sidecar alone is not consumed by a worker. Neither version grants a strategy proof
authority or requires a worker to follow an unsound mechanism.

Also not delivered here: authenticated Front Door routes/UI, hosted adaptive execution,
general model-authored executable probes, automatic classification of factory failures
as strategy rejection, selective proof reuse, trained predictors, historical retrieval,
or a production-observed A-rejected/B-qualified lap. The deterministic local scenario,
fixture adaptive driver, existing factory authority and production observation are
different evidence levels and must be reported separately.

## Validation and delivery

Focused tests cover measured choice reversal, programme conservation, owner isolation,
claim dependencies, unrelated-decision preservation, retained alternatives, new intent,
repository drift, shared budgets, stop, interruption, replay, observation precedence and
attempted model self-certification. New causal mutants remove those guards one at a time
in the production-shaped copied factory environment after a green baseline.

This addition is an exploration/observation service, not a trust-authority cutover. The
existing spine and programme admission still govern execution. Delivery follows the
normal protected maintainer lane and exact-head required checks. It neither dispatches
paid product work nor changes a deployment by itself.
