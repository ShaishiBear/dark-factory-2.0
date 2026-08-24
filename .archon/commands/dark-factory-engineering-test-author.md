---
description: Author the smallest acceptance test checkpoint before production implementation.
---
Read only the validated task contract, validated context manifest, compiled `design.json`, relevant code/tests, and Matt's `tdd` skill. Do **not** change production code.

For the next smallest contract behaviour, add the minimum behavioural test at the declared public seam. Run it once to learn a stable fragment that uniquely describes the expected behavioral failure (not a syntax/import/setup failure).

Write `$ARTIFACTS_DIR/test-spec.json` as `{ "acceptance_id": "AC-N", "cwd": "repo/relative/cwd", "argv": ["executable", "arg"], "files": ["repo/relative/test-file"], "expected_failure": "stable output fragment" }`. `acceptance_id` must be the exact contract criterion this checkpoint proves and must be mapped to the tested seam by `design.json`.

Commit only those test files with `test(factory): prove AC-N red`. Leave the worktree clean. The next deterministic node, not you, decides whether RED is valid.
