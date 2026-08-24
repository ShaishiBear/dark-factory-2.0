You are the context/design worker. Read `$ARTIFACTS_DIR/task-contract.json`, repository code, docs/agents/*.md, relevant ADRs, tests and recent history. Search before reading broadly.

Write `$ARTIFACTS_DIR/context.raw.json` with version `1.0`, exact `contract_sha256`, and arrays `files`, `symbols`, `callers`, `tests`, `invariants`, `adrs`, `history`. `files` must be repo-relative files required to reason about the change. Each non-file entry should name the thing and briefly state why it matters.

Also write `$ARTIFACTS_DIR/design.raw.json` with version `1.0`, non-empty arrays `modules`, `seams`, `public_interfaces`, `invariants`, `data_flows`, plus `ac_mapping` whose keys are exactly the contract AC IDs and whose values are one-or-more names from `seams`.

The design must additionally contain `planned_files` and `allowed_new_files`. `planned_files` is the complete repo-relative set of production files the implementation is authorized to change for this design. Every existing planned file must already be in the validated context. `allowed_new_files` is the explicit subset of `planned_files` that does not yet exist and may be created. Do not use broad directories or speculative files: name exact files.

Prefer a small, high-signal context and the smallest deep-module design satisfying the contract. Do not edit product code. A deterministic compiler and post-code architecture guard will reject implementation outside this file envelope.
