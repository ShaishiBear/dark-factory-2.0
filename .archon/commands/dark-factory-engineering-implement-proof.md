---
description: Implement against immutable independently-authored acceptance tests for the complete contract.
---
Read the validated contract, context manifest, compiled design, investigation/plan, and `$ARTIFACTS_DIR/red-proof.json`. Use `implement`, `tdd`, `codebase-design`, and `domain-modeling`.

The v2 RED proof must cover **every contract acceptance criterion**. All acceptance-test files hashed in that proof are immutable: do not edit, delete, rename, regenerate, or weaken them. Modify production code only as needed to make the complete independently-authored acceptance matrix GREEN. You may add temporary diagnostics while debugging, but do not add a substitute acceptance test, manufacture a new acceptance command, or redefine RED/coverage.

Before adding machinery, apply this order: reuse an existing repository helper/type/pattern; then prefer the language standard library or a native capability of the framework/platform already in use; then prefer an already-installed dependency; only then write the minimum new code required by the contract. Do not add speculative abstractions, factories/interfaces with only one real implementation, configuration or feature flags for hypothetical future needs, or a new dependency when an existing capability solves the proved requirement cleanly. Necessary complexity is allowed when the contract, a repository invariant, security/data-integrity boundary, accessibility requirement, or operational correctness requires it.

Commit the production change with Conventional Commits and `Fixes #N`. Leave the worktree clean. The following deterministic GREEN node will re-hash the full acceptance suite and replay every per-AC command captured by RED.
