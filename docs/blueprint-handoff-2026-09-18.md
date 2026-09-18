# Blueprint handoff, 2026-09-18

The state of `docs/implementation-blueprint-2026-09-17/` after twenty-two checkpoints on the maintainer lane, in the five
states the blueprint distinguishes (implemented, tested, merged, deployed, observed), with exact evidence and the domain
limitations that remain. Nothing in this document inherits a green badge from another row: new-product readiness,
calibrated learning and governed self-maintenance each stand only on the evidence named against them, and none of the
three has hosted or production evidence.

Vocabulary: "implemented" means the code is on `main`; "tested" means the repository's own detectors and the causal mutants
in `harness/factory_mutations/defects.json` (each caught as a failure of a property assertion, recorded in the checkpoint's
evidence directory); "merged" names the pull request and squash commit; "deployed" means the hosted worker runs the code
from `main` when it runs; "observed" means a hosted run exercised the capability and the record exists. The per-package
matrix is `docs/factory-capability-matrix-2026-09-18.md`; the module inventory is the "Blueprint modules" section of
`FACTORY.md`; the checkpoint documents are `docs/atlas/reviews/BLUEPRINT-*-CHECKPOINT-2026-09-1[78].md` in the maintainer's
local atlas with their evidence directories under `docs/atlas/reviews/local/2026-09-1[78]T*Z-blueprint-*/evidence/`.

## Main sequence

Every change went through a draft PR opened under the maintainer's account, the `quick-authority` and
`trust-root-authority` checks at the exact final head, `gh pr ready`, and the `unattended-merge` squash. No protection
was bypassed, no proof requirement weakened, no paid allowance increased, no infrastructure provisioned.

#239 (WP00) -> #240 (WP01/WP04) -> #241 (WP00) -> #242 (WP05) -> #243 (WP04) -> #244 (WP07 store) -> #245 (WP02) ->
#246 (WP11 evaluator) -> #247 (WP03) -> #248 (WP06 record) -> #249 (docs) -> #250 (WP02 spike) -> #251 (spike record) ->
#252 (WP02 transport) -> #253 (WP06 capabilities/broker) -> #254 (WP06 publish) -> #255 (WP08 registry) -> #256 (WP08
predictions) -> #257 (WP09 router) -> #258 (WP10A endpoint) -> #259 (WP11 calibration) -> #260 (WP11 experience) ->
#261 (WP12 maintenance) -> #262 (WP10B UI) -> #263 (WP08 contained runner). Main at this handoff: 4986fc8 (the #263
squash, merged 2026-09-18T16:08:50Z).

## Per package

| WP | What exists on `main` | Tested | Merged | Deployed | Observed | What does not exist |
|---|---|---|---|---|---|---|
| WP00 | Read-only `plan` job before any paid work; diagnostics retained on every path | yes | #239, #241 | yes (`dark-factory-worker.yml`) | yes: run 35294669709 answered idle and skipped dispatch | nothing owed by the blueprint |
| WP01 | One append primitive over the intent journal (`project_events`), repository profile, exact subjects and claims | yes | #240 | yes | no hosted decision appended since | a second journal |
| WP02 | Validation meter, provider gateway contract, billing reconciliation, metered probes; offline CLI compatibility spike with a daily hosted record step; loopback gateway transport behind the validation launcher (off: no policy block registered) | yes | #245, #250, #251, #252 | yes (transport off) | the daily spike record had not fired on 18 Sept (cron lateness recorded); no metered validation run since | allowance visibility to the owner; any paid allowance; the daily record's first hosted appearance |
| WP03 | Transition phase machine, receipts journal, release decision, `transition-status` reader; the eight public `transition_release` vectors | yes | #247 | yes (nothing calls it) | no transition exists | the transition service and its workflow (the Front Door host lane) |
| WP04 | Claims, source subjects, shadow allowed-actions compiler, project graph projection, `explain-claims` / `plan-obligations` | yes | #240, #243 | shadow | no | claim scheduling in the plan job |
| WP05 | Attestation envelopes, proof currency, companions, proof store partitions, authority profiles | yes | #242 | yes (companions emitted) | no hosted validation since | proof reuse across runs |
| WP06 | `.factory/tcb.json` record and verifier (authority closure 229620 of 400000 at #263); `capabilities.py` grants; in-process `effect_broker.py` on the real merge and publication paths (`merge_exact_head`, `publish_candidate`, `observe`) | yes | #248, #253, #254 | yes (every kernel merge and publication from `main`) | no hosted merge or publication through the broker since | a broker process, the worker view (`worker_view.py`), any TCB reduction claim, the process boundary for tool-bearing workers |
| WP07 | Lease/grant store over SQLite with the pure `lease_guard`; used by the WP08 contained runner for the experiment slot | store and one consumer (10 mutants on the store) | #244, #263 | yes | no | coordinator, executor, pipelines (the host lane) |
| WP08 | Experiment registry (four families, two runnable offline); predictions frozen before results with outcomes on every measurement; contained runner: every registered experiment runs in a fresh interpreter with an allowlisted environment, empty cwd, wall clock, one JSON in and out, receipt re-verified per family, under a lease, every attempt recorded | yes (registry 7, predictions 5, runner 8 mutants) | #255, #256, #263 | yes | no hosted exploration session has run an experiment | research; the two sandboxed families; an OS/container sandbox; parallel experiments |
| WP09 | Outcome router: six structural classifications of an authenticated outcome, recorded on every strategy assessment and on rule-less imports; refusal prose never read | yes (5 mutants) | #257 | yes | no hosted outcome imported | new predicates; automatic reconsideration; coordinator wakeup |
| WP10 | Read-only bounded graph endpoint with exact deltas, resnapshot instructions and authorized details; the static decision-graph UI (five views, SVG plus list, keyboard, details, exact client deltas, polling paused while hidden, predictions never shown as built, unproven never green); a local agent-browser journey that passed in a real browser including a restart over the same store | yes (5 + 8 mutants; Node-run pure functions) | #258, #262 | yes (any Front Door built from `main`) | no owner has opened the hosted page | observation-fed animation; a browser render at the 2000-node bound; the journey in the canonical ladder |
| WP11 | Lesson admission evaluator under a strict protected policy (nine gates in a fixed order, unknown never admits); calibration report with strict in-force joins, exact arithmetic, preregistered cohorts (`harness/experiments/learning_protocol.json`, evaluation cohort empty by declaration), admission observations the evaluator decides on; experience packets, role-scoped and bounded, at the one worker payload funnel, blind roles empty before any search, stage records by reference only | yes (evaluator 7, calibration 8, experience 9 mutants) | #246, #259, #260 | yes (every worker stage passes through the funnel) | no investigation in the hosted lane; no lesson ever admitted; no held-out cohort | a persistent lesson store; an installed lesson policy; retrieval-on versus retrieval-off comparison; any learned-improvement claim |
| WP12 | Deterministic maintenance classification against the protected path/effect policy (tier from paths and effects, a description cannot lower it, facts only add refusals, unknown refuses activation); reviewable maintainer proposals from digest-consistent incidents; old-authority shadow plan and disagreement blocker as records; cutover refused under the installed policy (no lane activated) | yes (9 mutants) | #261 | yes (module and installed policy on `main`) | no proposal outside tests | patch generation; the maintenance workflow; any run-path caller; any lane activation; a self-maintenance demonstration; guard protection for the two policy files (escalated) |
| WP13 | This handoff, the capability matrix and the FACTORY.md module inventory | documentation | this PR | n/a | n/a | the integrated new-project demonstration (needs the owner's product outcome and paid cap); the task cohort and metrics registration; the one-operator contract experiment and the coupled multi-file comparison on an unseen task |

Conformance: all 68 public vectors under `docs/implementation-blueprint-2026-09-17/conformance/` pass through production
code via `tests/factory/blueprint_adapter.py` (a thin adapter, no fixture answers), last recorded at main d6bc4bb; the
factory suite runs 2996 tests locally with 32 failures and 2 errors that predate this work and are confined to
`test_factory_bootstrap` and `test_evidence_retention` on Windows (the same set on every checkpoint's run; no run on a
Linux host is recorded here).

## Escalations: decisions that are the owner's, recorded and not taken

1. Guard protection for `.factory/maintenance-policy.json` and `.factory/lesson-policy.json`: the security guard's
   protected set does not list them, and FACTORY_RULES section 5 ties the guard's list to the document, so adding them
   is a governance amendment through section 12.
2. A paid allowance and a fresh product outcome for the WP13 demonstration, the WP08 paid adaptive reasoning run and any
   real A-to-B lifecycle: none exists; the hosted worker answers idle by design.
3. A held-out project or cohort for WP11 evaluation and any learned-improvement claim: the learning protocol declares
   the evaluation cohort empty.
4. Activation of an autonomous maintenance lane (WP12) and the trust-migration classification for reuse or authority
   replacement: an Architecture Change Proposal for the maintainer lane.
5. The Front Door host lane (intent store host, operational SQLite, fence acquisition, transition service, coordinator
   and executor): owned by the "Continue Dark Factory implementation" task, whose `transition-fence` worktree drafts were
   left untouched.
6. The daily regression cron and the hourly worker cron did not fire on schedule during this window (recorded, not
   dispatched; never dispatched by hand because that lane is paid).

## Remaining domain limitations

- Containment is process- and environment-level (WP08 child, WP06 in-process broker); no OS or container sandbox exists,
  and no worker view separates the judge's view from the builder's at the OS boundary.
- Learning is instrumented (forecasts frozen, outcomes joined, admission gated, packets bounded) but has produced no
  admitted lesson and no benefit measurement; the calibration report over the only ledger says so.
- Governed maintenance classifies and proposes; it evaluates no candidate and delivers no change.
- Every "observed" column that says no is a real absence: the hosted lane has not exercised any blueprint capability
  beyond the read-only plan job, because no allowance exists and the crons were late.
- The decision-graph UI has been seen in one local browser journey by the maintainer's assistant, not by the owner.

## Coordination

The Front Door task's `.worktrees/transition-fence` (fence admission, currency, effects, observation drafts and a
coordination note) was read and never modified; its supervisor and deployment were never operated. Ownership
boundaries were stated in the first checkpoint and held throughout.
