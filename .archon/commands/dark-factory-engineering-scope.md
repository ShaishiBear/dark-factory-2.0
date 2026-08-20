---
description: Decide whether an accepted issue is one factory-sized tracer bullet or must be decomposed.
---
Use `to-tickets` and `codebase-design` as the sizing/decomposition lens. Do not mutate GitHub.

A single factory change must be one independently valuable vertical slice and comfortably fit the `FACTORY_RULES.md` 500-changed-line ceiling; use 350 expected changed lines as the planning budget. Prefer a prefactor ticket first when making the change easy is safer than making the hard change directly.

Return only the structured output requested by the workflow: `implement` when this issue is already one coherent slice; `decompose` when it contains multiple slices or is likely to exceed budget. Explain the seam and estimated size.
