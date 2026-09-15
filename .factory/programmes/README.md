# Programme admission bootstrap

There is deliberately no `active.json` in this change. The approved work supplied for the
factory itself is maintainer work, which the current product lane must not execute. Do not
invent product work to make an idle queue look busy.

This directory is protected by the trusted-base security guard and denied to model workers.
To activate a programme, a maintainer records the user's approved specification and its
bounded decomposition in `active.json`, checks it with `python -m factory_kernel
programme-check .factory/programmes/active.json`, and submits it through the existing
maintainer review path. Passing that command establishes structural validity only. It does
not approve the user's intent, qualify the implementation, or permit protected-path changes.

The scheduled worker reads this file from GitHub's current protected default branch. An
uncommitted file, a candidate branch or an `approved: true` model output cannot activate work.
Absence means there is no programme to materialize; ordinary accepted issues still work.

See [the implementation contract](../../docs/PROGRAMME_ADMISSION.md) for schema, execution,
failure handling, limits and the frontend boundary.
