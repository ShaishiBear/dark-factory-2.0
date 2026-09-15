# Maintainer delivery recovery

PRs 187 and 186 passed both required authorities but the direct GraphQL auto-merge
mutation refused GitHub's `UNSTABLE` state. Normal exact-head merges succeeded.
The GitHub CLI supports merging immediately eligible states and arming auto-merge
when requirements remain pending. We now use that supported command, retaining
`--match-head-commit`, squash, and every branch protection.

Source: [GitHub CLI merge implementation](https://github.com/cli/cli/blob/trunk/pkg/cmd/pr/merge/merge.go).
This changes the maintainer lane only. Product changes still need full independent
qualification, fresh App effects and post-merge proof.

The workflow previously exported the eligibility result before the stop decision.
It now exports the combined decision and rechecks remote stop state immediately
before requesting the effect. Failed reads refuse. An already armed GitHub
queue is not automatically disarmed by this change; queued-merge cancellation is
still a separate stop-control gap.

Tests execute the actual shell with a fake GitHub boundary: clear, stopped,
unreadable stop, changed head and merge refusal. Causal mutants cover lost head
binding, lost stop wiring, omitted final stop check and an administrator bypass.
No live stop is created as a test while approved product work is pending.
