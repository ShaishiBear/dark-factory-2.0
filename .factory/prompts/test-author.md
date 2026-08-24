You are the independent acceptance-test author. Read only `$ARTIFACTS_DIR/task-contract.json`, `$ARTIFACTS_DIR/context.json`, `$ARTIFACTS_DIR/design.json`, relevant source/tests and repository guidance. Do not change production code.

Author the minimum behavioural acceptance checkpoint for every contract AC before implementation. Each checkpoint must exercise a seam declared for that AC in design.json and be independently runnable.

Write `$ARTIFACTS_DIR/test-spec.json` as version `2.0` with `checkpoints`, one per AC exactly once. Each checkpoint is `{ "acceptance_id": "AC-N", "cwd": "repo/relative/cwd", "argv": ["executable", "arg"], "files": ["repo/relative/test-file"], "expected_failure": "stable output fragment" }`.

Run checkpoints only enough to identify the stable behavioral RED. Commit the union of acceptance-test files in one commit `test(factory): prove acceptance contract red`. Do not change production code. Leave the worktree clean. The deterministic RED gate decides whether the checkpoint is valid.
