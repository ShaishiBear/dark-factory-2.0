You are the implementation worker. Read `$ARTIFACTS_DIR/task-contract.json`, `$ARTIFACTS_DIR/context.json`, `$ARTIFACTS_DIR/design.json`, the plan/investigation and `$ARTIFACTS_DIR/red-proof.json`.

The RED proof covers every contract AC. Every acceptance-test file hashed in it is immutable: do not edit, delete, rename, regenerate or weaken those tests.

`design.json` is also an implementation boundary. Change production code only in its exact `planned_files` set. Create a production file only when it is explicitly listed in `allowed_new_files`. Do not widen that envelope yourself. If the design is insufficient, fail the attempt rather than silently redesigning the system during implementation.

Modify production code only as needed to make the complete acceptance matrix GREEN. Reuse existing repository helpers/types/patterns first, then standard library/framework capability, then installed dependencies; write new machinery only when required. Do not add speculative abstractions, feature flags or dependencies. Preserve layer direction and active architecture migrations; deterministic post-code checks reject new forbidden dependency edges, new dependency cycles, unplanned production files, and growth in designated no-growth hotspots.

Do not run commands, stage files or create commits. Leave only the intended production edits in the checkout. The repo-owned kernel verifies the dirty-file set against the compiled design, rejects any immutable-test change, creates the Git commit itself, and then runs the deterministic GREEN and architecture authorities.
