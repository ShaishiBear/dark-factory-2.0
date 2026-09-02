---
description: Implement one factory-sized issue through Matt Pocock's iterative TDD loop.
---
Read the issue, `research.md`, investigation/plan, domain docs, `CLAUDE.md`, and `FACTORY_RULES.md`. Use the preloaded `implement`, `tdd`, `codebase-design`, and `domain-modeling` skills as the operating procedure, not optional advice.

Work vertically. At each agreed behavioural seam: add exactly one behaviour test, run it and observe the expected RED, make the smallest production change that can satisfy it, run GREEN, then repeat. A syntax/setup/unrelated failure is not valid red. For bugs, the regression seam must reproduce the investigated failure before the fix. Do not refactor during red/green; review owns refactoring.

Use the existing Python/uv and Bun tooling from `CLAUDE.md`. Run targeted type/tests during the loop, then the relevant full local suite once. Before commit, compute changed additions+deletions against `$BASE_BRANCH`; stop above 450 to leave validator/review headroom under the 500-line rule. Commit once with Conventional Commits and `Fixes #N`. Write `$ARTIFACTS_DIR/implementation.md` with every red→green seam, commands/results, deviations, and final diff size.
