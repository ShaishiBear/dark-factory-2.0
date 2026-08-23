---
description: Author the single behavioral proof test for a compiled Task Contract. Tests only.
---
Read `$ARTIFACTS_DIR/task-contract.json` and `$ARTIFACTS_DIR/context-manifest.json`, then inspect only the code needed to understand the declared public seam. Use the preloaded `tdd` skill.

Add the smallest test that proves the Task Contract's declared seam and acceptance criteria. **Do not edit production code, configuration, docs, or factory infrastructure.** Do not weaken or rewrite existing tests. The new test must fail for the contract's `expected_red` reason before implementation.

Commit the test-only change with `test(factory): prove red for #N`. Finish with a clean worktree. Do not implement the fix; the deterministic RED gate runs after this node.
