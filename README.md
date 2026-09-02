# The Dark Factory Experiment (RAG YouTube Chat App)

**A public Level-4 autonomous software factory experiment.** Humans file issues and promote releases. The repo-owned factory can triage, design, test, implement, review, independently validate and merge product changes without a human reading each product diff.

The factory does **not** write its own roadmap or invent its own issues. Its judge is also deliberately outside the product worker's authority: ordinary autonomous PRs cannot modify the kernel, validation harness, holdouts, security guard, evidence policy, CI, deployment control plane or governance files that decide whether those PRs are safe to merge.

The application itself is a dark-mode AI chat app for grounded conversations about a creator's YouTube videos, with cited answers drawn from transcript passages. The larger experiment is the factory that maintains it.

![Main chat interface](app/screenshots/screenshot-main.png)

---

## The Dark Factory

The orchestration authority now lives **in this repository**. Archon was useful during the original experiment, but it is no longer a runtime dependency.

The canonical control-plane entrypoint is:

```bash
python -m factory_kernel dispatch --once
```

The canonical unattended scheduler is `.github/workflows/dark-factory-worker.yml`: it invokes one dispatch at minute 17 of every hour and also supports manual `workflow_dispatch`. The workflow/state/authority logic itself lives in `factory_kernel/`, `.factory/`, `scripts/factory_*.py` and `harness/`. The checked-in systemd unit/timer remain an optional self-hosted scheduling alternative, not a second control plane.

### Three distinct layers

1. **Factory kernel — repo-owned Python.** `factory_kernel/` owns dispatch, isolated Git worktrees, GitHub coordination, worker boundaries, evidence provenance and exact-head merge orchestration.
2. **Reasoning worker — replaceable.** The checked-in default is Claude Code CLI. A worker may investigate, design, author tests, implement or review, but worker output never directly authorizes merge.
3. **Engineering authorities — deterministic and independent.** Contract compilation, immutable RED/GREEN replay, architecture governance, dependency/security checks, holdouts, mutation testing, the full harness, Evidence Bundle and merged-tree verification decide whether work can advance.

That distinction is load-bearing: **model confidence is not evidence**.

### One autonomous cycle

```text
emergency stop
  ↓
stale-lease reaper
  ↓
pending PR validation (highest priority)
  ↓ otherwise
accepted issue build
  ↓ otherwise
bounded issue triage
  ↓ otherwise
idle
```

The scheduler is intentionally uninteresting. The canonical GitHub-hosted workflow invokes the one-shot kernel command; the optional systemd timer does the same for a deliberately self-hosted deployment. All policy stays in the repo-owned kernel.

### How an issue becomes code

```text
GitHub issue
   │
   ├─ plan, or reproduce/investigate if it is a bug
   │
   ├─ typed execution contract
   │    └─ deterministic contract compiler
   │
   ├─ bounded context + design
   │    └─ deterministic provenance compilation
   │
   ├─ fresh architecture governor
   │    └─ deterministic architecture policy check
   │
   ├─ independent acceptance-test author
   │    └─ deterministic RED replay + immutable test hashes
   │
   ├─ implementation worker
   │    └─ deterministic GREEN replay
   │
   ├─ fresh code review
   │    └─ at most one fresh-context repair before re-review
   │
   ├─ post-code architecture conformance
   │    └─ exact HEAD + binary-diff binding
   │
   ├─ canonical quick gate
   │
   └─ PR with attached canonical contract + final proof
```

Each model stage is a fresh process. The implementation worker cannot rewrite the independently-authored acceptance tests recorded by RED.

### Independent validation and merge

A PR carrying `factory:needs-review` is validated from its **exact GitHub head SHA in a separate worktree**. The validator does not need the builder's narrative to establish correctness.

```text
exact PR head
  ↓
deterministic security/dependency guard
  ↓
blinded holdout outside the source checkout
  ↓
independent architecture holdout
  ↓
Evidence Bundle v5
  ├─ revalidates the attached contract
  ├─ revalidates the attached proof
  ├─ reconstructs and independently replays RED
  ├─ independently replays GREEN
  ├─ recomputes architecture policy applicability
  ├─ requires deterministic security pass
  └─ runs the full canonical harness
  ↓
exact-head/tree merge authorization
  ↓
second emergency-stop check
  ↓
squash merge using the expected head SHA
  ↓
post-merge verification that main contains the exact authorized tree
```

The logical merge authority is deterministic code (`scripts/factory_evidence.py` and `harness/merge_verify.py`), not an LLM saying “looks good”.

### Emergency stop

Two independent stop mechanisms are checked before dispatch and again immediately before merge:

- `${FACTORY_WORKDIR}/.factory-stop` — works even if the network is down.
- any open GitHub issue carrying `factory:stop` — reachable remotely.

The remote check fails closed. If GitHub stop state cannot be read, the factory stops.

### Labels are visible coordination state

The kernel keeps coordination visible in GitHub rather than an opaque database.

**Issues:** `factory:accepted`, `factory:in-progress`, `factory:rejected`, `factory:rate-limited`, `factory:needs-human`.

**PRs:** `factory:needs-review`, `factory:needs-fix`.

**Priority:** `priority:critical|high|medium|low`.

The stale-lease authority in `scripts/factory_lease.py` prevents abandoned `factory:in-progress` claims from wedging the queue.

### GitHub-hosted activation prerequisites

The unattended worker fails closed unless repository configuration is ready for Level-4 authority:

- GitHub Issues must be enabled because issues are the intake, state and remote-stop surface.
- `main` must be protected by GitHub branch protection/rules so a direct push cannot bypass the in-repo evidence and exact-tree merge authority.
- all eight control labels must exist: `factory:accepted`, `factory:rejected`, `factory:rate-limited`, `factory:in-progress`, `factory:needs-review`, `factory:needs-fix`, `factory:needs-human`, `factory:stop`.
- Actions secrets `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` and `SUPADATA_API_KEY` must be configured.

Validation database/JWT/browser-account state is not a persistent secret requirement: each hosted run creates disposable local Postgres and synthetic E2E state. See [`FACTORY.md`](FACTORY.md) for the full operational contract.

### Flood protection

Triage remains bounded. Non-owner accounts are capped at three issues per UTC calendar day; excess issues receive `factory:rate-limited` and become eligible again after UTC midnight. `scripts/frontier_filter.py` deterministically excludes blocked work before the triage model sees the batch.

### The protected trust root

An ordinary autonomous product PR is rejected if it tries to modify its own judge. Protected surfaces include:

- `factory_kernel/`
- `.factory/kernel.json`
- `.factory/evidence-spine.json`
- `.factory/prompts/`
- `.factory/holdout/`
- `harness/`
- `scripts/factory_*`
- architecture and ratchet policy
- `.github/`
- `deploy/systemd/`
- `MISSION.md`, `FACTORY_RULES.md`, `CLAUDE.md`
- deployment/environment-secret surfaces

Trust-root changes are human-reviewed infrastructure work.

### Canonical checks

```bash
# fast deterministic gate
python harness/ci.py --quick

# full merge-authority gate
python harness/ci.py

# protected holdouts
python .factory/holdout/run.py

# real-source + factory trust-root mutations
python harness/mutations/run.py
```

The full harness includes the real browser E2E journey. Its credentials/browser/runtime prerequisites are explicit: missing infrastructure fails rather than silently skipping the journey.

### Archon provenance

The repository retains attribution and some historical benchmark/reference material from the earlier Archon-based experiment. Active Dark Factory Archon workflows have been removed; the current kernel does not load or execute them. See `THIRD_PARTY_NOTICES.md` for attribution.

For the operational contract and host layout, see [`FACTORY.md`](FACTORY.md).

---

## The Application

What the factory is actually building.

### Architecture

```text
┌─────────────────┐       /api proxy        ┌─────────────────────────┐
│    Frontend     │ ─────────────────────── │        Backend          │
│  React + Vite   │    localhost:5173 →     │       FastAPI           │
│  TypeScript     │        :8000            │                         │
│  Tailwind CSS   │                         │  Routes ── RAG Pipeline │
└─────────────────┘                         │    │        │           │
                                            │    │     Chunker        │
                                            │    │     (Docling)      │
                                            │    │        │           │
                                            │    DB    Embeddings     │
                                            │(Postgres) (OpenRouter)  │
                                            │            │            │
                                            │         Retriever       │
                                            │  (RRF hybrid: tsvector │
                                            │   + pgvector cosine)    │
                                            │            │            │
                                            │           LLM          │
                                            │    (Claude via          │
                                            │     OpenRouter)         │
                                            └─────────────────────────┘
```

- **Frontend:** React 18 + Vite + TypeScript + Tailwind CSS (Bun)
- **Backend:** Python FastAPI, single process handling API + RAG + LLM
- **Database:** Postgres via asyncpg, with pgvector for hybrid retrieval
- **LLM:** Claude Sonnet via OpenRouter with SSE streaming
- **Embeddings:** `text-embedding-3-small` via OpenRouter
- **Chunking:** Docling HybridChunker
- **Retrieval:** Reciprocal Rank Fusion combining Postgres tsvector full-text search with pgvector cosine similarity, top-5 chunks

### How it works

1. **Ingest** — video transcripts are chunked and embedded.
2. **Sync** — `POST /api/channels/sync` enumerates and ingests new videos from a YouTube channel via Supadata.
3. **Retrieve** — queries run through Postgres full-text and pgvector cosine search; Reciprocal Rank Fusion combines the rankings.
4. **Generate** — top chunks are passed to Claude, which streams a cited answer via SSE.

---

## Quick Start

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- [Bun](https://bun.sh)
- Postgres 16+ with the `pgvector` extension. There is no SQLite fallback.
- An OpenRouter API key

### Setup

1. Clone the repo and create `app/.env`. `deploy/.env.example` is the annotated variable reference. The minimum to boot locally is `DATABASE_URL`, `OPENROUTER_API_KEY` and `JWT_SECRET`.

2. Apply migrations:

```bash
cd app/backend && uv run alembic upgrade head
```

3. Start everything:

```bash
# Unix/Mac
cd app && ./start.sh

# Windows
cd app && start.bat
```

This installs Python dependencies with `uv sync --all-extras`, starts FastAPI on `:8000`, runs `bun install` if needed, and starts Vite on `:5173`. Set `SEED_ENABLE=true` if you want the mock video library; it is off by default.

4. Open `http://localhost:5173`.

### Manual start

```bash
# Backend
cd app/backend
uv sync --all-extras
cd .. && uv --project backend run uvicorn backend.main:app --reload --port 8000

# Frontend (new terminal)
cd app/frontend
bun install
bun run dev
```

### Checks

```bash
cd app/backend  && uv run ruff check . && uv run mypy . && uv run pytest
cd app/frontend && bun run lint && bun run type-check && bun run test
```

---

## Contributing

For product changes, **file an issue**. The factory triages well-scoped issues against `MISSION.md` and `FACTORY_RULES.md`, and accepted work enters the autonomous queue. If triage rejects an issue, sharpen the requirement and reopen it with the missing context.

Changes to the factory trust root itself are intentionally different: those are reviewed as infrastructure changes rather than being self-approved by the factory they modify.
