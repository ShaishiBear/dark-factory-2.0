# Agent domain-document rules

This repository adopts a single-context domain-doc convention without changing the protected `CLAUDE.md`. **Today the convention is dormant:** there is no root `CONTEXT.md` and no `docs/adr/` directory. Nothing in the factory creates or maintains them; an issue that establishes shared vocabulary or records a hard-to-reverse decision may create them, and from then on the rules below apply.

- `CONTEXT.md`, when present, is a glossary only: canonical domain terms and what they mean.
- Architecture decisions live under `docs/adr/` when present.
- Create an ADR only for a decision that is hard to reverse, surprising to a future engineer, and involves a real trade-off.
- Update the glossary when a change establishes or corrects shared vocabulary; do not turn it into a changelog or design document.
- `MISSION.md`, `FACTORY_RULES.md`, and `CLAUDE.md` outrank domain docs.
- Before planning, implementation, or review, read the relevant glossary/ADRs if they exist. The context worker is told to read them; it is not authorised to write them.
