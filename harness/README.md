# The validation harness

```bash
python harness/ci.py --quick   # static + unit. ~3 min. Runs anywhere.
python harness/ci.py           # the whole gate. Needs the validation env (below).
```

Measured 2026-08-13 on `main`:

```
HARNESS_START mode=quick driver=http
STATIC_OK
UNIT_PASSED tests=549
GATE_OK mode=quick
```

549 = 390 backend (pytest, 67 skipped) + 159 frontend (vitest).

## What is in here, and where it came from

| File | Origin |
|---|---|
| `ci.py` · `appproc.py` | **Verbatim from the `build-dark-factory` skill.** The ladder and the app-process manager are the same in every factory; do not edit them here. |
| `harness.config.json` | This repo. Every command DynaChat runs. |
| `static.py` · `unit.py` | This repo. `ci.py` runs one command per rung and DynaChat is two stacks, so the split lives here rather than in the ladder. |
| `serve.py` | This repo. Starts the backend, or refuses with a named reason. |
| `e2e.py` | This repo. **A floor, not the full journey - read the next section.** |

Every command in `harness.config.json` is lifted from
`.archon/workflows/dark-factory-validate-pr.yaml`, so there is one definition of a green
build. **If you change one, change the other**, or the gate and the workflow will
disagree about what passing means and only one of them decides a merge.

## The honest gap

**This harness does not yet run FACTORY_RULES.md section 4.** Section 4 is the real
journey - sign in, ask a question with a known answer, watch the response stream, check
the citation renders with a timestamp deep-link, click it, see the modal open at the
right moment. That runs today in the validate-pr workflow's `behavioral-e2e` node via
agent-browser, and it is what actually has authority over a merge.

`e2e.py` asserts the HTTP contract underneath it: the app is genuinely serving, it can
report its version, and MISSION hard invariant 2 (no anonymous access to chat) holds
against a live process rather than against a mock. Five assertions. Real, worth having,
and **a smaller claim than section 4.**

Also absent, and named rather than quietly missing:

- **No holdout.** `.factory/holdout/` does not exist. Everything this harness runs is
  something the builder can read, which means everything here sits inside its
  optimisation loop. The independence property in FACTORY_RULES section 9 is enforced
  today by the *workflow* - fresh contexts, base-branch governance, the artifact
  tripwire - not by read-blocked assertion files.
- **No mutation set.** Nothing here has ever been shown to fail on purpose. A gate that
  has never gone red is a gate nobody has tested.
- **No ratchet.** Nothing stops 549 becoming 400 one deleted assertion at a time.

`ci.py` prints `HOLDOUT_ABSENT` and `MUTATIONS_ABSENT` on a full run for exactly this
reason: an absent rung is a fact in the log, never a silent pass.

## The validation env

The backend hard-requires `DATABASE_URL`, `OPENROUTER_API_KEY`, `JWT_SECRET`,
`SUPADATA_API_KEY` and `YOUTUBE_CHANNEL_ID` at **import** time. `serve.py` refuses to
start without them and names which are missing:

```
APP_START_REFUSED missing=DATABASE_URL,OPENROUTER_API_KEY,...
```

That refusal is deliberate. On 2026-04-18 the workflow launched uvicorn without loading
them, the process crashed at import, the health poll failed, and the synthesizer scored
it as `not_e2e_testable`. PR #80 auto-merged having never been driven through a browser
(`3fc03a0`). **A step that cannot run has to be loud, not absent.**

The file lives outside the repo because it holds secrets. Override the path with
`DARK_FACTORY_VALIDATION_ENV`; on the VPS it is `/opt/dark-factory/validation.env`.
Point it at a **dedicated validation database** - an E2E against production data is a
data-loss incident waiting for a slow afternoon.
