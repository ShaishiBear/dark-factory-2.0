# Preflight strategy context for factory planning

An exploration recommendation can now travel with a programme as bounded planning advice.
Without this adapter, publication preserves scope and ordering but loses the selected
mechanism and its assumptions before the factory starts planning.

## Contract

Programme input version `1.0` is unchanged, including hashes and rendered issues. Opt-in
version `1.1` requires a `strategy` field. It contains the selected mechanism and trajectory,
the complete causal ancestry of its claims, selection rationale, remaining uncertainty,
and source references to the exploration session, recommendation and repository context.
The compiler refuses missing ancestors, cycles, unrelated claims, out-of-scope acceptance
links, challenged or invalidated assumptions, self-certification, proof reuse, unknown
fields and more than 32,000 canonical bytes. Every strategy field contributes to programme
identity; changing it invalidates old issue membership and the normal issue-bound carry.

`Exploration.prepare_handoff(..., include_strategy=True)` rechecks the stored current
recommendation, approval, owner, project version, repository context and stop through the
existing preparation path, then exports a version `1.1` review input. The default remains
the version `1.0` review plus advisory sidecar. Export changes neither scope, project
history nor cumulative budget, and grants no publication capability. The opt-in review's
input and programme hashes include strategy; the retained handoff hash identifies the
earlier canonical exploration record. `Programme.to_input()` preserves both versions and
returns an independent copy suitable for version-aware publication observation.

### Publication adapter seam

The publication owner can bind a request to `project`, `session_id`, the exact
`expected_project_version`, `exploration.recommendation_sha256` and
`exploration.handoff_sha256` returned by this method. Repeated calls with the same current
state produce the same review without appending history, reserving budget or calling a
model. The caller must invoke the method itself using its authenticated owner principal;
caller-authored strategy JSON or provenance is not a substitute for this regeneration.

On reservation and immediately before publication, regenerate with
`include_strategy=True`, compare the expected recommendation/handoff references, and bind
the full returned `input_sha256` and `programme_sha256` using the existing owner-consent and
currency checks. The method requires the latest stored handoff to name the latest current
recommendation. Superseding project events fail the version fence; invalidated claims,
unapproved intent or changed repository context refuse the export. No publication effect
should follow such a refusal. Export is a read, not a durable activation capability.

`Exploration` takes a repository-context callback. A hosted adapter must implement that
callback with a fresh observation of the protected GitHub main revision, source files and
policies, not the pinned Front Door release checkout. The supplied local
`inspect_repository` helper reads its checkout's committed HEAD; it does not fetch GitHub
or prove that this is still current main. Check the protected branch before and after the
observation, and use the same canonical context representation. Context drift requires
normal exploration reconsideration and current export, not fabricated context identity.

## Worker boundary

Only the kernel's `ProgrammeQueue.admit` result supplies advice. The builder appends it to
the initial `plan` or `investigate` prompt and the `context` worker that proposes the design.
The latter is necessary because the design worker reads the compiled contract, which must
not promote strategy preference into acceptance. The validated contract hash remains first
in its prompt. Advice is absent from the rendered issue, acceptance, issue snapshot, direct
contract/architecture/test-author prompts and blinded certifier inputs. Ordinary planning
and design output may inform later workers; independent authorities still judge those
worker-produced artifacts. This does
not promise that model-generated design is uninfluenced by planning.

The planner is told to recheck assumptions against current code and record contradictions
in its plan. Source hashes identify historical context, not current qualified evidence.
An `active` causal claim is an assumption that has not been invalidated, not a proven fact.
There is no requirement to follow an unsound strategy, no automatic interpretation of
model disagreement as strategy failure, and no new skip or merge authority. The existing
contract, design, governor, RED/GREEN and independent qualification gates remain in place.

## Activation and limits

This is a staged worker/compiler contract. The hosted Front Door publication flow still
uses version `1.0`. Before activating `1.1`, its source observation, exact-input owner
consent, manifest admission and replay checks must all preserve the version and strategy
through the same normal publication boundary. Do not reconstruct a strategy programme as
version `1.0` or silently drop advice. The separate replanning comparison explicitly
refuses version `1.1` until it supports that contract. No programme is activated by this PR.

The exported advice does not contain raw private intent, model transcripts or unrelated
project claims. Full comparisons and finite-workload probe receipts remain in exploration
history; planning advice does not relabel them as production measurements. Production
outcomes for discarded alternatives remain unknown. Automated trusted strategy-failure
classification and an observed A-rejected/B-freshly-qualified factory cycle remain later
integration work.

## Acceptance

- Old inputs retain their exact identity; version `1.1` round-trips without loss.
- Changes to mechanism, assumptions or source identity change programme identity, and the
  current queue refuses old issue membership.
- Invalid scope, forged proof status and incomplete or circular dependencies fail closed.
- The real builder path, driven with fixture workers and deterministic gate doubles,
  sends advice to plan/investigate and context/design only and still traverses its existing gates.
- Export preserves approval, currency, owner and stop checks, and consumes no budget.
- Causal mutations must turn a green focused suite red with an assertion failure; an
  import or setup error is not accepted as detection.

These are deterministic fixture checks, not live paid-model or production qualification.
