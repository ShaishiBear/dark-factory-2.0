---
description: Assemble the smallest evidence-backed code context for a validated task contract.
---
Read the validated task contract, repository code, `docs/agents/*.md`, relevant ADRs, tests, and recent history. Use `codebase-design` and `domain-modeling`; search before reading broadly.

Write only `$ARTIFACTS_DIR/context.raw.json` with version `1.0`, the exact `contract_sha256`, and arrays: `files` (repo-relative files required to reason about the change), `symbols`, `callers`, `tests`, `invariants`, `adrs`, and `history`. Each non-file entry should name the thing and briefly state why it matters.

Prefer a small, high-signal context over a repository dump. Do not edit product code.
