# Domain Context

This file is a glossary, not a specification or implementation guide.

- **Task Contract** — machine-readable, ambiguity-free statement of one factory-sized behavior change.
- **Acceptance Criterion** — observable behavior that must be true for the task to be complete.
- **Test Seam** — the single public behavioral boundary used to prove RED then GREEN for a tracer-bullet task.
- **Ready Frontier** — issues whose declared blockers are all closed and may enter normal factory triage.
- **Context Manifest** — deterministic inventory of code, symbols, imports, docs, history, and hashes relevant to a Task Contract.
- **Builder Evidence** — machine data emitted by the builder; never provided to holdout reviewers.
- **Holdout Reviewer** — fresh-context evaluator that sees contract/outcome, never coder rationale.
- **Evidence Bundle** — exact-commit proof package consumed by the merge gate.
