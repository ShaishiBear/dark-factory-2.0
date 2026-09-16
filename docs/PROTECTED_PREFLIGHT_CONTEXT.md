# Fresh repository context for hosted Preflight

`inspect_protected_repository(github, selected_paths, check_stop=...)` supplies the hosted
counterpart of `inspect_repository(checkout, selected_paths)`. Both use the same analyser
and return the same canonical shape and identity for the same committed bytes. The hosted
service can therefore pin its own release independently while comparing recommendations
against current protected product state.

The helper reads repository identity, visibility and the main branch through the supplied
authenticated GitHub client's GET API. Main must be protected. It resolves the exact commit
and root tree, refuses incomplete trees, and reads selected files plus both protected
policy files by blob identity. It checks regular-file modes, advertised and decoded sizes,
encoding and the actual Git blob digest. It re-reads branch and repository metadata after
analysis; a change refuses the whole observation. It never checks out or executes source,
writes to GitHub, receives arbitrary URLs, or calls a model.

The same 1–40 explicit product source paths, 50 KB per source, 200 KB source total and
100 KB per policy limits apply. Remote calls have at most 15 seconds each within a shared
120-second observation deadline. The optional stop callback runs before each API request
and before returning. Missing files, nonregular objects, invalid encoding, changed context,
unreadable APIs and exhausted deadlines fail closed. Parsing remains incomplete static
analysis: dynamic imports/runtime dispatch and unselected files remain explicit gaps;
JavaScript import extraction is lexical. No returned fact is a qualified product claim.

Hosted wiring can supply:

```python
context = lambda: inspect_protected_repository(github, selected_paths, check_stop=check_stop)
exploration = Exploration(store, context, check_stop=check_stop, app_login=app_login)
```

Use the owning service's configured repository/client and existing authentication and stop
policy. This helper has no HTTP endpoint. A returned snapshot is not an atomic lock on
future main; the publication adapter must still regenerate the exact current review and
perform its own final currency checks immediately before effects. The local helper reads
only committed checkout HEAD and should not stand in for this fresh hosted observation.

Tests compare local and remote context identity from real fixture Git commits, and refuse
wrong revisions, changed visibility, lost protection, missing policies, truncated trees,
symlinks, malformed blobs, content/hash mismatches, invalid paths, excessive sizes, stop
and timeout. Causal mutations individually remove currency, content identity, complete
tree, protected-main and regular-file checks after a green copied baseline. A live read
also observed main at `fa74f84f459d7ea838f72c00c2f78f263028e710` on 2026-09-16 for
`app/backend/rag/retriever_hybrid.py` and `app/backend/rag/tools.py`, with identity
`4f96c99ec3c105df7e18fbdca215f8ab2ca5a0041cabc01df8182dd69a4221bb`. That observation
is historical source evidence, not deployment or product qualification.
