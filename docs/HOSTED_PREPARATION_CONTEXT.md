# Current repository facts for hosted preparation

The Front Door service runs a pinned, tested release. Its installed checkout can lag product
main. Intent drafting and programme synthesis therefore read `MISSION.md`, `README.md` and
`docs/API.md` from current protected main through the authenticated GitHub reader. They do
not use the service release as product context, update its checkout, execute product code,
or expose private service files to a worker.

The shared protected-file reader checks repository identity, visibility, default branch and
branch protection before and after reading. It binds the complete Git tree, regular file
modes, exact blob identities, decoded sizes and bytes. Reads retain the existing 15-second
per-call and 120-second overall bounds. Intake accepts only these three fixed paths and at
most 100 KB total UTF-8 bytes. Source coverage is still these documents, not comprehensive
product understanding or software proof.

Before the first paid call, the private preparation record saves the source commit and
canonical context hash. After each successful proposer, auditor or programme worker, the
service reads current protected context again and compares its full canonical hash. Drift
or unavailable context fails the preparation before releasing a draft or programme review;
drift after the first proposer also prevents the auditor spend. Already produced output and
cost telemetry remain recorded. The same request cannot retry spending, and scope approval
and publication retain their separate existing checks. Historical records remain readable.

This is a currency check at a particular observation, not a lock preventing later main
changes. Approved product intent is not revoked by a later product commit. Workers still
derive implementation from their own current checkout and must pass every independent
qualification and merge gate. No new model budget, role, credential, workflow or effect
authority is introduced.

Validation covers a service pinned behind a newer product commit, dirty checkout isolation,
missing/nonregular/tampered facts, aggregate size, drift after all three worker roles,
same-commit content mismatch, unavailable currency, unchanged owner approval, one-shot
spending and HTTP service wiring. Causal defects exercise every new currency boundary and
the existing protected Git reader.
