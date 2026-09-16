# Worker failure activity

Run 35039314657 timed out while authoring tests for issue #189: 13 turns and 34,850
observed thinking tokens within the existing 2,025-second wall. Its last five events
contained only thinking counters. The 24-turn draft-deadline observation had not fired,
so the retained failure record could not distinguish reading from an attempted draft.

Provider timeout, hang and terminal-exit diagnostics now include `draft_activity`:
Read-call count, at most the existing `FILES_READ_CAP` paths, the first observed Write/Edit
turn (or null), and the configured draft deadline. These are observations from the final
process attempt, not proof that a file was successfully written or that tests passed.
Existing secret scrubbing still applies before upload.

The timeout remains a refusal. No budget, model route, turn cap, retry rule, acceptance
test or proof gate changes. This cannot reconstruct the missing history of run35039314657;
issue #189 remains under its existing human hold. Recovery needs a concrete bounded repair
and the separate human release required by FACTORY_RULES section7.
