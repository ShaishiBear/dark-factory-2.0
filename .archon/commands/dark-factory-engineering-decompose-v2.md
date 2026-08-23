---
description: Decompose an oversized accepted issue into ready-frontier tracer bullets.
---
Use `to-tickets` and `codebase-design`. Create the smallest vertical child issues. Every child body must include `Part of #<parent>`, observable acceptance criteria, one proposed public test seam, and explicit dependency lines exactly as `Blocked by: #N` when needed.

Do not label children accepted. Comment the child/dependency graph on the parent, remove `factory:accepted` and `factory:in-progress` from the parent, and do not change product code. The deterministic frontier reconciler runs after this node.
