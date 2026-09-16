# Reasoning Preflight: choose what deserves qualification

Preflight answers which technical hypothesis deserves the cost of proof. A successful factory
build establishes that its candidate meets the qualification contract; it does not establish
that another strategy would have been worse. This layer records a bounded comparison while
keeping every candidate and handoff `UNPROVEN`.

## What “better” means

Before generation, the authenticated caller registers a question, decision criteria, any
priority ordering, an explicit tie-break and a budget. The registry binds the latest owner
approval, compiled programme item, exact repository commit and protected policy snapshot.
Applicable spec constraints, the item's acceptance criteria, non-goals, architecture policy
and factory rules are included automatically. A model cannot remove them or rewrite the
comparison policy after seeing its preferred solution.

The system generates materially different causal approaches, including one minimal-change
baseline, then makes a separate challenge call. The challenge sees the same criteria for
every candidate, without user/system attribution or the baseline marker. It identifies
cosmetic strategy families, constraint conflicts, judgments and decision-changing uncertainty.
Both calls remain untrusted reasoning and may share a model; separate calls are not a claim
of statistically independent errors or qualification authority.

There is no universal numeric score. The compiler counts declared files, dependencies and
top-level areas and checks paths against a committed file inventory. These are plan properties,
not measurements of future performance, actual implementation cost or architectural coupling.
Model judgments use explicit qualitative categories with reasons and cannot establish Pareto
dominance. Actual unknowns block selection. Assessed constraint conflicts screen a candidate
out of this comparison; they remain model assessments, not trusted invalidity certificates.

Plan dominance is allowed only across established declared-plan dimensions with no material
uncertainty and no judgment dimension. Otherwise, registered priorities can establish a
provisional preference. Equal candidates may use the explicit baseline tie-break; a real
tradeoff cannot be erased by it. Without a priority that resolves the tradeoff, the result is
`needs-priority`. Every candidate survives in the receipt with its assessment, conflicts,
dominance basis or priority trace. User candidates use identical rules and retain their origin
in owner-visible records.

For example, one strategy can change fewer files while another adds fewer dependencies.
Neither dominates. A pre-registered dependency priority can select the latter. If either
strategy's latency is unknown and could reverse the decision, the result is `needs-evidence`.
The next useful experiment must state the workload, threshold and result that would change
the choice before it runs. Executing that experiment is outside this delivery.

Every result says `global_optimality: not-established`: an unexplored solution may be better.
Meaningful strategy diversity, common comparison criteria and discriminating measurements
improve the decision. Generating more cosmetic variants does not. Later calibration must
compare predictions with real qualification outcomes and representative alternative probes;
a winning solution passing alone cannot reveal the unobserved counterfactual.

## Callable integration

`PreflightPreparation(store, provider, context, app_login=..., check_stop=...).prepare(project,
command, principal=authenticated_owner)` reuses `IntentStore` authorization, programme
compilation and the existing private preparation-record writer. The principal must originate
in authenticated transport, never JSON. No Front Door route or hosted transport is activated
by this delivery. New tool-less roles are separate from `INTAKE_ROLES` and from proof authorities.

The command contains:

- `idempotency_key`, `expected_project_version`, `approval_version`, `spec_sha256`;
- a normal programme `proposal` and the selected `item_id`;
- the frozen `policy`, `user_candidates`, and nullable `direct_candidate`.

Policy fields are `question_id`, `question`, `signals`, `criteria`, `priorities`, `tie_break`,
`max_candidates`, `budget`, and nullable `direct_reason`. Criteria are `{id, kind, question}`,
where kind is `planned-files`, `new-dependencies`, `planned-top-level-areas`, or `judgment`.
Smaller plan counts are preferred; judgments use favourable/mixed/adverse/unknown. Priorities
name criteria in order; an empty order preserves genuine tradeoffs. Tie-break is
`retain-tradeoff` or `prefer-baseline-if-equal`.

The policy is supplied by the authenticated host/caller before generation, not synthesized or
silently approved by this engine. The host must ground its criteria and material preferences
in approved scope. Semantic completeness of criteria or trigger signals is not mechanically
guaranteed. New evidence may justify a new registered decision, never an in-place policy edit.

Candidate fields are shown in `preflight_prepare.CANDIDATE_SHAPE`. Assumptions require a statement
and a concrete revisit condition. Result assumption IDs bind those records to the registration
and candidate. Selected assumptions are referenced by the advisory handoff. This is groundwork
for affected-only reconsideration, not an active invalidation/repair mechanism.

## Bounds and modes

- Direct: one caller-supplied strategy touching one existing file, no new dependency, no
  declared/detected trigger, and an explicit reason for skipping exploration. Zero model calls.
  Direct status does not assert that the supplied strategy is mechanically correct.
- Light: 2–4 candidates including supplied user candidates, one generator and one challenger.
  Exact renamed copies are refused; semantic family grouping remains an untrusted judgment.
- Deep: declared security, irreversible migration, distributed state or difficult rollback
  signals return `needs-deep-analysis`. Conservative path rules also detect protected/auth,
  migration and API surfaces and multiple directories/new dependencies in declared plans.
  A newly detected deep signal stops before further reasoning spend. This is not a complete
  security or architecture analysis; trusted factory authorities still judge the implementation.

One registered decision permits at most two worker invocations, $2 in total reservation and 676 seconds
of reasoning wall time; callers may lower dollar/wall bounds. Each call reserves half the
decision's dollar allowance before invoking the provider, in an empty temporary directory
with no tools, at most five turns and 338 seconds. This wrapper's per-call dollar cap is at
most $1, below the roles' protected $2 ceiling. Provider-reported costs are retained separately
from conservative reservations; they are not asserted to be billing reconciliation.
The call count means bounded worker invocations, not a claim about the number of underlying
HTTP requests or model turns within each invocation.

Unknown/over-reservation cost, time exhaustion, provider failure or malformed output stops
the session. There is no automatic retry or recovery request. A durable pending reservation
survives interruption, and the same request returns its record without another call. A new
request key cannot restart the same project-version/item/question slot. Provider adapters must
honor request bounds; configured automatic retries are refused.

Stop and current owner scope/repository identity are checked before spending and again before
handoff. A new intent invalidates its older approval for preparation. Cached records cannot
be reused against changed context. Committed context reads only fixed policy files and a
bounded Git inventory, never uncommitted files or model-selected file contents. Sessions and
outputs have explicit size bounds and are private derived preparation artifacts; they do not
replace the canonical intent log or create an approval lifecycle.

## Handoff and remaining work

The advisory handoff names exact spec/programme/item/repository and registration/candidate/
decision hashes, selected assumption IDs, `qualification_status: UNPROVEN`,
`proof_reuse_allowed: false`, and `requires-normal-admission-and-fresh-qualification`.
It is not an execution capability and is not yet consumed by programme publication/admission.
The production factory's admission, scope, budgets, independent authorities, stop and merge
checks remain mandatory and unchanged.

This delivery adds no F0 historical retrieval, runtime measurements, executable probes,
Virtual Factory predictions, automatic reconsideration, deployment or paid canary. Scope and
repository drift are detected conservatively, without claiming complete semantic dependencies.
Front Door policy/recommendation presentation and explicit admission integration remain with
the coordinating task. Probe isolation, cumulative cross-decision/programme budgets and
evidence provenance must precede automatic experiments or reconsideration loops.
