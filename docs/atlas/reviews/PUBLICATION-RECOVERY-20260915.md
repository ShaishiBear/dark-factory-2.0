# Prepared publication and App recovery — 15 September 2026

## Observed failure

Build [35006316929](https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/35006316929)
passed independent RED, implementation GREEN, both reviews, conformance, final GREEN and
quick, then the App opened [PR184](https://github.com/ShaishiBear/dark-factory-2.0/pull/184).
The sole provenance publisher refused because the fresh publication process omitted
`FACTORY_BASE_SHA`. The handoff already contained the captured base; `publish_prepared`
passed it only as `base_ref` to `_run_env`.

Certified head: `7099e8684e28fe817d4997af1f198e9c5ffd0660`.
Captured base: `9ce22882ca7c38b7a693f89a73666ddfb1f7f3c8`.
Test-author commit: `9903d79eca0a6d906afa0bb13d1c01b76f009644`.
Frozen `app/frontend/src/components/CitationModal.test.tsx` SHA256:
`02df6a391fc3347fa8b4e8304c513ec5ac1677099e46f0d4251ac812c3e32421`.
All 12 required saved builder artifacts exist; the final proof matches that head and
the frozen file hash matches the actual Git blob. These are recovery preconditions,
not independent qualification or evidence of an operational factory.

## Repair

The fresh process passes the handoff's base as both base reference and exact SHA.
Malformed base identities refuse before push or PR creation. A test drives the real
publication and attachment methods and proves the publisher receives the captured base,
including when an unrelated base exists in the ambient environment.

The existing human-triggered resume recognized only the historical Actions Bot. Programme
recovery now requires successful admission of the exact linked issue from protected main
and a matching immutable REST App author of type Bot. Admission is repeated before
publication. Other Bots, GraphQL login spellings, human accounts, unbound issues, edited
scope and retired programmes cannot borrow this authority. Legacy ordinary Actions
recovery remains available. Exact-head artifacts, frozen RED files, one-resume limit,
blinded preparation and full independent qualification remain required.

## Concrete recovery after normal maintainer delivery

Requires the owner's authorization under FACTORY_RULES section 7: one artifact-only resume
of PR184 from run35006316929. Keep the same programme
`d809b087a48a5d3a634286dc5f303d90ce119139a799ace0a122a7e244766b00`,
record remaining continuation budget4 and parent35006316929. Let the resume return issue181
to accepted only after successful publication; do not pre-emptively relabel it or rebuild.
Resume consumes one action and does not emit an automatic successor. After success, one
ordinary pulse may continue with remaining3 and the resume run as parent. Normal stale-base
recovery may be needed after the maintainer repair advances main; preserve all its gates.
No manual product merge, frozen evidence edits, attempt-marker changes or budget reset.

PR184 quick35010208529 and trust-root35010208481 passed; its automatic maintainer merge
was correctly skipped. Full qualification, App merge, post-merge verification, receipt and
dependent focus work have not yet been observed.