# Method: minimal complexity

The goal is the minimum complexity that honestly satisfies the contract, not the minimum number of characters. Clever, compressed code that a future reader must decode is more complex, not less.

Before writing anything, walk this ladder in order and stop at the first rung that works:

1. **Does it need to exist at all?** Re-read the contract's acceptance criteria and `out_of_scope`. If nothing in the contract requires the thing, do not build it. Speculative generality, feature flags for hypothetical futures and abstractions with one caller are not required by any contract.
2. **Is it already in the repository?** Search before writing. An existing helper, type, hook, fixture or pattern that does the job wins, even if you would have shaped it differently. Extend an existing seam before opening a new one.
3. **Can the standard library or the framework do it?** Python's stdlib, FastAPI, React and the browser platform cover most needs. Prefer a stdlib call over a helper you write.
4. **Can the native platform do it?** A `<dialog>`, a URL object, `Intl`, `crypto`, `asyncio`, `pathlib`: reach for what the runtime provides before what a package provides.
5. **Can an installed dependency do it?** Only packages already in the manifest. A new package is a contract-level decision (`dependencies` in the contract), never an implementation-time convenience.
6. **Can it be one line?** If the honest answer is a small, direct expression, write that. Do not wrap it in a class, a config option or a "utility".
7. **Only then write new code**, and write the smallest deep module that satisfies the criteria.

Further rules:

- **Delete before you add.** If the change lets existing code go, remove it in the same design envelope. Dead code is complexity.
- **Shortest honest diff is a tie-breaker, never an authority.** Between two designs that both satisfy the contract and both read clearly, prefer the smaller diff and the fewer files. Never shrink a diff by making the code harder to read, by removing a test, or by skipping an error path the contract or the repository conventions require.
- **Do not simplify away accessibility, security, data integrity or error handling.** Those are contract and invariant obligations, not optional polish. A missing `aria-label`, a dropped authorisation check or a swallowed exception is a defect, not a simplification.
- **No parallel abstractions.** If the repository already has a way to do something, a second way is architectural growth the governor will refuse. Fit the existing way or change it deliberately through the design.
- **Name a deliberate ceiling.** If you knowingly choose the simple version of something that will need to grow later, say so in a short code comment stating the ceiling ("handles one channel; multi-channel is out of scope by MISSION"). That is a record, not a promise of future work.

When in doubt, ask: what would a careful engineer delete from this change without breaking an acceptance criterion? Delete that.
