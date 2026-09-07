# Dark Factory 2.0 — Architecture Document Index

**Branch while current qualification is in flight:** `architecture/dark-factory-2-target`

This index exists so future overseers do not use chat transcripts, terminal history or private model memory as competing architecture sources.

## Read order

1. `docs/DARK_FACTORY_2_TARGET_ARCHITECTURE_AND_OVERSEER.md`
   - owner-approved architectural north star;
   - trust boundaries;
   - Front Door / programme / Preflight / graph / learning direction;
   - sequencing principles;
   - overseer anti-waste / stabilisation discipline.

2. `docs/DARK_FACTORY_2_QUALIFICATION_ACCELERATION.md`
   - measured qualification bottlenecks;
   - proof reuse by dependency identity;
   - EXACT_TREE / TRUST_ROOT / LIVE_WORLD replay semantics;
   - `FactoryTrustRootAttestation` direction;
   - detector-specific mutation qualification;
   - validator fan-out;
   - post-merge tree-proof transfer;
   - performance targets and adversarial tests.

3. Current repository-protected decisions / ADRs / trust policy.
   - These remain authoritative for the currently implemented factory.
   - Target documents do not silently waive current gates.

4. `HANDOVER.md` / current programme state.
   - Live state, not architectural authority.

## What is NOT an architecture source of truth

Do not treat any of the following as canonical when these documents cover the same subject:

- ChatGPT transcripts;
- Claude terminal transcripts;
- private Claude memory files;
- old handover snapshots;
- abandoned implementation branches;
- stale run-specific observations that have since been superseded.

Those can be evidence or historical context, but they are not the place to rediscover architecture.

## Promotion rule

Future substantial owner-approved design work should be promoted into a bounded repo document or existing ADR rather than left only in conversation history.

Do **not** dump raw conversation text into the repository. Promote:

- durable decisions;
- explicit invariants;
- canonical schemas;
- algorithms;
- dependency ordering;
- benchmarks/adversarial cases;
- measured baselines that remain useful;
- unresolved decisions that are intentionally deferred.

Leave out:

- repeated explanations;
- superseded recommendations;
- conversational framing;
- temporary polling/status chatter;
- stale run IDs unless they support a durable measured baseline.

## Current next documents to promote when ready

The following should become separate canonical documents when their design is mature enough, rather than expanding the two documents above indefinitely:

- detailed Front Door / Grill-Me algorithm and benchmark suite;
- repo-mapped TCB/kernel migration plan;
- canonical data schemas (approved spec, graph event, question, assumption, candidate, recommendation, authority attestation, factory handoff, trajectory, lease);
- programme-synthesis/DAG compiler contract;
- Preflight strategy-selection contract if implementation needs more detail than the north-star document provides.

The aim is a small, navigable architecture set — not a second giant transcript inside Git.
