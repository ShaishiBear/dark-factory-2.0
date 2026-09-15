# Durable factory attempt observations

Status: locally implemented and tested. The archive workflow and data branch are not
activated. The live citation post-merge proof continues independently on unchanged main.

The target architecture requires failure trajectories before self-learning. Current diagnostic
artifacts expire after seven days. This change records a small, deterministic observation for
every completed canonical worker attempt, including cancellation and failures before a PR or
artifact exists. Missing diagnostics remain explicit gaps. They never become invented success,
zero cost or proof of completion.

## Record

The `dark-factory/trajectory` v1 record retains the platform run/attempt/outcome, exact source
revision, public configuration reference, issue/PR subjects, available programme/spec hashes,
repeated stages in order, model names present in the source configuration, effort, turns,
reported cost, timing, exit codes and termination flags. Selected contract, context, design,
RED/GREEN, review, evidence and merge artifacts contribute references and SHA-256 hashes.
The source revision identifies the corresponding committed prompts, methods and CLI pin.

Raw prompts, original wording, model answers, exception text, cookies and arbitrary artifact
strings are not copied into the archive. This is project-local metadata in the existing public
repository. The full diagnostic files remain subject to their original retention. Triage calls
without stage telemetry and interrupted jobs with no artifacts have source-level records and
explicit gaps. Detailed missing stages cannot be reconstructed from labels or issue closure.

## Capture and publication

`dark-factory-trajectory.yml` runs after the canonical worker completes, regardless of its
conclusion. It checks out its protected-main implementation, verifies the source run's exact
attempt and reads artifacts only as data. It performs no model call and holds no provider key.
An owner-only dispatch supports bounded backfill/recovery of a named completed attempt.

After collection, the workflow mints a fresh GitHub App Contents-write capability. It appends
one `<run>-<attempt>.json` to the dedicated orphan branch `factory/trajectories`. The branch
contains only non-executable JSON observations. Publication preserves the previous tree and
parent, uses a normal fast-forward update, checks the resulting file and never rewrites an
existing attempt. Byte-identical replays perform no write; conflicting observations refuse.
An uncertain ref update is observed without repeating its POST. A concurrent different append
may require an explicit archival recovery after the non-fast-forward refusal.

Each record is at most 250 KB, each diagnostic file at most 2 MB, each kernel attempt at most
500 stage rows, and the initial archive at most 10,000 records. Capacity exhaustion fails
visibly. No cloud resource, bucket, service, paid model call or additional recurring charge is
introduced. The existing cleanup workflow deletes only exact heads of merged PRs; this orphan
archive never opens a PR.

## Authority and activation

Every record says `authority: observation-only` and `learning_scope: project-local`. Neither
the archive workflow nor its branch participates in qualification, approval, merge, programme
completion or continuation. A capture failure leaves the original worker result unchanged and
is visible as a separate failed observation run. The archive branch is not checked out into a
worker or blinded judge. No learner, retrieval path, automatic method change or self-maintainer
is enabled here.

Deliver this protected implementation through the existing maintainer trust-root checks after
the useful live proof has finished. Then backfill a real failed attempt and a complete verified
cycle, inspect immutable records and exact replay behavior, and observe automatic capture of
the next completed canonical worker. Do not call remote archival operational based on fixtures.

## Validation

Local canonical quick passed 2,507 tests and seven static checks. Seventeen focused tests
passed, and eight causal trajectory defects were caught with a green copied baseline. The
first quick run identified the explicit App-operation inventory assertion; the new operation
was added to that assertion and its stale-token rejection test, then the full gate passed.

A read-only collection of real failed worker run `35014391657`, attempt 1, produced a local
trajectory with one kernel attempt, five stages and its PR subject. The unavailable merge
artifact is an explicit gap. This exercised live GitHub source/attempt metadata, source policy
and artifact download; it performed no App publication or model call. No durable remote archive
has yet been observed.
