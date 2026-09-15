# Citation checkpoint diagnosis

## Observed failure

Approved programme `d809b087a48a5d3a634286dc5f303d90ce119139a799ace0a122a7e244766b00`
created and triaged [issue 181](https://github.com/ShaishiBear/dark-factory-2.0/issues/181).
[Build 34998691864](https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/34998691864)
ran on `4b6c1e72f20147a2ee18e51dfc2e094d8836d2f0`. RED passed; GREEN accepted AC-1
but rejected AC-2. The kernel stopped without publishing a product PR and escalated the issue.

The frozen test used `screen.queryByText(fullText, { exact: true })` where `fullText`
contained literal newlines. Testing Library's default normalization collapses the actual
text's whitespace, so the query cannot match the raw multiline expected string. The saved
implementation rendered `citation.snippet` verbatim with inline `whiteSpace: 'pre-wrap'`.

## Causal reproduction

The recorded test-author Write was reconstructed and formatted with the same pinned Biome.
Its SHA-256 matched RED's file receipt exactly:
`dde12cf67c38c53908c6302df8c9a3218e907fcef72dbd5c5f24bc2dd5cd4f8b`.
The test-author commit recorded in RED was `b364fc81a7338c0235d37f21368e035612f31665`.

In a separate disposable checkout with the recorded implementation and pinned dependencies:

| Probe | Result |
| --- | --- |
| Exact frozen test | Same AC-2 assertion fails |
| Only add `normalizer: (text) => text` to AC-2 query | All 19 component tests pass |
| Corrected query; implementation omits snippet | Fails snippet assertion |
| Corrected query; implementation flattens newlines | Fails multiline assertion |
| Corrected query; implementation omits pre-wrap | Fails display assertion |

Raw evidence is retained locally under the implementation worktree's ignored
`.validation/citation-repro/`: `original-checkpoint.log`, `correct-normalizer.log`,
`negative-controls.json`, and the three named negative-control logs. The downloaded
run artifact retains original transcripts, RED and GREEN failure proofs, contracts and design.
These local probes diagnose the test defect; they do not qualify product code for release.

## Bounded repair and recovery

Test-author guidance now explains exact whitespace matching, preserving the AC's text and
checking display independently through the declared design seam. No product file, frozen
acceptance test, proof gate, budget, scope, model setting or ratchet floor is changed.
No positive-witness framework or automatic rewrite of acceptance evidence is introduced.

Deliver this prompt through the existing maintainer lane and required checks. Recovery of
issue 181 requires the recorded human escalation boundary: removal of `factory:needs-human`
and restoration of `factory:accepted`. A fresh ordinary build must author new checkpoints,
pass RED and GREEN, then complete the unchanged independent qualification, App merge and
post-merge receipt. Existing issue body and attempt markers remain untouched. The failed
build's frozen evidence stays immutable. Do not call the factory operational before that
complete real cycle and approved continuation have been observed.
