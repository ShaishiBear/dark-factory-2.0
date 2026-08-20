---
description: Publish oversized work as tracer-bullet child issues, then release the parent from execution.
---
Use the preloaded `to-tickets` skill and `docs/agents/issue-tracker.md`.

Turn the parent spec/investigation into the smallest coherent set of vertical GitHub child issues. Each child must state `Part of #<parent>`, observable acceptance criteria, test seam, and explicit blockers. Use native sub-issues/dependencies where available. Do not label children `factory:accepted`; normal triage must admit them.

Comment on the parent with the child list and dependency order. Remove `factory:accepted` and `factory:in-progress` from the parent so it cannot be implemented as one oversized PR. Do not change product code.
