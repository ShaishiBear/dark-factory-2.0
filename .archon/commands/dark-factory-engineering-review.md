---
description: Review the completed PR with Matt Pocock's independent Spec and Standards axes.
---
Use the preloaded `code-review`, `domain-modeling`, and `codebase-design` skills. Review from the merge-base diff, not the implementer's rationale.

Run the skill's Spec and Standards axes independently: Spec checks missing/wrong behaviour and scope creep against the issue/spec; Standards checks repository conventions, test quality, deep-module/interface design, error handling, code smells, and unnecessary complexity. Do not collapse the axes into one intuition.

Within Standards, apply reuse-before-invent pressure. Flag meaningful cases where the diff hand-rolls functionality already present in the repository, standard library, framework/platform, or an installed dependency; adds a dependency without a demonstrated need; introduces a pass-through wrapper or single-implementation factory/interface; adds speculative flags/configuration/extension points; or creates an abstraction that fails the deletion test. Prefer the simplest implementation that preserves the contract. Do not recommend deleting security/auth checks, trust-boundary validation, data-integrity protections, accessibility, required error handling, observability, tests, or deterministic gates merely to reduce line count.

Return prioritized, actionable findings with file/line evidence. Mark blockers clearly. Do not edit code in this node; Cole's synthesis/self-fix stages own remediation.
