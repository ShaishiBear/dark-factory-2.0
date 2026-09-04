# Method: code review, Standards axis

The Standards axis asks: is this change built the way this repository builds things, and will it be safe and maintainable? It does not re-check the acceptance criteria; that is the Spec axis, judged separately so that a change which passes its tests is still held to the repository's standards, and a beautifully built change is still held to its contract.

Judge against `CLAUDE.md`, the architecture policy and the minimal-complexity method:

- **Conventions.** Placement rules (where routes, SQL, hooks, components and API wrappers live), naming, typing, async discipline, logging instead of print, parameterised SQL, Tailwind-only styling, no inline fetch. A convention broken is a finding even when the code works.
- **Test quality.** Tests that assert implementation details rather than behaviour, tests with no failing case, mocks that mock the thing under test, and fixtures that touch live services are findings. Acceptance tests are frozen and out of scope; judge any additional tests the change added.
- **Error handling and data integrity.** Swallowed exceptions, bare `except`, missing authorisation checks on a new path, unvalidated input reaching SQL or the LLM, and state that can be left half-written are findings; severity is `critical` when a hard invariant (auth, owner-only access, the message cap) is at risk.
- **Architecture seams.** Imports against the layer direction, a new parallel abstraction beside an existing one, growth in a no-growth hotspot, and orchestration pushed onto callers are findings.
- **Unnecessary complexity.** Apply the minimal-complexity ladder: anything that could be deleted, replaced by stdlib or the platform, or reduced to a line without losing an obligation is a finding. So is compression that hurts readability.
- **Hand-rolled duplicates and dependencies.** Reimplementing something the repository or an installed package already provides is a finding. A new package without a contract-level `dependencies` declaration is a finding.
- **Accessibility and security are never "extra".** Missing labels on interactive elements, keyboard traps, and weakened security posture are findings regardless of how small the change is.

Severity: hard-invariant risk is `critical`; convention breaks that will mislead future readers, duplicated machinery and wrong-direction imports are `high`; readability and naming are `medium` or `low`.

Write exactly one JSON object to the artifact path your role prompt names (your final message is not read): `{ "version": "1.0", "axis": "standards", "verdict": "pass|fail", "findings": [...] }`. Each finding has `severity` (`critical|high|medium|low`), `file`, `line` (integer or null) and a concise `description` naming the convention or principle it relates to. Any `critical` or `high` finding means verdict `fail`. Do not include Spec observations here.
