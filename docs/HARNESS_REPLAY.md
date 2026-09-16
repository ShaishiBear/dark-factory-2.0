# Offline harness replay

This is the first executable evaluation slice from the Harness Club assessment.
It builds on existing trajectory capture/retention and leaves the separate
trajectory-analysis work unchanged. It supplies bounded execution and retained
observations to the existing benchmark scorer; it has no qualification authority.

## Run

Use a Linux checkout (the same platform as the hosted factory), with Python 3.11+
and Git. No provider credentials, database, GPU or model calls are needed.

```bash
python -m factory_kernel.task_replay \
  --inputs harness/replay/inputs.json \
  --labels harness/replay/labels.json \
  --output /tmp/harness-replay-example \
  --repeats 2 --per-case-seconds 30 --total-seconds 300
```

The output directory must not exist. Every run preserves a source/configuration
manifest, frozen input and label files, a flushed/fsynced event journal, individual
attempt records and a final report. An interrupted process can leave an unmatched
`started` journal entry; that is incomplete execution, never a passing observation.
Repeat the command with a new output directory to retry; existing evidence is not
overwritten or silently reused. This version does not resume prior attempts.

Exit 0 means every assigned case produced an observation, source stayed unchanged,
and supplied expected labels matched. Exit 1 means a failed/incomplete experiment;
exit 2 means invalid setup. Without `--labels`, a complete run is explicitly
unscored. A successful unscored run makes no correctness claim.

## What runs, and what is simulated

Ten public development fixtures execute four reviewed adapters:

| Adapter | Real code exercised | Limits |
|---|---|---|
| Contract | `factory_protocol.validate_contract` | Given a supplied draft, not an LLM-generated interpretation of an issue. |
| Reproduction | `repro.validate_repro` and `repro.execute` | Process exit/output is a recorded fixture; no test command is launched. |
| Lease | `factory_lease.decide_reap` | Computes keep/reap/protect; does not touch a real lease or prove concurrent ownership. |
| Validation | Existing `harness.rehearsal.rehearse` | Real validation orchestration/provenance rules with fake GitHub, provider and executor. A `merge` outcome is simulated. |

Happy-path controls accompany refusals. The fixtures cover ambiguous/valid
contracts, passing/failing reproduction observations, fresh/expired leases,
successful simulated validation, rejected architecture, invalid base ancestry,
and a post-merge verification failure. They do not claim coverage of every case
in `.factory/benchmark/public.json`: oversized-task decomposition and actual
concurrent dispatch need their own executable fixtures.

Unexpected exceptions, nonzero child exits, invalid output, timeouts and skipped
attempts remain distinct failures. They cannot be converted into a correct
`reject` or `needs-human` answer. In particular, a simulated merge followed by
failed verification is `needs-human`, not successful completion.

## Isolation and reproducibility

Each attempt starts a fresh isolated-mode Python process in a temporary directory.
The environment has a temporary home and no inherited credentials, PATH or Python
startup configuration. Only registered adapter names and JSON inputs are sent to
the child. Labels, case IDs and incident/split metadata are not arguments to the
adapter. Adapters cannot be selected by arbitrary module path or shell command.

Network/subprocess tripwires detect accidental use by reviewed Python adapters.
**These are not an OS sandbox for hostile or model-authored code.** The child can
read the repository to import trusted code; label separation is an API property,
not a secure hidden-test boundary. No arbitrary worker, generated code, browser,
live provider or tool plugin can be plugged into this runner. Adding those requires
a separately reviewed execution boundary and evaluation design.

The manifest binds HEAD, actual bytes of tracked and untracked source under
`factory_kernel`, `scripts`, `harness`, `.factory` and `tests/factory`, the root
mission/rules/conventions/programme/README files, input/label
hashes, interpreter/platform and budgets. The source identity is rechecked after
execution. Local changes cannot masquerade as an evaluation of clean HEAD;
cross-run comparisons must compare the full manifest, not only `factory_sha`.
Manifests are provenance for experiments, not cryptographic attestations.

Input JSON rejects duplicate keys, nonfinite values, unknown fields, unsafe IDs,
unregistered adapters and an incident group occurring in multiple splits. Keep
related retries in one group. All shipped cases are development cases. The runner
cannot discover unlabelled relationships between cases or prevent manual reuse of
a confirmation set: corpus curation remains part of experiment review.

Per-case and total wall limits cap execution; repeats cap assigned work. Runtime
errors remain in the denominator. Reports deliberately leave live verified
completions unknown and mark model quality unmeasured. No cost-per-success or
prompt-improvement claim follows from this regression suite.

## Follow-on work

The other tasks own durable capture, analytics, claims/reconsideration, Front Door
and deployment. This slice owns only the new `task_replay*` modules, fixtures,
tests and this document. It changes no runtime, worker tools, judge configuration,
trajectory schema, proof reuse, budgets, scheduler or live services.

Use this foundation to validate future experiment adapters before spending on
model trials. The next capability experiment is [bounded worker feedback](HARNESS_FEEDBACK.md):
frozen task/acceptance fixtures, container/VM execution with no private judge
material, task-wide shared budget, and actual checks between drafts. The separate
feedback lab now implements an opt-in tool-less provider adapter and disposable
function checks. Production feedback, prompt optimisation, learned-memory
injection and persistent workers require evidence of benefit before production
integration, as the existing programme and evaluation plan specify.
