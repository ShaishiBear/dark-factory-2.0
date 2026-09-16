# Catch immunity drift in the quick gate

The full mutation prerequisite refused current main because IMM-007 still required the old
`expectedHeadOid: $head` GraphQL string. PR #190 replaced that effect with `gh pr merge
--match-head-commit`, preserving the actual exact-head guard, but the retained text detector
was not updated. The later stop-aware merge repair and quick checks did not expose this drift.

IMM-007 now names the current exact-head command and also requires the bounded required-check
wait and explicit all-passing result check. All18 active obligations remain; their assertions
increase from65 to67. No proof requirement is retired, skipped or made optional.

The static rung now runs the real immunity verifier before expensive checks. This makes the
quick gate catch a future source refactor that leaves a stale detector. That live verification
stays outside mutation copies: running it inside every copied test suite would falsely count
unrelated deliberate source changes as detected defects. The unit test checks the static
registration only, and a causal mutant proves removing the registration is detected.

This repairs a proof-path prerequisite. It does not recover issue #189, qualify any product
change or claim the full end-to-end ladder has run at the new head.
