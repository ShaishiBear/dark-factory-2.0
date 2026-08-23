---
description: Implement against immutable independently-authored acceptance tests.
---
Read the validated contract, context manifest, investigation/plan, and `$ARTIFACTS_DIR/red-proof.json`. Use `implement`, `tdd`, `codebase-design`, and `domain-modeling`.

The acceptance-test files hashed in the red proof are immutable: do not edit, delete, rename, regenerate, or weaken them. Modify production code only as needed to satisfy the proved behavior. Use targeted checks while working, but do not manufacture a new test command or redefine RED.

Commit the production change with Conventional Commits and `Fixes #N`. Leave the worktree clean. The following deterministic GREEN node will re-hash the acceptance tests and rerun the exact command captured by RED.
