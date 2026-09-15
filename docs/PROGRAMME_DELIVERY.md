# Programme delivery checkpoint — 15 September 2026

## Implemented and locally tested

Branch: `codex/programme-admission`, continuing `b4c895fdca9e8195d1ba66059e11b912996bb18e`.
Base at review: `c692f2de86cc1f6eb145b3c42973bbdfbe497702`.

The deterministic programme compiler and App projection now include fresh build-publication
credentials, a digest-bound handoff between qualification and merge jobs, current merge
authorization revalidation, and completion checks for the actual App merger and repository.
Stop requests at both publication checkpoints release claims without charging a failed attempt.
The initial App mint explicitly requests the three required write permissions, so missing
Issues-write access fails before candidate creation or paid work. This is implemented preflight,
not yet evidence that the installed App has that permission.

Local WSL validation, with the existing frozen toolchain:

- Canonical `python harness/ci.py --quick`: `STATIC_OK checks=7`,
  `UNIT_PASSED tests=2461`, `GATE_OK mode=quick`.
- The final explicit-permission workflow edit then passed 125 focused authority/workflow tests.
- Seven targeted mutations were caught after a green baseline: dispatch and merge checkout
  credential persistence, dispatch and merge Claude CLI pinning, both pgvector services, and
  the contract-derived PR body. These are selected detectors, not a full mutation-family result.
- Raw local logs: `.validation/continuation-quick.log`, `.validation/continuation-focused.log`,
  `.validation/targeted-mutations.log` in the implementation worktree. They are diagnostic
  evidence; they do not replace protected required checks or the full product qualification.

No floor, proof requirement, model budget or measured ladder duration was lowered. Hosted job
ceilings are 360 minutes for dispatch and 210 for merge/post-merge; the full proof budgets are
unchanged. The two jobs hold the existing single workflow concurrency lock.

## Approved scope and delivery state

The owner's explicit **“Approve both citation fixes”** response is recorded in
`docs/PROGRAMME_ACTIVATION.md`. The inactive input is
`.factory/programmes/citation-inspection.approved.json`:

- Specification SHA-256: `2ebd0a37d772950d9364c1178f7c6330c6c0cd12367a479ef1eebc7a66f0bbb5`.
- Compiled programme SHA-256: `d809b087a48a5d3a634286dc5f303d90ce119139a799ace0a122a7e244766b00`.
- Transcript snippet and multiline rendering first; modal focus entry/restoration second.

At this checkpoint: implemented and locally tested; not deployed, not activated, and no full
programme cycle observed. Delivery requires a normal maintainer PR judged by the existing
base-anchored trust-root authority and the head quick authority. Activation follows in a
separate reviewed change installing the approved input as `active.json`.

Record subsequent PRs, exact heads, run IDs and outcomes here or in the main continuation
handoff. Prove App creation, ordinary triage, API build, independent qualification, exact-head
App merge, full post-merge validation, completion receipt and dependent progression before
describing the factory as operational. One execution is not reliability evidence.

## Remaining work and boundaries

Front Door, bounded replanning, general durable learning, CLI-free workers and governed
self-maintenance remain outstanding. Follow the target architecture through the current
compiler/admission and trusted authority boundary; do not create another issue-writing queue.

The old Claude Max supervisors remain disabled. PR #176 and unrelated main-checkout work
remain untouched. No AWS transfer or new resource is involved; the existing $7/month ceiling
stands. Missing evidence blocks progression; it never licenses floor promotion or bypass.
