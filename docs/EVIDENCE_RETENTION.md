# Evidence retention and provenance observations

The worker's seven-day diagnostic upload collected top-level JSON but omitted the nested
`spine/` and `independent/` records. A retained success could therefore contain an evidence
bundle without its manifest or supporting claim files. This slice preserves existing bytes;
it never reconstructs, recertifies or reuses proof.

## Retained artifacts

Both dispatch and merge jobs stage a separate, fixed evidence allowlist using
`python -m factory_kernel.evidence_retention`. Names come from the existing 21-claim spine:
the bundle, manifest, index, builder provenance, builder/validator claim subjects,
certifications and independent wrappers, plus the existing scrubbed `validation-refusal.json`.
That refusal records the validator's stage/reason and exact PR/base/head; it is not automatic
evidence that the underlying strategy is wrong. Logs, arbitrary nested files and model transcripts
are excluded. Each job requests 90-day retention for two Actions artifacts:

- `dark-factory-evidence-<phase>-<run>-<attempt>`: the original JSON bytes under the original
  `<kernel-run>/artifacts/` paths, usable directly by `claim_explanation.explain_run`.
- `dark-factory-evidence-index-<phase>-<run>-<attempt>`: a metadata-only `retention-index.json`.

Limits per job: 20 kernel attempts, 256 copied files, 250,000 bytes per JSON object and
8,000,000 total copied bytes. Duplicate JSON keys, non-object JSON, unsafe paths, symlinked
ancestors and oversized files are refused. Missing, invalid and over-bound file counts are
recorded per attempt. Optional certificates that never existed also count as absent; these
counts are observations, not failed obligations. Existing output directories are refused.
The raw upload is permitted only after successful index validation. Staging and upload are
observation-only, run on failure as well as success, and cannot change a completed proof or
merge into a failed job. They receive no step-scoped provider or App credentials.

The index records repository, workflow run, run attempt, phase and workflow source revision.
It separately records the checkout revision observed at packaging time, a digest of that
commit's conservative `CARRY_POLICY_PATHS` Git tree, and the packaging interpreter version.
The worker checks out current main, so its observed checkout may differ from the workflow
event's revision. Neither revision is substituted for the candidate head/base in proof files.
The tree snapshot does not establish the bytes of every executed program, installed toolchain,
issuer authenticity or live-world dependencies. It is not an execution attestation.

## Durable archive

The existing trajectory collector records GitHub artifact IDs, ZIP digests, sizes, creation
and expiry timestamps after matching the canonical repository/run/source revision and exact
attempt-specific names. It downloads only the small metadata index, checks that its platform
identity remained unchanged, then validates its source binding and projects fixed paths,
hashes, bounded counts and typed observations into the existing trajectory record.

Raw proof contents do not enter the public `factory/trajectories` branch. File identities in
the index are explicitly `recorded-index`; the collector does not authenticate the issuer or
independently rehash the raw evidence artifact. Artifact expiry/deletion, a missing companion,
duplicates, malformed metadata and unavailable downloads remain gaps. An expiry timestamp
is availability metadata, not a semantic proof dependency or a promise of current availability.

Old runs without these artifacts retain their original record shape, including their gaps.
Artifacts from a later rerun do not modify the old attempt's shape. Publication remains
append-only: recollection never overwrites an archived attempt, including after expiry changes
what can be observed. Oversized combined metadata falls back to an explicit bounded gap.

## Qualification boundary and next step

`proof_reuse_allowed` remains false. Claim explanation still compares retained hashes and
dependencies, with issuer, complete authority/toolchain and live-world coverage unavailable.
All existing qualification, independent judgments, budgets, stop and merge checks remain
the execution authority. Retention changes no approval or programme state.

Next: observe an ordinary worker run after delivery to confirm both packages and archived
metadata appear without commissioning a paid canary. Then implement minimum Preflight with
fixed alternatives and criteria, deterministic/direct reasoning first, and `UNPROVEN` as a
normal handoff. Executable probes need their isolation and cumulative-budget boundary before
activation. Selective reuse and affected-only reconsideration remain later validated layers.
