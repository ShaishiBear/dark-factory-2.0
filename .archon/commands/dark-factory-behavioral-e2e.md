---
description: Independent E2E validator that delegates browser behavior to the canonical base-branch harness journey.
argument-hint: (no arguments — reads $fetch-linked-issue.output, $fetch-pr.output, and $start-app.output)
---

# Dark Factory Behavioral E2E — Canonical Harness Authority

## Purpose

You are an independent reporter for the repository-owned browser floor. You do **not**
design browser steps, choose selectors, or call `agent-browser` directly. The canonical
journey lives only in `harness/e2e.py`; your job is to execute the trusted base-branch
copy against the PR's already-running app and translate its deterministic result into the
workflow's structured E2E schema.

This separation is deliberate:

- the PR branch supplies the software under test;
- `origin/main` supplies the browser test authority;
- this node supplies only issue-aware interpretation of what that fixed journey proves.

A PR must never be able to weaken its own E2E gate by editing `harness/`.

## Holdout rules

Do not read the PR diff, application source, implementation plans, commit history, prior
comments, or coder rationale. Do not inspect the materialized harness source. The only
permitted git operation is the exact `git archive origin/main harness` command below,
which copies the trusted harness into a temporary directory for execution.

Bash is permitted only to:

- read `$ARTIFACTS_DIR/.backend-port` and `.frontend-port`;
- materialize the base-branch harness with the exact archive command below;
- run that harness E2E CLI and capture its log;
- list evidence files under `$ARTIFACTS_DIR` after the run;
- remove the temporary directory.

**Never run `agent-browser` yourself.** If you do, there are two browser specifications
again and D-002 is back.

## Inputs

### Original issue
$fetch-linked-issue.output

### PR metadata
$fetch-pr.output

### App-start evidence
$start-app.output

## Required execution

Run exactly one canonical browser journey:

```bash
set -uo pipefail
BACKEND_PORT=$(cat "$ARTIFACTS_DIR/.backend-port")
FRONTEND_PORT=$(cat "$ARTIFACTS_DIR/.frontend-port")
HOLDOUT_ROOT=$(mktemp -d)
LOG="$ARTIFACTS_DIR/e2e-canonical.log"

cleanup() { rm -rf "$HOLDOUT_ROOT"; }
trap cleanup EXIT

git archive origin/main harness | tar -x -C "$HOLDOUT_ROOT"

set +e
ARTIFACTS_DIR="$ARTIFACTS_DIR" \
DARK_FACTORY_VALIDATION_ENV="${DARK_FACTORY_VALIDATION_ENV:-/opt/dark-factory/validation.env}" \
python "$HOLDOUT_ROOT/harness/e2e.py" \
  --backend-port "$BACKEND_PORT" \
  --frontend-port "$FRONTEND_PORT" \
  2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e

printf 'CANONICAL_E2E_RC=%s\n' "$RC"
ls -1 "$ARTIFACTS_DIR"/authenticated.png \
      "$ARTIFACTS_DIR"/citation.png \
      "$ARTIFACTS_DIR"/citation-modal.png 2>/dev/null || true
```

Do not substitute another browser procedure if this command fails. A missing base harness,
missing validation account, unavailable `agent-browser`, failed health/auth assertion, or
failed browser assertion is a real red E2E gate, not `not_e2e_testable`.

## Interpreting the deterministic result

The canonical journey proves these flows when and only when the process exits `0` and its
log contains `E2E_PASSED steps=N` with `N > 0`:

1. backend health and version endpoints answer;
2. anonymous conversation create/list remain blocked;
3. validation-account login reaches the real frontend;
4. a new chat question enters the streaming state;
5. a real conversation is created;
6. at least one citation exposes title/timestamp plus quoted evidence;
7. the citation modal and YouTube link point to the locked video at the exact timestamp.

Screenshots and `E2E_EVIDENCE ...` are supporting evidence, not the pass criterion.

### `solves_issue`

- `no` — the canonical harness returned non-zero or never emitted a positive
  `E2E_PASSED steps=N` marker. This is a regression block even when the linked issue is
  unrelated to chat.
- `yes` — the harness passed **and** the linked issue's user-visible acceptance criteria
  are entirely within the seven canonical flows above.
- `partially` — the harness passed and proves some, but not all, user-visible criteria in
  the issue.
- `not_e2e_testable` — the harness passed, but the linked issue is an internal change or
  its user-visible behavior lies wholly outside the canonical flows. This means only
  "this fixed journey does not prove that issue-specific behavior"; it does not weaken
  the canonical regression pass.

Do not invent issue-specific browser evidence for criteria outside the canonical journey.
The static behavioral reviewer evaluates those requirements from the issue and diff; the
weekly comprehensive suite owns broader exploratory E2E coverage.

### `app_booted`

Set true only when the start-app input contains `APP_STARTED` **and** the canonical run
reached its live HTTP/browser assertions. If startup evidence is absent, set false and
`solves_issue: "no"`.

## Output

Return JSON matching the workflow schema:

- `solves_issue`: `yes | partially | no | not_e2e_testable`
- `app_booted`: boolean
- `flows_tested`: use the canonical flow names actually proved; do not add flows
- `criteria_results`: map issue criteria to `pass/fail/skip` using only the canonical log
- `regressions_observed`: deterministic failures from the log, otherwise `[]`
- `evidence_captured`: `e2e-canonical.log` plus screenshots that actually exist
- `confidence`: `high` for an unambiguous positive/negative harness result; lower only if
  issue-to-canonical-flow mapping is genuinely ambiguous
- `reasoning`: concise explanation of the deterministic run and what it does/doesn't prove

## Success conditions

- `BASE_AUTHORITY`: browser code came from `origin/main`, never the PR branch.
- `ONE_JOURNEY`: you did not call `agent-browser` directly or invent selectors/steps.
- `POSITIVE_MARKER`: a pass requires `E2E_PASSED steps=N`, never absence of errors.
- `FAIL_CLOSED`: harness failure is `solves_issue: no`, not a skip.
- `HONEST_SCOPE`: criteria outside the canonical journey are marked skip/not-e2e-testable,
  not fabricated as passed.
