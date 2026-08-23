---
description: Compile the issue and engineering investigation into a typed execution contract.
---
Use the preloaded `to-spec`, `domain-modeling`, and `codebase-design` skills. Read the original fetched issue first; it remains the source of truth. Then read `research.md` and `investigation.md`/`plan.md`.

Write only `$ARTIFACTS_DIR/task-contract.raw.json` with version `2.0`, issue `{number,title}`, a concise summary, `behaviors` as `AC-N` objects containing Given/When/Then plus an observable public `seam`, and arrays for `invariants`, `out_of_scope`, `risks`, and `ambiguities`.

Do not invent requirements. If a product decision is genuinely unresolved, put it in `ambiguities`; the deterministic compiler will stop the factory rather than guess. Every requested behavior must map to a testable seam.
