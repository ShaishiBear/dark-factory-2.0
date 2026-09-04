# Agent issue tracker

GitHub Issues is the factory's work/state surface. Use `gh`; infer the repository from the current checkout.

- The kernel's label vocabulary is authoritative: the eight `factory:*` control labels configured in `.factory/kernel.json` plus the `priority:*` and `type:*` labels returned by `label_vocabulary()` in `factory_kernel/triage.py`. Do not invent a second triage vocabulary.
- An accepted issue is an execution request. A child created by decomposition starts unaccepted and must pass the normal factory triage before execution.
- Preserve parentage with GitHub sub-issues when available; otherwise put `Part of #<parent>` at the top of the child body.
- Preserve blocking edges with GitHub issue dependencies when available; otherwise put `Blocked by: #...` at the top of the child body.
- Child tickets must be vertical tracer bullets with their own observable acceptance criteria and testing seam.
- Never dispatch a blocked child merely because it exists.
- Do not use PRs as a request surface.

When publishing tickets, publish GitHub issues following these conventions. The factory remains responsible for deciding which issue enters `factory:accepted`.
