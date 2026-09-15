# Fresh programme admission and post-merge observation

The factory must reject obsolete work before spending on proof, and must use a fresh main
observation when issuing a post-merge result.

## Before expensive work

Validation and governed rehead now admit the linked issue against the current programme
before fetching candidate worktrees or starting model/proof work. Stop is checked again after
the remote admission reads. Retired, changed, edited, unmarked or closed programme items
refuse early. The existing final merge admission and stop checks remain in place.

## After the full post-merge ladder

Previously `post_merge.execute` fetched main once, before the long harness, then supplied that
cached observation to `result_payload`. If main moved during proof, the final exact-main
assertion compared two old identities and could issue a stale result.

The authority now fetches main again and reads the updated remote-tracking ref immediately
before constructing the result. A changed or unreadable main produces no post-merge proof
file. The full ladder, clean worktree checks, merge/tree identity and all existing proof
requirements are unchanged. Tests exercise the production control flow with main unchanged,
moving during the ladder, and unavailable at the final fetch.

## Validation and delivery

The integrated local quick gate passed 2,494 tests and seven static checks. Four early
admission mutations and two final main-refresh mutations were caught with green copied
baselines. The real citation run remains on fixed main `f8c2fab` while its existing post-merge
gate executes; this draft is held until that useful proof finishes. No live behaviour has
been changed by this repair yet.
