# Deviations from the Revision 4 specification

`docs/implementation-review-2026-09-18/implementation-contracts/README.md` asks that, where an
actual incompatibility makes a prescribed detail impossible, the exact counterexample and the
smallest invariant-preserving replacement be recorded here. Everything below is a detail-level
adaptation. No storage system, trust boundary, retry model, promotion metric, product
architecture or ownership split is changed.

## D1. Six mutation defects re-anchored (P0)

**Prescribed.** `INTEGRATION_AND_DELIVERY.md` P0: "Keep existing floor and all 950 audited
mutant definitions unless an independently reviewed invalid mutation is evidenced."

**Counterexample.** Five of those definitions anchor into the exact text of
`harness/factory_mutations/run.py` functions the same P0 section requires be rewritten
(`evaluate_all`'s worker-pool call, `run_tests`' stop-at-first-red branch, the failure-naming
loop), and one (`maintenance-kernel-paths-not-trust-root`) anchors into the
`factory_kernel/maintenance.py` condition the governance unit extends. After the prescribed
rework `python harness/mutation_anchors.py` reported them as uninjectable: the anchors named
code that no longer exists.

**Adaptation.** Each defect keeps its id and the property it stands for; only the `find` and
`replace` text moves onto the lines that carry that property today. One `why`
(`factory-mutations-stop-before-a-green-suite-finishes`) additionally now names the detector
that catches it, because the property moved from `run_tests` to `evaluate` and its detector
moved with it. No defect was removed, none was weakened, and all six were kill-checked one at
a time in their own copies after re-anchoring. `MUTATION_ANCHORS_OK factory=956`.

## D2. `FACTORY_MUTATIONS_NOT_INJECTED` retained alongside the new states (P0)

**Prescribed.** P0 lists exactly five states: `caught`, `survived`, `infra_error`, `timeout`,
`not_run`. "Not injected" is not among them.

**Counterexample.** `harness/observe.py` and `scripts/factory_evidence_spine.py` both parse
`FACTORY_MUTATIONS_NOT_INJECTED=<n>` from the family's output, and the spine's acceptance
requires it to be zero. Dropping the marker would make the evidence spine unable to read a run
it is required to judge.

**Adaptation.** A mutant whose anchor no longer matches is recorded in the new vocabulary as
`infra_error` — nothing was observed about it — with the reason `anchor missing/non-unique` in
its own diagnostic file. The historical marker is computed from those reasons, so its meaning
is unchanged and its consumers are unchanged.

## D3. An entirely red baseline still refuses (P0)

**Prescribed.** "Run each detector on the unmutated exact environment before accepting its
failures as kills... a baseline failure prevents that detector from proving a kill."

**Counterexample.** Taken literally, a tree in which every detector is red would run the whole
catalogue with an empty set of usable detectors and report every mutant as `survived` — a
catalogue-wide false bypass report caused by a broken environment.

**Adaptation.** Individual red detectors are excluded and named
(`FACTORY_MUTATION_BASELINE_RED=...`), exactly as prescribed, and the run continues. A
baseline in which *every* detector is red keeps the historical refusal
(`FACTORY_MUTATIONS_REFUSED focused baseline is red`). A run with any unusable detector can
never print `FACTORY_MUTATIONS_OK`.

## D4. Sharding implemented, not enabled (P0)

**Prescribed.** "If measured complete work still cannot fit, deterministically shard by sorted
mutant index modulo K across existing CI jobs... start K=4 only after observing that need."

**Status.** The split, the per-shard manifest and the aggregate refusal are implemented and
tested. K remains 1 because the need has not been observed on the hosted runner under the
reworked runner. This is the specification's own condition, not a deviation; it is recorded
here so that nobody reads the presence of the code as a change in how CI runs.

## D5b. Two lease-store defects re-anchored (P1)

**Counterexample.** `lease-acquire-partial-bundle` and `lease-unresolved-resource-reacquired`
anchor into the live-holder and unresolved-operation guards inside `acquire_many`. Splitting
that method so the acquisition can run inside a caller's transaction dedents both guards by
four spaces, and the hosted static rung reported them uninjectable
(`MUTATION_ANCHORS_FAILED defects=959 uninjectable=2`, run 35381678491).

**Adaptation.** Both anchors were re-indented onto the same two guards in `_acquire_locked`.
The property, the id and the `why` are unchanged, and both were kill-checked afterwards in
their own copies against `tests/factory/test_lease_store.py`, each caught by an assertion
failure.

## D5. A started provider call must name an issued grant (P1)

**Prescribed.** `operational_additions.sql` declares
`grant_id TEXT REFERENCES grants(grant_id)` and `CHECK(state!='started' OR grant_id IS NOT NULL)`.

**Observation, not a change.** Those two constraints together mean a call cannot record itself
as started against an identifier no grant ever had. This is stronger than the prose and is
kept: `tests/factory/test_operational_state.py` takes a real grant out of `consume_grant`
rather than inventing one, and pins that an invented grant id raises
`sqlite3.IntegrityError` while leaving the call `reserved`.

## Dependencies that block a live run, not an implementation

These are the package's own named dependencies, recorded where they were hit.

| Dependency | Where | Consequence |
|---|---|---|
| Live host access | An SSH reachability probe to the existing Lightsail Front Door host was refused by this session's permission classifier as a production read. No host command ran. | Per the package: finish source, offline qualification, release bundle and migration rehearsal; installed status `unverified_access`. |
| Hosted mutation observation | The reworked family's required checks are `quick-authority` and `trust-root-authority`; neither runs the mutation rung. The daily main regression does, after merge. | P0's effect on the hosted family is **unobserved** until that run. Stated in the checkpoint rather than assumed. |
