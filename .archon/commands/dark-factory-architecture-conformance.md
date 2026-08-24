---
description: Independently judge the finished implementation against the governed architecture and compiled design.
---
This is a fresh post-code architecture authority. Read only `.factory/architecture.json`, `$ARTIFACTS_DIR/task-contract.json`, `$ARTIFACTS_DIR/context.json`, `$ARTIFACTS_DIR/design.json`, `$ARTIFACTS_DIR/architecture-governor.json`, the merge-base diff, and source/tests needed to understand that diff.

Do **not** read `plan.md`, `investigation.md`, `implementation.md`, prior review/synthesis outputs, PR comments/reviews, or commit messages. Judge the resulting code, not the coder's explanation.

Use `codebase-design`, `improve-codebase-architecture`, and `code-review` as the lens. Decide whether the finished diff still implements the compiled design and moves every touched active migration/debt area in the permitted direction. A locally correct implementation that crosses the wrong seam, grows a declared hotspot, duplicates responsibility, or undoes an active migration is an architectural deviation.

Write `$ARTIFACTS_DIR/architecture-conformance.raw.json` with version `1.0` and exactly these semantic fields:
- `verdict`: `conform` or `deviates`
- `convergence`: `improves`, `neutral`, or `regresses`
- `principles`: IDs of every architecture principle applicable to the actual changed files
- `migrations`: IDs of every active migration applicable to the actual changed files
- `debts`: IDs of every debt entry applicable to the actual changed files
- `rationale`: one-or-more concise evidence statements grounded in the diff/code
- `findings`: concrete architectural deviations; use `[]` only when there are none

Do not edit code. The deterministic gate recomputes policy applicability, HEAD, and diff hashes and will reject omitted/invented policy IDs or a regressing implementation presented as conforming.
