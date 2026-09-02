# Agent domain-document rules

This repository uses Matt Pocock's single-context domain-doc convention without changing the protected `CLAUDE.md`.

- `CONTEXT.md`, when present, is a glossary only: canonical domain terms and what they mean.
- Architecture decisions live under `docs/adr/`.
- Create an ADR only for a decision that is hard to reverse, surprising to a future engineer, and involves a real trade-off.
- Update the glossary when a change establishes or corrects shared vocabulary; do not turn it into a changelog or design document.
- `MISSION.md`, `FACTORY_RULES.md`, and `CLAUDE.md` outrank domain docs.
- Before planning, implementation, or review, read the relevant glossary/ADRs if they exist.
