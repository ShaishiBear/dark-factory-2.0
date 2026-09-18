# Dark Factory capability matrix, blueprint transformation of 2026-09-17

Status of each work package of `docs/implementation-blueprint-2026-09-17/` as of 2026-09-18, in the five states the
blueprint distinguishes. "Implemented" means code on `main`; "tested" means the repository's own detectors and causal
mutants; "merged" names the PR; "deployed" means the hosted worker runs it from `main`; "observed" means a hosted run
exercised it and the record exists. Checkpoint documents under `docs/atlas/reviews/BLUEPRINT-*.md` carry the evidence
pointers; nothing here is a claim beyond them.

| WP | Delivered part | Implemented | Tested | Merged | Deployed | Observed |
|---|---|---|---|---|---|---|
| WP00 | Read-only `plan` job before any paid work; diagnostics retained on every path; early diagnostics directory | yes | yes | #239, #241 | yes (`dark-factory-worker.yml`) | yes: scheduled run 35294669709 (2026-09-18 01:16 UTC) answered idle and the dispatch job was skipped (transcribed from `gh run view`; that the plan job starts no service, toolchain or paid call follows from the workflow's job structure, not from the run record) |
| WP01 | One append primitive over the intent journal (`project_events`), repository profile, exact subjects and claims | yes | yes | #240 | yes | no hosted decision has been appended since |
| WP02 | Validation meter (spend classes, one reservation bundle), provider gateway contract, billing reconciliation, metered diagnostic probes; the offline CLI compatibility spike | yes (accounting part; spike) | yes | #245; spike PR pending | yes | no: the metered test-author route cannot run while the workflow withholds the credential; no allowance exists. The spike ran once locally against Claude Code 2.1.259 (compatible; the CLI follows a cross-origin 307 with its credential) |
| WP03 | Transition phase machine, receipts journal, release decision, `transition-status` reader; the eight public `transition_release` vectors | yes (pure part) | yes | #247 | yes (no workflow or runtime path calls it; the CLI reader exists) | no transition exists |
| WP04 | Claims, source subjects, shadow allowed-actions compiler, project graph, `explain-claims` / `plan-obligations` | yes | yes | #240, #243 | yes (shadow; the plan job does not run `plan-obligations`) | no |
| WP05 | Attestation envelopes, proof currency, companions, proof store partitions, authority profiles | yes | yes | #242 | yes (the spine script emits companions) | no hosted validation has run since |
| WP06 | `.factory/tcb.json` record and verifier, entered into the host's authority closure; no broker, no worker view, no reduction claimed | measurement only | yes | #248 | yes (the host reads the record from `main`) | no |
| WP07 | Lease/grant store over SQLite with the pure `lease_guard`; no coordinator, no executor | store only | yes | #244 | yes (nothing calls it) | no |
| WP08 | Preflight experiments, predictions, research | no | | | | |
| WP09 | Outcome-to-reconsideration loop | no | | | | |
| WP10 | Decision and implementation graph UI | no (`claim_views`, `transition_views` are CLI projections) | | | | |
| WP11 | Lesson admission evaluator with a strict protected policy; the investigation host records every proposal's evaluation | evaluator only | yes | #246 | yes | no investigation has run in the hosted lane |
| WP12 | Governed self-maintenance | no | | | | |
| WP13 | This matrix and the FACTORY.md "Blueprint modules" section | partial (documentation of implemented behaviour only) | n/a | docs PR pending | n/a | n/a |

Conformance: all 68 public vectors of `docs/implementation-blueprint-2026-09-17/conformance/` pass through production
code via `tests/factory/blueprint_adapter.py`, recorded at main d6bc4bb after PR #248
(`docs/atlas/reviews/local/2026-09-18T0530Z-blueprint-docs/evidence/conformance-at-d6bc4bb.log`; the #247 branch head
ce97eaf recorded the same) (`python docs/implementation-blueprint-2026-09-17/conformance/run.py
--adapter tests/factory/blueprint_adapter.py`). Passing them is necessary for the named interfaces and sufficient for
nothing else.

Known blockers outside this repository's lane: the Front Door host (owner: the Front Door task) holds the intent store,
the operational SQLite database and the fence acquisition lane, so the WP07 coordinator, the WP03 transition service and
any allowance are host work; the hosted worker has no allowance, so every scheduled run is expected to answer idle.
