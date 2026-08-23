---
description: Reduce the completed diff to the simplest implementation that still satisfies the proved contract.
---
Review the merge-base diff after self-fix. The validated task contract and the acceptance tests bound into RED/GREEN are authoritative: simplify the implementation, never the requirement.

Apply this decision order before keeping or adding machinery:

1. If a piece of code is not required by the issue, contract, tests, or an existing repository invariant, delete it.
2. Reuse an existing repository helper, type, module, or established pattern when it already solves the problem cleanly.
3. Prefer the language standard library or a native capability of FastAPI, React, TypeScript, Postgres, the browser, or another platform already in use.
4. Prefer an already-installed dependency over introducing another package.
5. Only then keep the smallest new implementation that makes the proved behavior correct.

Actively look for pass-through wrappers, single-implementation factories/interfaces, speculative configuration and feature flags, duplicated helpers, hand-rolled standard-library/platform behavior, unused extension points, and abstractions whose complexity simply reappears in their callers. Use the deletion test: if removing an abstraction makes its complexity disappear, it was likely unnecessary; if the complexity necessarily resurfaces at callers, the abstraction may be earning its keep.

Do not optimize for line count blindly. Never remove or weaken authentication, authorization, input/trust-boundary validation, data-integrity protections, accessibility, required error handling, observability needed to operate safely, tests, deterministic gates, or behavior required by the contract merely to make the diff smaller. A few explicit lines are preferable to a clever abstraction when they are easier to verify.

If simplification changes production code, run the most targeted relevant checks and commit the result. Never edit the immutable acceptance-test files or redefine RED. If the diff is already the simplest correct implementation, make no cosmetic churn and do not create an empty commit.

This policy is inspired by the reuse-before-invent discipline in Dietrich Gebert's Ponytail project and is adapted here to Dark Factory's contract-first, fail-closed model.
