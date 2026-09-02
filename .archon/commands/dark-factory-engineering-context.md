---
description: Assemble the smallest evidence-backed code context and design contract for a validated task.
---
Read the validated task contract, repository code, `docs/agents/*.md`, relevant ADRs, tests, and recent history. Use `codebase-design` and `domain-modeling`; search before reading broadly.

Write `$ARTIFACTS_DIR/context.raw.json` with version `1.0`, the exact `contract_sha256`, and arrays: `files` (repo-relative files required to reason about the change), `symbols`, `callers`, `tests`, `invariants`, `adrs`, and `history`. Each non-file entry should name the thing and briefly state why it matters.

Also write `$ARTIFACTS_DIR/design.raw.json` as version `1.0` with non-empty arrays `modules`, `seams`, `public_interfaces`, `invariants`, and `data_flows`, plus `ac_mapping`, an object whose keys are exactly the contract AC IDs and whose values are one-or-more names from `seams`. Describe the design we intend to implement, not speculative future architecture.

Prefer a small, high-signal context and the smallest deep-module design that satisfies the contract. Do not edit product code.
