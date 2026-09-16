# Claim/evidence foundation checkpoint — 2026-09-16

This task owns the claim explanation and decision-history projection only. It does not own
Front Door deployment/publication, HTTPS, owner controls, or `NEXT-CHAT-HANDOFF.md`.

## Source and ownership

- Isolated worktree: `.worktrees/claim-evidence-foundation`.
- Branch: `codex/claim-evidence-foundation`.
- Base freshly fetched and matched to `git ls-remote origin refs/heads/main`:
  `0651a23a08113efc0147c885d0e46cec4ab02540` (renewal timeout repair, PR #193).
- Architecture reference: `c1f7ef0187870d82d69b1b3fa7941ee266fa133b` on
  `origin/architecture/dark-factory-2-target`; reference documents only, no runtime replacement.
- Main user checkout remains `human/review-corrections`, unrelated changes preserved.
- Task `Continue Dark Factory implementation` (`01a0a53b-ed61-7573-b5ce-e3dab7be9e77`)
  explicitly confirmed no ownership conflict and accepted the JSON-ready read-only seam.
  It will review before integrating. It is separately repairing control-issue provenance;
  preserve both tasks' appended mutation registrations if main advances.
- No operation on issue #189, abandoned supervisor, deployment or paid experiment.

## Decisions and delivered interface

See [interface and semantics](CLAIM_EVIDENCE_FOUNDATION.md).

`claim_explanation.explain_run(artifact_root, policy_path, expected_head_sha,
expected_base_sha, current_claim_hashes=None)` uses keyword-only arguments and returns a
`dark-factory/claim-explanation` 1.0 projection. Inputs are host-selected retained artifacts,
protected policy and freshly observed expected identities. No network/effects, new proof issuer,
trusted lifecycle, selective reuse or qualification changes. Omitted current hashes stay unknown.

`decision_history.explain_history(store, project, principal=authenticated_owner)` returns
`dark-factory/decision-history` 1.0 from the existing canonical locked IntentStore read.
All historic proposals/assumptions/questions and exact approval bases survive. Owner-only
visibility; hashes do not authenticate an attacker who owns the store directory.

No CURRENT proof is claimed: even intact retained bundles lack complete issuer, authority-program,
toolchain and live-world identity. Record integrity, recorded dependency currency and proof status
are separate. This conservative adapter precedes any dependency-validation or authority migration.

## Retained real-run observation

Observed local retained artifacts for run `35015446482`, attempt `1`, issue `181`, PR `184`.
The bundle's bytes match its existing retained trajectory archive reference:

- Source/base revision: `397c9be7850f6ca5263e6553cfdbd31e2edab847`.
- Exact qualified subject named in the bundle: `dc0bdf429236e69bd6bf9f68db9bcf4ef4daca14`.
- Bundle SHA256: `2efe989f4af1e750696834a99d70293202ebc412fcd55e0ab928a7f01f7f0d38`.
- Archive reports workflow success, but the retained artifact directory lacks
  `spine/run-manifest.json` and its referenced claim files.
- Projection result at that historical subject: **21 insufficient claims, 21 unknown currency
  comparisons, manifest-absent explicit, proof reuse false**. No reconstruction or invented evidence.

This is an observation of retained local files and their hash agreement, not fresh platform
authentication, fresh qualification, a reliability baseline or evidence that today's main is proven.
Repro script and full JSON are in this worktree's ignored `.validation/observe.py`,
`retained-success-explanation.json` and `retained-receipt.json`.

## Validation and delivery state

Final focused Linux suite: **27 tests passed**. **Eight registered causal mutations caught** after
a green copied baseline. Mutation anchors valid (516 factory, 9 application). Final reviewed
source passed the canonical quick gate: **2,629 unit tests, seven static checks, GATE_OK mode=quick**.
This is local maintainer validation, not full product qualification or live deployment.
Prepared for the normal protected-main PR process; publication details are in the separate local
delivery receipt `docs/atlas/reviews/CLAIM-EVIDENCE-HANDOFF.md` in the main user checkout.

The first Windows test invocation failed on sandbox temporary-directory permissions before
exercising behavior; Linux is the repository's canonical environment. Old `/tmp` tool directories
were absent after WSL restart. This task created a persistent isolated Linux clone and pinned
tools/locked dependencies at `/home/yisha/.cache/dark-factory-claim-evidence-20260916`.
The Front Door task was given read-only dependency reuse paths, not ownership of this clone.
The initial quick run passed backend/factory but the system Node could not load the unchanged
frontend PostCSS module. A separate Node 22.14.0 runtime resolved that environment mismatch.
Final validation used Python 3.12, uv 0.12.5, Bun 1.4.0, Node 22.14.0 and frozen dependency locks.

Final logs remain in `.validation/{focused,final-mutants,final-quick}.log`; earlier setup/failure
logs remain alongside them. The canonical maintainer PR path must still
run both required hosted checks under current protected-main authority. No bypass is permitted.

## Next bounded work

Front Door integration belongs to the coordinating task. Preflight and reconsideration remain
subsequent milestones, not implemented here. Before probe execution, establish isolation and
cumulative budgets. Before selective proof reuse, validate dependency coverage against retained
and adversarial evidence. Keep the normal admission and fresh qualification boundary intact.

## Follow-on delivery: evidence retention — 2026-09-16

The owner authorized the next bounded slice. Worktree `.worktrees/claim-evidence-retention`,
branch `codex/claim-evidence-retention`, was created from foundation main
`c252828b164816fc752566cf5f5da328c52e3058` and rebased onto current main
`146c4bb13b63062fe7aa53c46409e1508fefa885` after PRs 198, 199 and 200. Those deliveries are
preserved. The coordinating task still owns Front Door and deployment. Issue 189 is untouched.

`docs/EVIDENCE_RETENTION.md` describes the fixed-path, bounded 90-day evidence/index uploads,
packaging-time source observations and sanitized metadata in the existing trajectory archive.
The underlying loss was the worker's top-level JSON upload omitting nested spine/independent
files. Raw proof does not enter the public archive, and retention cannot authorize reuse.

Validation: 73 focused tests; nine new causal mutants and the existing proof-job fail-closed
mutant caught after a green copied baseline; canonical quick gate passes 2,667 unit tests and
seven static checks. The continuation workflow test now permits only the six named retention
steps to ignore errors; both dispatch-step and proof-job error masking are causally detected.

Read-only repack of retained run 35015446482/1 preserved four files (20,193 bytes), the archived
bundle hash, and the exact explanation. The missing manifest stayed absent; all 21 claims stay
insufficient and reuse remains false. No paid run, fresh qualification or platform upload was
used as a canary. An ordinary future worker run must still demonstrate actual artifact delivery.

Logs and receipt: this worktree's ignored `.validation/{focused,mutants,quick}.log` and
`legacy-retention-receipt.json`. Linux validation checkout:
`/home/yisha/.cache/dark-factory-evidence-retention-20260916/repo`, with the foundation tools
and locked dependency directories reused read-only. Final PR/merge receipt remains in the
separate main-checkout `docs/atlas/reviews/CLAIM-EVIDENCE-HANDOFF.md`, never `NEXT-CHAT-HANDOFF.md`.

## Follow-on delivery: reasoning Preflight — 2026-09-16

The owner asked to start Preflight and how to know whether an alternative solution would have
been better. The delivered comparison contract is deliberately bounded: best-supported among
the considered strategies, with no claim of global optimality or qualification. Required
criteria are registered before generation; unknowns, tradeoffs and reversal conditions remain
explicit. A selected strategy passing qualification cannot establish an unobserved alternative's
counterfactual outcome.

Worktree `.worktrees/preflight-foundation`, branch `codex/preflight-foundation`, started from
retention main `b5ceb64d6f3b67f3bb237ea15d36917335e5cf74` and was rebased onto
`9d8baa4a9cdfc056c3aac0fb453ea288466181c1`, preserving coordinating PRs 203–205.
Target architecture reference remains `c1f7ef0187870d82d69b1b3fa7941ee266fa133b`, especially
`DARK_FACTORY_2_PROGRAMME_AND_PREFLIGHT.md` sections 9–40.

New modules: `preflight.py` (frozen policy, equal assessment, plan facts, conservative comparison),
`preflight_context.py` (fixed committed policy and inventory reads), `preflight_prepare.py`
(owner-scoped derived records, bounded generator/challenger, fresh currency/stop and UNPROVEN
advisory handoff). Separate tool-less roles do not gain existing hosted intake transport.
`docs/PREFLIGHT_FOUNDATION.md` defines the callable integration and remaining limitations.

Direct mode uses zero model calls. Light mode has 2–4 strategy slots and at most two bounded
worker invocations with $2 total reservation and 676 seconds. Deep signals stop explicitly.
Each invocation receives the protected role limits, then is narrowed to the decision's remaining
budget/time. The new construction site is registered in the existing authority-boundary tests;
no existing assertion is bypassed. Unknown spend and pending/failed requests are never retried.

Final validation on rebased implementation: 67 focused tests, 15 registered causal mutants
caught after a green copied baseline, canonical quick gate passes 2,748 unit tests and eight
static checks. The initial full run caught the unregistered AgentRequest construction site;
that site is now governed by the same AST policy checks and additional cumulative-budget tests.
All model responses in these tests are fixtures: live decision quality is not calibrated here.

No paid call, disposable probe, factory dispatch, deployment, publication/admission activation,
issue 189 action, or Front Door/UI modification. Front Door integration remains with the
coordinating task; preserve its ownership. No reconsideration loop or proof reuse is enabled.
Existing scope, independent qualification, budgets, stop and exact-head merge remain authoritative.

Receipts in `.worktrees/preflight-foundation/.validation/`: `focused.log`, `mutants.log`,
`quick.log`, `pre-sync-quick.log`. Isolated Linux checkout:
`/home/yisha/.cache/dark-factory-preflight-20260916/repo`, reusing the foundation tools and
locked dependency directories read-only. Final PR/merge receipt is in the separate shared
`CLAIM-EVIDENCE-HANDOFF.md`. Next: authenticated presentation/admission integration, then
probe isolation, cumulative cross-decision budgets, measured comparisons and affected-only
reconsideration under fresh qualification.
