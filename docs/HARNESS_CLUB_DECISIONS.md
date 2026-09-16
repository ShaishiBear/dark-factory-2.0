# Harness Club decisions

Assessed 2026-09-16; implementation follow-through 2026-09-17. The supplied talk
describes persistent execution, durable memory, prompt optimization, subagents,
model routing and self-modifying harnesses. Those are hypotheses to test against
this factory's tasks, not evidence that replacing its runtime will improve it.

## Current harness

Dark Factory has a repo-owned Python control plane, bounded fresh workers,
compiled intent/contracts/design, independent RED acceptance tests, immutable
GREEN replay, separate reviewers and blinded authorities, provenance, protected
holdouts and exact-revision merge checks. Worker tools deliberately exclude a
general shell. The kernel already supplies a narrow static-check hand-back.
Existing role routing, budgets and certified carry address parts of the talk's
ideas without persistent sessions. The inherited DynaChat application is a
current validation workload, not a declaration of the owner's future product.

## Selected implementation and remaining conditions

| Idea | Decision and implementation |
|---|---|
| Durable state outside disposable workers | Reuse capture, retention and authenticated refusal evidence delivered by the other tasks; do not add another archive or causal authority. |
| Observe actual failures before optimizing | PR #220 provides read-only trajectory analysis; PR #223 adds source-bound offline replay with all assigned attempts retained. |
| Execute checks between drafts | HC-01 now provides an isolated function-task lab, same aggregate reservation per arm, real public-check/repair execution, independent final checks and retained evidence. No production policy change follows automatically. |
| Shared budget across retries | Lab reservations are recorded before every call, unreported/failed calls remain spent reservations, and provider retries are disabled. The separate claims task owns canonical authenticated worker spending. |
| Smaller context and reviewed lessons | Measure context and accepted task outcomes before injecting new memory. Preserve supporting/contradicting evidence and expiry. Existing claim evidence must remain distinct from model-written lessons. No lessons go to judges. |
| Prompt optimization / GEPA | Defer search until representative replay cases and untouched confirmation data exist. The experiment runner supplies an execution seam; it does not optimize its own acceptance rules. |
| Adaptive model routing | Existing fixed role routing remains. Compare any proposed escalation under the same aggregate budget after the feedback baseline is measured. |
| Persistent workers / subagents | Defer until measured lost work or latency justifies the coordination and state-contamination risk. Extend bound, inspectable carry before opaque sessions. |
| Self-rewriting harness or judge | Do not grant a worker authority to alter the evaluator that selects its changes. Any proposed harness change uses the maintainer lane and independent checks. |
| Local inference / fine-tuning / minimum-spend grinding | No demonstrated corpus, economics or hardware need. Stop on accepted completion or a real limit; spending a minimum amount is not an objective. |

## What completion means here

The selected no-model infrastructure can be built and verified now. The live
feedback comparison requires an authorized model allowance and working configured
provider route. A small smoke establishes execution feasibility only; it cannot
justify production adoption, cost-saving claims or a learning loop. A confirmation
study also needs representative tasks from the owner's intended project, not just
the six public development functions supplied with HC-01.

For promotion, compare accepted tasks over **all** assigned attempts, correct
refusals separately, total reconciled cost, latency and human intervention. Keep
failed calls and timeouts. Group related cases before splitting; freeze the
candidate, budget and decision rule before untouched confirmation. An inconclusive
result retains the incumbent. Every later production change remains versioned,
reviewable, reversible and subject to the current factory authorities.

The original broad roadmap remains `PROGRAMME.md`. This decision record is a
bounded refinement of it and does not claim the rest of that programme is done.
Run instructions and evidence limitations are in [HARNESS_FEEDBACK.md](HARNESS_FEEDBACK.md),
[HARNESS_REPLAY.md](HARNESS_REPLAY.md) and [FACTORY_RUN_ANALYSIS.md](FACTORY_RUN_ANALYSIS.md).
