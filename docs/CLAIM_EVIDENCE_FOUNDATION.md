# Claim evidence and decision history: observation foundation

This delivery adds rebuildable, read-only explanations around the existing factory. It does
not add an attestation issuer, proof store, qualification state machine or merge authority.

## Existing sources and consumers

| Canonical record | Existing writer/authority | Existing consumer | New projection |
| --- | --- | --- | --- |
| `.factory/evidence-spine.json` | protected policy | spine assessment, evidence closure, merge verifier | proof obligation and required predecessors |
| `spine/run-manifest.json` | `evidence_closure.compile_full_spine` | spine closure | subject, producer, certification references and bindings |
| `evidence-bundle.json` with embedded spine | protected evidence wrapper | merge verification | recorded index, base/head, policy and manifest identity |
| independent certificates | registered blinded authorities, checked by `independence.py` | evidence closure | expected authority and retained envelope checks |
| IntentStore project events | authenticated versioned commands | intake snapshots and scope review | all historical proposals, owner approvals and their basis |
| programme completion receipt | existing post-merge kernel | `ProgrammeQueue.completed` | unchanged; not inferred from history |
| trajectory archive | completed-run observer/App archive | operational history | source for locating retained evidence, never proof authority |

The programme compiler conserves approved acceptance criteria and produces a DAG. Its runtime
separately verifies admission and completion. Neither a proposed programme, an approved spec,
a closed issue, a successful workflow nor a favourable model response substitutes for that path.

## Read-only integration contract

```python
from factory_kernel.claim_explanation import explain_run
from factory_kernel.decision_history import explain_history

claims = explain_run(
    artifact_root=retained_artifact_directory,
    policy_path=protected_policy_path,
    expected_head_sha=observed_candidate_head,
    expected_base_sha=observed_base,
    current_claim_hashes=None,  # optional freshly observed claim_id -> SHA256
)
history = explain_history(intent_store, project_id, principal=authenticated_owner)
```

Both return JSON-ready values. The host owns path selection and authentication: HTTP requests
must not supply arbitrary filesystem paths or construct a `Principal`. History uses the existing
store lock and chain validator and allows only the configured owner. It includes private wording;
do not publish it to the trajectory archive, another principal, model workers or blinded judges.
Render all strings as text, never HTML. History writes no events; the store may create its normal
lock file. The narrow adapter uses IntentStore's existing internal read/lock methods; changes to
those methods require updating this adapter together with its persistence tests.

Standalone diagnostic command (JSON on stdout, no writes or authority execution):

```text
python -m factory_kernel.claim_explanation --artifacts <directory> --policy .factory/evidence-spine.json --head <full-sha> --base <full-sha>
```

The Front Door task owns mounting this interface into its authenticated snapshot/API and UI.
This PR does not add a route or deploy the service. The agreed seam is suitable for that work.

## Status semantics

| Field | Meaning |
| --- | --- |
| `evidence_status=absent` | neither a usable manifest claim nor a retained index claim exists |
| `evidence_status=stale` | a recorded identity differs from the requested subject, policy or observed dependency, including transitive predecessor changes |
| `evidence_status=insufficient` | a claim is recorded, but evidence or provenance is incomplete |
| `recorded_dependency_status=current` | supplied identities match the retained records and the checked envelope/artifact chain is intact |
| `recorded_dependency_status=unknown` | an identity, artifact or binding needed for this comparison is missing or inconsistent |
| `proof_status=not-established` | this observer has not authenticated and established currently valid proof |

`current` in the comparison field **never means CURRENT trusted proof**. Omitting
`current_claim_hashes` leaves currency unknown. Even an intact synthetic/retained closure with
all supplied hashes has insufficient evidence for proof reuse: legacy records do not capture
complete issuer provenance, authority-program and toolchain identity or live-world replay state.
`proof_reuse_allowed` is always false. This conservative limit is intentional, not a missing
fallback to workflow success. No new CURRENT attestation can be emitted by this module.

Wrong subject or policy conservatively affects the whole run. An explicitly changed claim hash
propagates through the existing spine's `requires` edges only. This is an explanation of recorded
dependencies, not validated selective proof reuse or an instruction to skip unaffected checks.

The output includes source file hashes, canonical policy identity, claim record IDs, expected
authority identities, exact subject, per-artifact checks, predecessor comparisons and explicit
gap/change codes. Missing manifests are never reconstructed from a successful bundle. Corrupt,
duplicate, oversized, escaping and symlinked artifact inputs cannot become intact evidence.

History orders by project version, not timestamps. Event IDs derive from canonical event hashes.
It retains old proposals, assumptions and questions, links approvals to exact drafts and prior
approvals, and rejects replayed events. The latest recorded approval is historical information;
it does not imply that a newer draft is approved, or that any programme is active or qualified.
File hashes detect drift within the trusted service store; they cannot authenticate a malicious
writer who controls that directory. Imported JSON is not an authenticated decision history.

## Acceptance and governance

- Reuse the current spine/manifest/intent records; no parallel proof or event lifecycle.
- Expose obligations, authorities, subject hashes, dependencies, source identity and gaps.
- Wrong revision, changed dependency, absent evidence, stale policy, forged authority,
  self-certification and duplicate/replayed events are exercised by deterministic tests.
- Verify causal detection with a green copied baseline before injected mutations.
- Preserve current qualification, blinded authorities, exact-head merge, budgets and stop rules.
- Delivery uses the existing maintainer PR lane and trusted-base review, without bypass.

This is a Tier 1 observation addition, not a trust-boundary cutover: no privileges, credentials,
allowed actions, authority replacements or protected-policy weakening are introduced. Source is
protected because it lives beside the current factory, but no merge decision consumes it.
Containment is simply not mounting the optional view. TCB authorization delta is zero.

## Subsequent bounded deliveries

1. Front Door integration after review of this contract and retained-run receipt.
2. Retain missing claim artifacts/provenance and measure dependency identity coverage; do not
   backfill unknown identities as facts. Any authority migration first runs in shadow.
3. Minimum Preflight: direct/light modes, fixed constraints and selection criteria, alternatives,
   assumptions, predictions and rationale retained separately from proof; UNPROVEN factory handoff.
   No probes until isolation, budgets and execution boundaries exist. No predictor prerequisite.
4. Bounded reconsideration: distinguish implementation failure from strategy rejection, preserve
   scope/completed work/cumulative spend and resubmit through the same programme admission boundary.

The later A rejected -> explain -> reconsider B -> fresh qualification scenario is not claimed by
this delivery. Preflight, reconsideration, selective reuse and general decision graphs remain future work.

## Architecture references

Read at `origin/architecture/dark-factory-2-target`, commit
`c1f7ef0187870d82d69b1b3fa7941ee266fa133b`: document index; target architecture; claim-centric V3;
architecture governance §§13,15–16; kernel/TCB migration §§1,5–9; attestation dependency model and
claim-model addendum; project decision graph semantics; programme/Preflight; canonical contracts;
learning/experience. Those are architectural direction. Current runtime artifacts and protected
authorities remain authoritative during this incremental migration.
