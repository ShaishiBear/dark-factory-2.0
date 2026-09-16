# Authenticated factory feedback

Step 2 imports a reported validation refusal for a current strategy programme into the
existing owner project log. Authentication establishes who reported what, about which
candidate. It does not establish why the candidate failed, invalidate a strategy or qualify
a replacement. This is the refusal path needed before evidence-based reconsideration;
successful completion keeps its existing post-merge authority and receipt protocol.

## Receipt and import boundary

The protected validator's failure handler writes `factory-feedback.json` only after current
programme admission and an exact canonical workflow checkout are established. It binds the
programme hash, item, issue, PR, head, base, workflow run and attempt to the bytes of the
scrubbed `validation-refusal.json`. Its digest is also posted in the existing Actions-authored
refusal comment. Failure to mint a receipt preserves the ordinary refusal and repair route;
it cannot manufacture authenticated feedback. Existing historical files are not upgraded.

The existing bounded retention collector includes this receipt. The importer independently
reads GitHub, verifies the latest completed canonical worker attempt, same-repository PR
and App-authored programme issue, successful retention steps, complete bounded artifact
inventory, artifact lifetimes and platform SHA-256 digests of the downloaded ZIP bytes.
It reads bounded regular members in memory, never extracts an archive, and checks every
retained file against the exact index. The receipt must also have one unedited Actions bot
comment during that run. Programme compilation must match both protected main and the
project's historical exploration handoff. Mutable platform facts are reread after download.

The receipt is a kernel report, not an independent certifier attestation. Authentication
depends on the repository's protected workflow/code and GitHub platform identity. It does
not defend against compromise of GitHub, the trusted kernel or the owner state directory.
Neither an artifact's self-declared authority nor a copied comment alone is sufficient.

## Owner controls

When hosted exploration is enabled, each session with a handoff offers **Import a factory
outcome**. Enter run ID, attempt, PR and programme item. The existing bearer authentication,
same-origin controls and server-owned principal apply. The API is
`POST /api/exploration/import-feedback`, with the usual `idempotency_key`,
`expected_project_version`, `session_id` and a `request` containing only
`run_id`, `attempt`, `pr`, `item_id`. No request may supply evidence paths or a causal verdict.

The stop observer is checked before and after observation. Import uses the existing atomic
project writer, CAS and exact replay semantics; another key cannot duplicate a run/PR
attempt. Imported records appear in exploration and decision history with evidence hashes,
`cause: unresolved`, `independent_strategy_rejection: not-established`, `UNPROVEN` and
`proof_reuse_allowed: false`. Claims, recommendations, execution ownership and cumulative
budgets are unchanged. The earlier explicit owner assessment lane remains separately
identified as owner judgment; it gains no authentication from these records.

## Deliberate bounds

This first importer handles dispatch-phase validation refusals for an open PR at the
**current protected main revision**, the current approved scope and active programme.
A moved main/head, rerun, replaced programme, expired/missing archive, incomplete inventory
or absent receipt refuses import. Older observations may still be inspected through the
existing historical evidence interface, without being upgraded to current authority.
Selective historical reuse requires the later dependency/currency work.

Only the refusal is established. A failed certifier, malformed output, infrastructure outage
or unknown reason is still not a contradicted strategy assumption. Step 3 must supply explicit
rules over independently established findings and preregistered assumptions; Step 4 must
govern replacement and fresh qualification. Neither is implemented by this import command.

No paid exploration, factory dispatch, repository setting change or service deployment is
necessary to validate this slice. Tests cross the actual receipt writer, retention collector,
ZIP reader, platform observation adapter, project writer and HTTP route using synthetic
platform responses. The complete hosted A-to-B cycle remains unobserved.
