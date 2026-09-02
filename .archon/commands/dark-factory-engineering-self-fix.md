---
description: Repair synthesized review findings through diagnosis and TDD, without weakening evidence.
---
Read the synthesized findings and current diff. Use `diagnosing-bugs`, `tdd`, and `implement` as the repair procedure.

For each real finding, reproduce or demonstrate it first, add/adjust a behaviour test at the correct public seam when behaviour is involved, observe red for the expected reason, make the smallest fix, and prove green. Do not delete/weaken tests, bypass validators, expand scope, or modify protected factory files.

Re-run the relevant targeted checks, commit the repair, and summarize fixed versus rejected findings with evidence.
