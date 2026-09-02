---
description: Author the complete acceptance-test checkpoint before production implementation.
---
Read only the validated task contract, validated context manifest, compiled `design.json`, relevant code/tests, and Matt's `tdd` skill. Do **not** change production code.

Author the minimum behavioural acceptance checkpoint for **every** contract criterion before implementation begins. Each checkpoint must exercise the criterion at one of the seams declared for that AC in `design.json`, and each must be independently runnable so the deterministic gate can prove RED for the right behavioural reason. Reuse a test file across criteria when appropriate; do not create redundant tests merely to satisfy the schema.

Write `$ARTIFACTS_DIR/test-spec.json` as version `2.0` with `checkpoints`, one per contract AC exactly once. Each checkpoint is `{ "acceptance_id": "AC-N", "cwd": "repo/relative/cwd", "argv": ["executable", "arg"], "files": ["repo/relative/test-file"], "expected_failure": "stable output fragment" }`.

Run each checkpoint only as needed to identify a stable expected behavioral failure. Commit the union of acceptance-test files in a single test-author commit such as `test(factory): prove acceptance contract red`. Do not change production code. Leave the worktree clean. The deterministic RED gate, not you, decides whether every criterion is validly RED.
