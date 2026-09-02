---
description: Decide whether an accepted issue is one factory-sized tracer bullet or must be decomposed.
---
Use `to-tickets` and `codebase-design` as the sizing/decomposition lens. Do not mutate GitHub. Read `$ARTIFACTS_DIR/architecture-governor.json` before deciding scope.

Architecture is a veto, not a sizing suggestion. If the compiled governor decision is `prefactor` or `decompose`, return `decompose`; do not return `implement` even when the requested behavior is small. Use the governor's `required_changes` to explain the structural precursor or split. If the governor decision is `proceed`, you may still choose `decompose` for normal factory sizing or tracer-bullet reasons.

A single factory change must be one independently valuable vertical slice and comfortably fit the `FACTORY_RULES.md` 500-changed-line ceiling; use 350 expected changed lines as the planning budget. Prefer a prefactor ticket first when making the change easy is safer than making the hard change directly.

Return only the structured output requested by the workflow: `implement` when architecture permits it and this issue is already one coherent slice; `decompose` when architecture vetoes direct implementation, the issue contains multiple slices, or it is likely to exceed budget. Explain the seam and estimated size.
