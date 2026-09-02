---
description: Diagnose a bug before implementation using Matt Pocock's debugging discipline.
---
Use the preloaded `diagnosing-bugs`, `domain-modeling`, and `codebase-design` skills as authoritative procedure.

For the accepted bug: establish a feedback loop first; reproduce and minimise it; identify the public behavioural seam; form falsifiable hypotheses and probe them; state the root cause only when evidence supports it. If the environment cannot reproduce the bug, stop rather than guess.

Read `research.md`, relevant glossary/ADRs, `CLAUDE.md`, and `FACTORY_RULES.md`. Do not implement the fix. Write `$ARTIFACTS_DIR/investigation.md` with repro command, observed failure, root-cause evidence, regression-test seam, files likely involved, and constraints. Preserve diagnostic evidence so the implementation node can prove red before green.
