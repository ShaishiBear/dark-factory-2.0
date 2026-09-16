# Factory run analysis

## Decision and delivery plan

This implements the 2026-09-16 request to turn retained factory experience into a
measurable improvement process. It starts with deterministic, offline analysis;
no new model, paid request, scheduler, or automatic policy change is needed.

The initial checkout was old. Inspection and coordination with **Continue Dark
Factory implementation** and **Implement claim-based evidence layer** established
that trajectory capture and evidence retention already exist on protected main.
Implementation uses isolated branch `codex/factory-run-analysis`, based on
`958b6201b519220793f36ed184c7bab4dd6c1295`. Existing worktrees, services, publication,
Front Door, qualification and archive writers are outside this change.

### Scope

1. Consume existing version 1.0 trajectory JSON snapshots, including failures
   before a PR, source-only observations, repeated stages and workflow reruns.
2. Validate identity, repository, observation-only scope and the fields used in
   calculations. Bound input size. Reject conflicting observations rather than
   selecting the most convenient version. Preserve missing measurements.
3. Produce reproducible JSON and Markdown reports containing the exact input
   hashes, workflow outcomes, stage/model costs and durations, recorded failure
   signals, repeated stages, repair invocations, and collection gaps.
4. Link each observation to its GitHub workflow attempt. Report only observed
   symptoms; do not infer causal explanations from stage names or private logs.
5. Exercise malformed/incomplete/adversarial inputs and known arithmetic with
   synthetic tests. Then run the report against a fixed real archive snapshot,
   followed by the repository quick gate in an isolated validation checkout.
6. Commit the implementation separately and provide the other tasks with its
   integration boundary and validation. Main delivery remains coordinated.

### Acceptance criteria

- Running the report performs no network call, model inference, Git effect or
  mutation of input files. Only stdout/stderr are written by the command.
- The same records in a different order yield the same canonical report.
- Identical duplicate identities count once; conflicting identities count zero
  and make the report explicitly incomplete.
- Unknown agent cost is not zero. Reported amounts remain provider-reported
  estimates, not reconciled invoices. Never multiply a stage's aggregate cost
  by its provider attempt count.
- Exec durations and agent durations are separate. Summed stage durations are
  not claimed to equal wall time because observations can overlap.
- A failed stage followed by a successful retry remains a failed stage event;
  it is not labelled the terminal cause of the workflow outcome.
- Workflow success, a retained proof filename, and an issue number do not
  establish verified product completion. Cost per verified completion and human
  intervention counts remain unavailable with an explicit explanation.
- Empty, malformed, oversized, mixed-repository and conflicting data never
  produce a quietly complete report. Source gaps are visible, including missing
  merge artifacts which can be expected on runs that did not enter merge.
- Raw prompts, model output, exception messages and retained proof bodies are
  not copied into the report. Metadata is projected through bounded fields.

### Follow-on experiments

Use the report to pick one measured bottleneck. For causal failure classification,
prepare operator-labelled historical cases with `unknown` and distinguish labels
from observed reason codes. Split by issue/incident (not individual stages) to
avoid related retries leaking across development and evaluation sets. Freeze the
questions and evaluation split before comparing deterministic rules, existing
models and optionally Jev. Measure per-class recall, false alarms, abstention,
probability calibration, latency, and total cost including fallback work.

No confidence threshold becomes policy on the strength of model self-reports.
Any later live experiment starts as advisory shadow output. Promotion requires
evidence of lower cost per independently verified completion, shorter time or
less operator effort without degrading correctness. Historical capture gaps
must not be treated as failures, and historical proof must not become fresh
merge authority. This change does not implement a model benchmark, claim reuse,
learning injection into judges, or automatic method editing.

## Running the report

Export an immutable snapshot of the existing `factory/trajectories` data branch
to a local directory. Supply its flat `<run_id>-<attempt>.json` files, not raw
worker artifacts or a source-code checkout. Record the archive commit alongside
your results. The command itself has no GitHub client and needs no credentials.

```sh
python -m factory_kernel.trajectory_report \
  --archive /path/to/trajectory-snapshot \
  --repository ShaishiBear/dark-factory-2.0 --format markdown

python -m factory_kernel.trajectory_report \
  --archive /path/to/trajectory-snapshot \
  --repository ShaishiBear/dark-factory-2.0 --format json > /path/to/report.json
```

Write reports outside the snapshot directory. Exit 0 means the supplied records
were valid and non-conflicting, not that historical coverage is complete or any
work is qualified. Exit 2 means empty/incomplete input or refusal. Semantically
invalid records produce an explicitly partial report; malformed JSON, unexpected
files, filename/identity mismatches, symlinks and input bounds refuse the whole
snapshot without emitting a report. Bounds: 10,000 entries, 250,000 bytes per
record, 64 MB total. JSON output includes raw source-file hashes and a canonical
input-set hash. Hashes identify bytes; they do not authenticate the publisher.

Percentiles use nearest rank. Each recorded stage cost is counted once, including
when the provider reports multiple attempts. Unknown measurements stay null.
Repeated stages are additional observations of the same kind/name within one
kernel attempt; they are not assumed to be failed retries. Workflow reruns remain
separate by `(run_id, run_attempt)`. Different models and efforts are separate
groups, not a causal comparison: they may have handled very different tasks.

Retention-index absent-file counts describe a fixed artifact allowlist, so many
are expected for stages never reached or files not used in that phase. They are
not missing mandatory proofs. `non_ok_stages` is observed telemetry; it must not
be used as a labelled root-cause training set without independent labelling.

## First real snapshot

The read-only check uses public archive commit
`0c0fd1f5309ac1b3c67e8f7056a556d64e5e28e5`, downloaded on 2026-09-16.
Its canonical input-set hash is
`15b8a1ecf31f630368cce20fb1806b4ed0ba49dae0b38c9d5ac511c821567c4a`.

- All 12 observations accepted: 8 workflow successes and 4 failures.
- 111 stage observations, including 26 agent stages. Three runs have no stages.
- 25 agent stages report USD 17.267705 in total; one agent stage has unknown cost.
  This is a partial, unreconciled telemetry sum, not total factory expenditure.
- Three non-ok stage events: two `currency` refusals and one failed `test_author`.
- Every observation contains a collection gap, including expected absent-phase
  artifacts. None is promoted into a new product failure or proof decision.

This small sample supports investigating the two recorded currency refusals and
the failed test-author attempt using their existing diagnostics. It does not
establish that an alternative model or a changed threshold would improve them.
No paid run, archive rewrite or new provider was used to produce these results.

## Validation and handoff

The implementation passes 17 focused Linux tests and the existing quick gate
(`GATE_OK mode=quick`, 2,915 unit tests, all eight static checks). A separate
disposable-copy check confirmed that four deliberate defects cause assertion
failures: converting unknown cost to zero, counting conflicting identities,
promoting workflow success to verified completion, and multiplying aggregate
cost by provider attempts. No authority or mutation registry was changed.

Tracking issue: #219, held with `factory:needs-human` so the autonomous queue
does not build a duplicate. Delivery is a separate maintainer draft. The
claim-evidence task retains ownership of causal refusal evidence and subsequent
owner-reviewed feedback; this report supplies observations only. It is not wired
into any scheduler or Front Door, and does not change the running factory.
