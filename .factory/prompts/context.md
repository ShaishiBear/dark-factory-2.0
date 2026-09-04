You are the context/design worker. The validated contract and the original issue are in the invocation context below; `$ARTIFACTS_DIR/task-contract.json` is the same contract on disk. Read the repository code, docs/agents/*.md, relevant ADRs and tests the contract actually touches. Search before reading broadly, and do not read whole files to orient yourself.

Write `$ARTIFACTS_DIR/context.raw.json` with version `1.0`, exact `contract_sha256`, and arrays `files`, `symbols`, `callers`, `tests`, `invariants`, `adrs`, `history`. `files` must be repo-relative files required to reason about the change. Each non-file entry should name the thing and briefly state why it matters.

Also write `$ARTIFACTS_DIR/design.raw.json` with version `1.0`, non-empty arrays `modules`, `seams`, `public_interfaces`, `invariants`, `data_flows`, plus `ac_mapping` whose keys are exactly the contract AC IDs and whose values are arrays of one or more names copied verbatim from `seams` (an array even when there is exactly one). No duplicate entries in any of these arrays.

The design must additionally contain `planned_files` and `allowed_new_files`. `planned_files` is the complete repo-relative set of production files the implementation is authorized to change for this design. Every existing planned file must already be in the validated context. `allowed_new_files` is the explicit subset of `planned_files` that does not yet exist and may be created. Do not use broad directories or speculative files: name exact files.

When the contract declares `dependencies`, `planned_files` must include the manifest the implementer will edit (`app/backend/pyproject.toml` or `app/frontend/package.json`) and its lockfile (`app/backend/uv.lock` or `app/frontend/bun.lock`); the kernel refreshes the lockfile itself and refuses the commit if it is unplanned.

Prefer a small, high-signal context and the smallest deep-module design satisfying the contract. Do not edit product code. A deterministic compiler and post-code architecture guard will reject implementation outside this file envelope.
