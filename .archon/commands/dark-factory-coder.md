---
description: Implement one compiled Task Contract after deterministic RED proof. Production only.
---
Read `$ARTIFACTS_DIR/task-contract.json`, `$ARTIFACTS_DIR/context-manifest.json`, `$ARTIFACTS_DIR/red-proof.json`, the issue, and relevant code. Use `implement`, `tdd`, `codebase-design`, and `domain-modeling` as the engineering procedure.

The tests are now frozen evidence. **Do not edit, delete, rename, or weaken any test file.** Make the smallest production change that satisfies the contract and preserves its invariants/out-of-scope boundary. Do not refactor unrelated code.

Run targeted checks as useful, but the deterministic GREEN gate is authoritative. Commit once with Conventional Commits and `Fixes #N`, finish clean, and write `$ARTIFACTS_DIR/implementation.md` with only factual changed paths and deviations from the contract (normally none).
