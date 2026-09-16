# The validation and experiment harnesses

The repository owns the factory kernel and its validation ladder. The `.archon/`
files are historical sources, not the executing workflow or merge authority.
DynaChat is the inherited application used by the current product checks; its
presence does not establish the owner's intended future product.

```bash
python harness/ci.py --quick  # static + backend/frontend/factory unit tests
python harness/ci.py          # full ladder; requires the validation environment
```

`harness.config.json` selects the commands. `budgets.json` owns ladder deadlines.
`static.py` and `unit.py` collect the checks across both application stacks and the
factory itself. Missing tools, zero tests, missing markers and failed checks are
failures. The protected floors in `.factory/locks/floor.json` are minimums, not
claims about a run that has not happened.

`e2e.py` now drives the built SPA in a real browser as well as checking its HTTP
surface: authentication, streamed answers, citation rendering and the transcript
modal. It is no longer an HTTP-only five-assertion substitute. The full gate also
executes independent holdouts, mutations and immunity checks. The current source
and the exact run's evidence determine counts; historical counts are not current
validation results.

The canonical process is documented in `FACTORY_RULES.md` and `FACTORY.md`.
`dark-factory-ci.yml` supplies the head-based quick gate, while
`dark-factory-trust-root.yml` runs its security authority from the trusted base.
Autonomous product delivery also requires the kernel's exact-revision evidence
and independent authorities. Experiment scores do not replace any of these.

## Validation environment

Use the dedicated validation database and credentials named in `serve.py` and
`harness.config.json`, supplied outside the repository. The full browser journey
uses a dedicated account and fixture content. `DARK_FACTORY_VALIDATION_ENV`
overrides the validation environment file. Missing configuration must refuse the
run; never interpret an unavailable browser or service as a pass.

## Harness improvement experiments

These are separate from production qualification:

- [Offline replay](../docs/HARNESS_REPLAY.md) executes reviewed rule adapters and
  simulated validation orchestration with source identity and retained attempts.
- [Bounded feedback experiment](../docs/HARNESS_FEEDBACK.md) compares one draft
  with public-check/repair rounds under the same aggregate reservation. Candidate
  Python executes in disposable containers; the host scores its returned values.
- [Trajectory analysis](../docs/FACTORY_RUN_ANALYSIS.md) reports captured
  observations without assigning causal blame or changing runtime policy.

PR #223's checked tree passed 2,961 unit tests and eight static checks on
2026-09-16, followed by 20/20 offline replay attempts on its merged commit.
Those are dated measurements of that tree, not claims about later revisions.
