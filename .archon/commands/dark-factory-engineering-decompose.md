---
description: Publish oversized or architecture-blocked work as tracer-bullet child issues, then release the parent from execution.
---
Use the preloaded `to-tickets` skill, `docs/agents/issue-tracker.md`, and `$ARTIFACTS_DIR/architecture-governor.json`.

Turn the parent spec/investigation into the smallest coherent set of vertical GitHub child issues. Each child must state `Part of #<parent>`, observable acceptance criteria, test seam, and explicit blockers. Use native sub-issues/dependencies where available. Do not label children `factory:accepted`; normal triage must admit them.

When the architecture governor decision is `prefactor` or `decompose`, treat every `required_changes` entry as a mandatory structural outcome. Create the smallest prefactor/migration child tickets needed to satisfy those outcomes and make dependent product tickets explicitly `Blocked by: #<prefactor>` where appropriate. Preserve the governor's migration direction instead of re-planning around the veto.

Comment on the parent with the child list and dependency order. Remove `factory:accepted` and `factory:in-progress` from the parent so it cannot be implemented as one oversized PR. Do not change product code.
