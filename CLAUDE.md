# CLAUDE.md

Instructions for AI coding agents working in this repository. Read this before making any code changes.

This file covers **how the code is written**. For *what* to build, see `MISSION.md`. For *how the factory operates*, see `FACTORY_RULES.md`. When this file and those conflict, MISSION.md wins on scope, FACTORY_RULES.md wins on process, and CLAUDE.md wins on code style.

---

## Project Overview

**DynaChat** is a RAG-powered chat interface that lets viewers query a single YouTube channel's content and get streaming answers with per-chunk citations that deep-link to the exact timestamp in the source video. FastAPI + Python backend, React + Vite + TypeScript frontend, Postgres + pgvector everywhere (local dev and production — there is no SQLite mode; see **Database** and **Deployment** below).

This repository is also the home of the **Dark Factory**, the repo-owned autonomous control plane that maintains DynaChat. `FACTORY.md` describes the factory's runtime; `FACTORY_RULES.md` describes its rules. The factory's own code (`factory_kernel/`, `.factory/`, `harness/`, `scripts/factory_*`, `tests/factory/`) is trust root, not product code — see **Protected paths** at the end of this file.

---

## Tech Stack

**Backend**
- Python 3.11+ (not specified in any lockfile; don't rely on 3.12+ features)
- `uv` for package management (not pip, not poetry) — `backend/pyproject.toml` is the dependency source of truth, `backend/uv.lock` pins exact versions
- FastAPI with `uvicorn[standard]` ASGI server
- `asyncpg` for async Postgres access (via connection pool from `db/postgres.py`)
- `alembic` for schema migrations (one Alembic migration layer for all tables)
- `docling-core[chunking]` for transcript chunking (HybridChunker)
- `openai` SDK pointed at OpenRouter's OpenAI-compatible endpoint (for both embeddings and chat completions)
- `numpy` for in-process cosine similarity
- `python-dotenv` for config loading

**Frontend**
- Bun (not npm, not pnpm — use `bun install`, `bun run dev`, `bun run build`)
- React 18.3, TypeScript 5.4, Vite 5.2
- `react-router-dom` v6 for routing
- `react-markdown` + `remark-gfm` for assistant message rendering
- `react-syntax-highlighter` for code blocks
- Tailwind CSS 3.4 (no component library — components are built from Tailwind primitives)
- Vanilla `fetch()` for API calls (no axios, no SDK) — typed wrappers in `src/lib/api.ts`

---

## Repo Layout

```
dark-factory-2.0/
├── MISSION.md               # Product scope — the factory reads this at triage
├── FACTORY_RULES.md         # Factory operational rules — every factory stage reads this
├── CLAUDE.md                # This file — code conventions
├── FACTORY.md               # Factory runtime: control plane, build/validate/merge paths
├── README.md                # Human-facing quick start
├── docs/
│   ├── API.md               # HTTP API reference
│   ├── dynachat.prd.md      # Product requirements (reference, not governance)
│   └── agents/              # Agent-facing conventions (domain docs, issue tracker, pinned skills)
├── factory_kernel/          # TRUST ROOT — the repo-owned control plane (dispatch, build, validate, merge)
├── .factory/                # TRUST ROOT — kernel policy, prompts, holdouts, evidence spine, ratchet floors, decisions log
├── harness/                 # TRUST ROOT — canonical CI ladder, E2E journey, mutation suites, immunity registry
├── scripts/factory_*.py     # TRUST ROOT — deterministic authorities (security guard, evidence, protocol, architecture)
├── tests/factory/           # TRUST ROOT — the factory's own detector tests
├── .github/workflows/       # TRUST ROOT — dark-factory-trust-root.yml (base-anchored authority + unattended merge), dark-factory-ci.yml (head quick gate), dark-factory-worker.yml (hourly dispatch)
├── deploy/                  # Docker Compose stack, Caddy, blue/green deploy script, optional systemd units
├── .archon/                 # Legacy Archon prompt sources kept for provenance; the kernel does NOT load them
├── app/
│   ├── start.sh             # POSIX bootstrap: venv → deps → uvicorn + bun dev
│   ├── start.bat            # Windows equivalent
│   ├── backend/
│   │   ├── main.py          # FastAPI app factory, lifespan init (pool + alembic upgrade), /api/health, SPA catch-all
│   │   ├── config.py        # All env var reads + hardcoded constants
│   │   ├── rate_limit.py    # 25 msg/user/24h cap (MISSION hard invariant #1)
│   │   ├── signup_rate_limit.py # Signup abuse guard
│   │   ├── pyproject.toml   # uv dependencies + tool config (ruff, mypy, pytest)
│   │   ├── uv.lock          # uv lockfile (committed, pinned versions)
│   │   ├── .env.example     # Placeholder env file for local dev
│   │   ├── alembic/         # Schema migrations (the only way schema changes)
│   │   ├── auth/            # tokens.py, password.py, dependencies.py (get_current_user / get_current_admin)
│   │   ├── data/
│   │   │   └── seed.py      # Mock videos seeded on startup when SEED_ENABLE is on
│   │   ├── db/
│   │   │   ├── postgres.py  # asyncpg pool (get_pg_pool)
│   │   │   ├── repository.py # ALL raw chat/video/chunk SQL lives here — nowhere else
│   │   │   ├── users_repo.py, user_messages_repo.py, signup_attempts_repo.py  # auth + audit tables
│   │   ├── ingest/          # Transcript source parsers (youtube_url.py, dynamous.py)
│   │   ├── integrations/    # circle.py — Circle membership verification
│   │   ├── llm/
│   │   │   └── openrouter.py # stream_chat() async generator, SSE-formatted output, tool loop
│   │   ├── rag/
│   │   │   ├── catalog.py      # In-process video catalog cache; builds cache_control block for system prompt
│   │   │   ├── chunker.py      # Docling HybridChunker wrapper
│   │   │   ├── citations.py    # Citation assembly (title, URL, timestamp deep-link, snippet)
│   │   │   ├── embeddings.py   # embed_text / embed_batch via OpenRouter
│   │   │   ├── expansion.py    # Neighbouring-chunk expansion window
│   │   │   ├── retriever_hybrid.py  # RRF hybrid retrieval (tsvector + pgvector)
│   │   │   └── tools.py        # LLM-driven retrieval tools (search_videos, keyword/semantic search, get_video_transcript)
│   │   ├── routes/
│   │   │   ├── admin.py         # /api/admin/* (gated by ADMIN_USER_EMAIL)
│   │   │   ├── auth.py          # /api/auth/* signup, login, me
│   │   │   ├── channels.py      # POST /api/channels/sync, GET /api/channels/sync-runs
│   │   │   ├── conversations.py # GET/POST/DELETE /api/conversations*, GET /api/videos
│   │   │   ├── messages.py      # POST /api/conversations/{id}/messages (streaming SSE)
│   │   │   └── ingest.py        # POST /api/ingest
│   │   ├── services/
│   │   │   ├── supadata.py      # Supadata API client (channel video enumeration, transcript fetching)
│   │   │   ├── video_ingest.py  # Ingest orchestration (fetch → chunk → embed → store)
│   │   │   └── youtube_meta.py  # YouTube Data API metadata
│   │   ├── scripts/         # One-off operator scripts (sync_channel.py, eval_retrieval.py, migrate_sqlite_to_pg.py)
│   │   └── tests/           # pytest suite + fixtures/ (recorded external-API responses)
│   └── frontend/
│       ├── package.json      # Bun dependencies + scripts (dev, build, type-check, lint, test)
│       ├── biome.json        # Lint + format config
│       ├── vite.config.ts    # Dev server port 5173, API proxy to backend 8000
│       ├── tsconfig.json
│       ├── index.html
│       └── src/
│           ├── main.tsx      # React root
│           ├── App.tsx       # BrowserRouter + layout
│           ├── pages/        # Login, Signup, AdminVideos, NotFound
│           ├── components/   # ChatArea, Sidebar, Message, MarkdownRenderer, ChatInput, CitationModal, VideoExplorer, AddVideoModal, BrandingHeader, ToastProvider
│           ├── hooks/        # useAuth, useConversations, useMessages, useStreamingResponse, useAdminVideos, useToast
│           ├── lib/
│           │   ├── api.ts    # All typed fetch wrappers + TypeScript interfaces
│           │   ├── authApi.ts # Auth fetch wrappers
│           │   └── exportMarkdown.ts
│           ├── __tests__/    # Vitest tests (co-located *.test.tsx also exist)
│           └── styles/
│               └── globals.css # Tailwind imports
```

**Placement rules** (where new files go):

- New API routes → new file in `app/backend/routes/`, one file per resource. Mount from `main.py`.
- New SQL queries → `app/backend/db/repository.py` only. Never write SQL in route handlers, services, or components.
- New schema changes → a new Alembic migration under `app/backend/alembic/versions/`. Never `CREATE TABLE` from application code. See "Database".
- New RAG pipeline steps → `app/backend/rag/`. Keep chunker, embeddings, and retriever as separate modules.
- New React components → `app/frontend/src/components/`, one component per file, named exports matching filename.
- New React hooks → `app/frontend/src/hooks/`, prefix with `use`.
- New API client functions → `app/frontend/src/lib/api.ts`. Keep all fetch calls in this one file.

---

## Running the App

Install and start everything (backend venv + deps, frontend deps, both dev servers). You need a reachable Postgres and `DATABASE_URL` set first; the app refuses to start without it:

```bash
cd app
./start.sh         # POSIX
start.bat          # Windows
```

Manual backend:

```bash
cd app/backend
uv sync --all-extras                   # creates backend/.venv, installs runtime + dev deps
cd ..
uv --project backend run uvicorn backend.main:app --reload --port 8000
```

Backend **must** be run from `app/` (not `app/backend/`) — the `backend.main:app` import path requires it. Running from the wrong cwd gives `ModuleNotFoundError: No module named 'backend'`. The `--project backend` flag tells uv to use `app/backend/.venv` while cwd is `app/`.

Manual frontend:

```bash
cd app/frontend
bun install
bun run dev           # dev server with HMR
bun run build         # production build → dist/
bun run preview       # serve built assets
```

---

## Testing

Tests exist and are a merge gate. Backend: `app/backend/tests/` (pytest, ~40 modules plus `fixtures/` with recorded external-API responses). Frontend: `app/frontend/src/__tests__/` and co-located `*.test.tsx` (Vitest). The factory's canonical unit gate (`harness/unit.py`) runs both suites plus the factory's own `tests/factory/` suite and reports one `UNIT_PASSED tests=N` count that is ratcheted in `.factory/locks/floor.json`. The browser E2E journey (`harness/e2e.py`, see FACTORY_RULES.md §4) is a separate gate and does not replace unit/integration tests.

**Python backend:**

```bash
cd app/backend
uv run pytest tests -xvs
```

All backend tool invocations run from `app/backend/` so that `pyproject.toml` (which holds ruff, mypy, pytest config) is picked up. Running the tools from `app/` with `--project backend` works for package resolution but mypy/pytest **do not** auto-discover config from a non-cwd project, so you'd silently lose the exclude lists and asyncio mode.

- Test directory: `app/backend/tests/` (`conftest.py` sets fake secrets; `fixtures/` holds recorded responses)
- `pytest`, `pytest-asyncio`, and `httpx` are declared in `backend/pyproject.toml` under `[project.optional-dependencies].dev` — installed by `uv sync --all-extras`
- Use `pytest-asyncio` for async tests (`asyncio_mode = "auto"` is set in `pyproject.toml`, so plain `async def` test functions work)
- Use `httpx.AsyncClient` against a test FastAPI app for integration tests
- Tests must not depend on a live database or live external API. Mock the repository/HTTP boundary (see **Testing external APIs** under Deployment).

**TypeScript frontend:**

```bash
cd app/frontend
bun run test
```

- Test directory: `app/frontend/src/__tests__/` or co-located `*.test.tsx` files (Vitest, Testing Library and jsdom are already installed)
- Use Vitest (not Jest — Vite-native, faster)
- Mock `fetch` with `vi.stubGlobal('fetch', ...)` for hook tests

---

## Lint, Format, Type Check

**Backend tooling is configured in `app/backend/pyproject.toml`:** ruff (lint + format, line-length 100, target py311, conservative rule set — E/F/W/I/B/UP/SIM/RUF), mypy (lenient `strict = false`, `warn_return_any = true`, `ignore_missing_imports = true`), pytest (asyncio auto mode). All three are in the `dev` optional-dependency group and installed by `uv sync --all-extras`.

**Python:**

```bash
cd app/backend
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

**TypeScript:** `biome` (configured in `app/frontend/biome.json`). Do not add eslint or prettier alongside it.

```bash
cd app/frontend
bun x biome check src
bun x biome format --write src
bun run tsc --noEmit           # type check
```

**Before every commit (what the factory's static and unit rungs run — `python harness/ci.py --quick` runs exactly this ladder):**

```bash
# Backend (from app/backend/)
cd app/backend
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests -xvs
cd ../..

# Frontend
cd app/frontend
bun run tsc --noEmit
bun x biome check src
bun run test
```

---

## Code Conventions

### Python (backend)

- **Async everywhere.** FastAPI routes are `async def`. Database calls use `asyncpg` via a connection pool. Any sync blocking call (file I/O, CPU work) in a route handler is a bug — use `asyncio.to_thread` or move it to a background task.
- **Imports:** stdlib first, third-party second, local third. Group with blank lines. No wildcard imports.
- **Type hints:** use them on every function signature and return type. Use `list[str]` / `dict[str, int]` syntax (Python 3.9+), not `List` / `Dict` from `typing`.
- **No `print()` in runtime code.** Use `logging` with a module-level logger: `logger = logging.getLogger(__name__)`. `print()` is acceptable in `data/seed.py` and one-off scripts.
- **Errors:** raise specific exceptions (`ValueError`, `KeyError`, custom) with clear messages. Never `except:` bare. Avoid `except Exception` except at the outermost request handler, where FastAPI's exception handlers take over.
- **SQL:** all queries live in `db/repository.py` (or the auth/audit `*_repo.py` modules). Parameterize — never use f-strings or `%` formatting to build SQL. `asyncpg` uses `$1, $2...` placeholders.
- **Config:** every environment variable is read exactly once in `config.py` and exposed as a module-level constant. Routes and services import the constant, never `os.environ` directly.
- **Pydantic models:** use `pydantic.BaseModel` for request/response schemas, defined in the route file that uses them (unless shared).

### TypeScript (frontend)

- **Function components only.** No class components. Named exports, one component per file. File name matches component name.
- **Hooks for state and effects.** Custom hooks live in `src/hooks/`, prefixed `use`, returning a typed object.
- **All API calls go through `src/lib/api.ts`.** Components and hooks import from there. Never `fetch()` inline in a component.
- **Types:** every function signature typed; no `any` except when bridging an untyped dependency with a clear comment explaining why. Prefer `interface` for object shapes, `type` for unions and aliases.
- **Imports:** use relative paths within `src/` (no path aliases configured currently). External libraries first, then internal.
- **Styling:** Tailwind utility classes only. No inline `style={{...}}` except for dynamic values that can't be expressed in Tailwind. No CSS modules, no styled-components.
- **Event handlers:** typed callbacks (`(e: React.ChangeEvent<HTMLInputElement>) => void`), not `any`.
- **State:** React built-ins (`useState`, `useReducer`, Context) only. Do not add Redux, Zustand, Jotai, or any external state library — it's out of scope.
- **SSE parsing:** all SSE consumption goes through `useStreamingResponse`. Do not parse SSE in components or new hooks.

---

## Database

**Current state:** Postgres via `asyncpg`. All tables (chat + auth) live in Postgres. Schema is managed by Alembic migrations. Connection pool initialised in the FastAPI lifespan handler via `db/postgres.py:get_pg_pool()`. No ORM. No SQLite.

**Tables:** `users`, `user_messages`, `signup_attempts`, `videos`, `chunks` (FK → videos), `conversations`, `messages` (FK → conversations), `channel_sync_runs`, `channel_sync_videos` (FK → channel_sync_runs). All use `TIMESTAMPTZ` for timestamps. TEXT primary keys for chat tables (compatible with client-side IDs).

**Alembic workflow:** All schema changes go through Alembic migrations. The initial migration (`0001_initial.py`) creates all tables. On startup, the app runs `alembic upgrade head` automatically in the lifespan handler.

**Rules for database code:**
1. All SQL lives in `db/repository.py` — parameterised, no f-string interpolation.
2. Use `$1, $2, $3...` placeholders for asyncpg (not `?` as in aiosqlite).
3. All timestamps stored as TIMESTAMPTZ via ISO 8601 strings parsed by Postgres.
4. TEXT primary keys for chat tables (text UUIDs generated via `_new_id()`).
5. UUID primary keys for auth tables (`gen_random_uuid()` in Postgres).

---

## RAG Pipeline Invariants

These behaviors are part of DynaChat's contract and must not regress. The agent-browser regression test verifies most of them.

1. **Chunking** uses Docling `HybridChunker` with `max_tokens=512` (`HYBRID_CHUNKER_MAX_TOKENS` in `config.py`). Do not swap to recursive-character splitters or LangChain chunkers.
2. **Embeddings** come from OpenRouter's `openai/text-embedding-3-small` (1536-dim). Never call a different embedding model or provider. Never embed on the frontend.
3. **Retrieval** is hybrid Reciprocal Rank Fusion (RRF) over Postgres tsvector keyword search and pgvector cosine similarity (`app/backend/rag/retriever_hybrid.py`, authorized by issue #59), driven by LLM tool calls (`rag/tools.py`, `LLM_TOOLS_ENABLED`). Constants (`RETRIEVAL_TOP_K = 5`, RRF `k = 60`, per-video cap) live in `config.py`. Do not introduce an external vector database (FAISS, Chroma, a hosted service) — that's an architectural change requiring an explicit issue and human approval.
4. **Chat completion** goes through OpenRouter via the `openai` SDK pointed at `https://openrouter.ai/api/v1`. The model slug is `CHAT_MODEL` (default `anthropic/claude-sonnet-4.6`), overridable per deployment by env only. Do not change the provider, and do not change the default model in a PR — that's out of scope per MISSION.md.
5. **Streaming format:** Server-Sent Events with JSON-encoded tokens. Each token is framed as `data: <json-string>\n\n`. The `sources` event is emitted as `event: sources\ndata: <json-array>\n\n` **before** the `data: [DONE]\n\n` terminator. Do not change this format — the frontend parser in `useStreamingResponse.ts` depends on it exactly.
6. **Citations** must include video title, video URL, exact timestamp deep-link, and the quoted transcript snippet. The citation modal opens an embedded YouTube player at the timestamp. This is a MISSION.md quality bar — removing or regressing any of these fields is an auto-reject.

---

## Environment Variables

All env var reads happen in `app/backend/config.py`. Add new variables there and import the constant elsewhere.

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | **yes** | Authenticates embeddings and chat completions to OpenRouter |
| `SUPADATA_API_KEY` | prod (YouTube ingestion) | Fetches YouTube transcripts via Supadata. Required for channel sync and manual ingestion |
| `YOUTUBE_CHANNEL_ID` | prod (channel sync) | YouTube channel ID/handle to sync videos from via `POST /api/channels/sync` |
| `CHANNEL_SYNC_TYPE` | prod (channel sync) | Content type filter for channel sync: `all`, `video`, `short`, `live`. Default: `video` |
| `DATABASE_URL` | **yes** (prod + dev) | Postgres connection string. Shape: `postgresql://dynachat:<pw>@127.0.0.1:5433/dynachat`. The app refuses to start if this is unset (no SQLite fallback). |
| `CORS_ORIGINS` | No (dev default) | Comma-separated list of allowed CORS origins. Defaults to `http://localhost:{FRONTEND_PORT},http://127.0.0.1:{FRONTEND_PORT}`. Used in `app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS)` in `main.py`. |
| `CATALOG_ENABLED` | No (default: `false`) | Injects a video-catalog block into the system prompt to enable Anthropic prompt caching. Accepted values: `1`, `true`, `yes`, `on`. Adds input tokens on every request (even cache hits). |
| `CATALOG_TIER` | No (default: `standard`) | Cache tier: `standard` = ~5-min ephemeral; `extended` = 1-hour TTL (3600 s). Ignored when `CATALOG_ENABLED` is false. |
| `CATALOG_CACHE_TTL_SECONDS` | No (default: 3600) | In-process catalog cache lifetime. |
| `JWT_SECRET` | **yes** (whenever `DATABASE_URL` is set) | HS256 signing secret for auth tokens (7-day expiry). 32+ random bytes in prod. Protected: see **Protected paths**. |
| `ADMIN_USER_EMAIL` | prod (admin UI) | The single hardcoded admin identity (MISSION "Administrative surface"). Empty = every `/api/admin/*` call returns 403. |
| `CIRCLE_ADMIN_TOKEN`, `CIRCLE_PAID_ACCESS_GROUP_ID` | prod (paid content) | Circle membership verification. Missing = every user treated as non-member (fails closed). |
| `MEMBERSHIP_REFRESH_SECONDS` | No (default: 3600) | How stale a membership verification may be before `/me` re-checks Circle. |
| `CHAT_MODEL` | No (default: `anthropic/claude-sonnet-4.6`) | OpenRouter chat model slug. Per-deploy canary override only; never change the default in a PR. |
| `LLM_REASONING_EFFORT` | No | Reasoning-effort hint for reasoning models (e.g. `minimal`). |
| `LLM_TOOLS_ENABLED` | No (default: `true`) | Tool-driven retrieval. Off = diagnostic fallback with no context. |
| `LLM_TOOLS_MAX_PER_TURN` | No (default: 6) | Cap on tool calls per turn (budget guard). |
| `RETRIEVAL_EXPANSION_WINDOW` | No (default: 1) | Neighbouring chunks pulled around each hit. |
| `RETRIEVAL_MAX_PER_VIDEO` | No (default: 3) | Per-video diversity cap after each search-tool call. |
| `CITATIONS_MAX_COUNT` | No (default: 10) | Cap on non-cited citations; cited chunks always pass through. |
| `TRANSCRIPT_TOOL_MAX_CHARS` | No (default: 120000) | Truncation for `get_video_transcript`. |
| `YOUTUBE_API_KEY` | prod (metadata) | YouTube Data API key for video metadata. |
| `SEED_ENABLE` | No (default: `false`) | Seed mock videos on startup. The factory's validation env sets it to `false`. |
| `FRONTEND_DIST` | prod | When set, the backend serves the built SPA from this directory via the catch-all route. |

The full list is the set of `os.environ` reads in `config.py`; that file is the source of truth if this table drifts. Everything else (ports, chunk size, top-k, RRF constants, JWT algorithm/expiry) is hardcoded there. When adding configurability, add the constant to `config.py` with a sensible default:

```python
NEW_CONSTANT: int = int(os.environ.get("NEW_CONSTANT", "42"))
```

**Never commit `.env` files.** `.env` and `.env.*` are in `.gitignore` (only `.env.example` files are allowed) and the security guard refuses any PR that adds or edits them. Placeholder templates live at `app/backend/.env.example` and `deploy/.env.example`.

---

## Deployment

**DynaChat ships via Docker Compose to a VPS at `chat.dynamous.ai`.** Source of truth for the compose stack lives in this repo at `deploy/` (committed, readable to the factory). The real `.env` lives **only** on the prod host at `/opt/dynachat/.env` (root-owned, mode 600) and is never in git and never in an LLM context.

**The factory does not run on the prod host.** It runs as a GitHub-hosted Actions worker (`.github/workflows/dark-factory-worker.yml`) with a disposable Postgres and synthetic credentials created per run. It has no login to the VPS, no production secrets, and no production database access. See `FACTORY.md` → Operations.

### Production host layout

```
/opt/dynachat/                     # root:root 700
├── .env                           # root:root 600 — real secrets, never committed
├── deploy.sh                      # hand-synced copy of deploy/deploy.sh, run by the systemd timer
└── app/                           # git clone of this repo
    └── deploy/
        ├── docker-compose.yml     # Caddy + Postgres + app-blue + app-green
        ├── Caddyfile              # TLS + routing; imports upstream.conf
        ├── upstream.conf.example  # template for the host-local upstream.conf (which color is live)
        ├── Dockerfile             # app image (protected: uvicorn proxy-header flags)
        ├── deploy.sh              # blue/green deploy script (source of truth; copied to /opt/dynachat/)
        ├── sync-channel.sh, sync-dynamous-content.sh  # content sync helpers
        ├── systemd/               # dynachat-channel-sync.* (app) and dark-factory.* (optional self-hosted factory scheduler)
        ├── .env.example           # placeholder template (committed)
        └── README.md              # first-time-setup runbook
```

If a change needs a new production secret, the PR body must say so under a "Manual smoke-test" section so a human adds it to the prod `.env` out-of-band. The factory never handles it.

### Services (via `deploy/docker-compose.yml`)

| Service | Image | Port | Purpose |
|---|---|---|---|
| `dynachat-caddy` | `caddy:2.8-alpine` | `80`, `443` | TLS termination + reverse proxy. Auto-provisions Let's Encrypt cert on first request |
| `dynachat-postgres` | `pgvector/pgvector:pg16` | `127.0.0.1:5433` | Primary database (loopback-only; no public exposure) |
| `dynachat-app-blue` / `dynachat-app-green` | `deploy/Dockerfile` | internal `:8000` | FastAPI plus the built frontend bundle (`FRONTEND_DIST`). Exactly one color is live; `upstream.conf` names it |

### Caddy routing (`deploy/Caddyfile`)

Caddy imports `/etc/caddy/upstream.conf` (host-local, generated from `upstream.conf.example`) to pick the live color. The backend serves both `/api/*` and the SPA from the same container, so there is no separate frontend upstream in production.

### What the factory is authorized to do in `deploy/`

The "Don't modify Dockerfiles, deployment configs" rule in the Don'ts list has one explicit exception: **when an issue asks for deployment work** (app Dockerfile, docker-compose additions, Caddy route changes), the factory may modify files inside `deploy/` and add a root-level `Dockerfile` for the app service. Anything touching `.env` or real secrets is still off-limits.

### YouTube ingestion on the VPS

Production transcript fetching uses **Supadata** (`SUPADATA_API_KEY`), not `youtube-transcript-api`. Digital Ocean IPs are blocked by YouTube's scraping defenses, which breaks `youtube-transcript-api` in prod. Supadata sits behind a managed residential proxy pool and is the only reliable option.

**Supadata client rules:**
1. Always pass the `lang` parameter (Supadata has a known bug where non-English-only videos 500 without it — pass `lang="en"` if you only need English, or iterate through available languages).
2. Handle rate limits gracefully — Supadata's free tier is generous but not infinite. Back off on 429.
3. The API key is read from `SUPADATA_API_KEY` in `config.py`; never inline the key anywhere.

### Testing external APIs (Supadata, OpenRouter, anything else with a secret)

**The factory does not have production API keys and will not get them.** Any PR that adds or modifies an external-API integration must ship with **mocked-boundary tests**, not live-key tests. Pattern:

1. Record real responses once (you, locally, with your key) into `app/backend/tests/fixtures/<service>/<scenario>.json`. Check the fixtures into git — they're public, non-sensitive transcripts/metadata.
2. In tests, use `httpx.MockTransport` or `respx` (for httpx-based clients) or `pytest` `monkeypatch` to short-circuit the HTTP client and return the fixture. Never hit the real API from a test.
3. Cover the happy path, a rate-limit (429), a transient 5xx, and any service-specific quirks (for Supadata: the missing-`lang` 500 case).
4. If a test needs a secret value to exist in `os.environ`, set it in `conftest.py` with a fake value like `"test-supadata-key"`. Never read from a real `.env`.

**PR acceptance for external-API work requires a "Manual smoke-test" section in the PR body** listing exactly what a human will run on the prod host after merge. Example:

```
## Manual smoke-test (post-merge)
On /opt/dynachat host:
1. `curl -X POST https://chat.dynamous.ai/api/ingest -d '{"video_id": "dQw4w9WgXcQ"}'`
2. Confirm transcript lands in `chunks` table: `psql -U dynachat -c 'SELECT count(*) FROM chunks WHERE video_id = ...'`
3. Ask a question about the ingested video in the chat UI; verify citations deep-link correctly
```

This is the sole place where production-only verification happens. The factory is never the entity running that smoke-test.

### The factory's validation database

The factory never touches production data. Each worker run provisions a fresh `postgres:16` service database (`dark_factory_validation`), a random `JWT_SECRET`, a synthetic E2E account and `DARK_FACTORY_E2E_BOOTSTRAP=1`, then runs Alembic migrations and ingests one locked fixture video through the real Supadata/OpenRouter path before the browser E2E journey. Everything is discarded when the run ends.

**Rules:**
1. Integration tests that need a database run against that disposable instance via `DATABASE_URL`, exactly as the app does. No code path may special-case "the factory's database".
2. Tests that need today's production content belong in a manual smoke-test, not an automated gate.
3. Unit tests still use fixtures — see "Testing external APIs" above.

### Redeploy flow — blue/green, automatic

Deploy is pull-based and **fully automated**. On the prod VPS, a systemd timer runs `/opt/dynachat/deploy.sh` (a hand-synced copy of `deploy/deploy.sh`) every 10 minutes. The script:

1. `git fetch`es `/opt/dynachat/app`; if `HEAD == origin/main`, no-op
2. Otherwise `git pull`, then blue/green swap:
   - Reads active color from `deploy/upstream.conf` (content: `reverse_proxy app-blue:8000` or `app-green:8000`)
   - Builds + starts the **inactive** color (`docker compose up -d --build --no-deps app-<inactive>`)
   - Polls `docker inspect ... Health.Status` for up to 90s
   - If inactive never goes healthy: abort, stop the failed container, keep active serving. Deploy fails loudly in the systemd journal.
   - If healthy: rewrite `upstream.conf`, `docker compose exec caddy caddy reload` (Caddy reload is graceful — no dropped connections), sleep 5s (drain), stop the old color

The factory's contract with prod is narrow: **merge a green PR to main, the VPS handles rollout within 10 minutes.** There is no CI deploy step, no webhook, no GitHub Action.

### What the factory must never do

The live deploy infrastructure (`/opt/dynachat/deploy.sh`, the host's systemd units, `/opt/dynachat/.env`, `/var/log/dynachat-deploy.log`) is host state and must not be touched by the factory. In addition, the security guard refuses autonomous PRs that modify `Dockerfile`, any `docker-compose*.yml`, `deploy/systemd/`, or any `.env*` file. If an issue seems to require editing systemd, secrets, or the deploy script, that's a sign the issue is scoped wrong — escalate to `factory:needs-human` rather than inventing deploy infra inside the repo.

The factory's lane, summarized:
- **Inside the repo, inside the PR, outside the protected set** → fair game (product code, tests, docs, `deploy/Caddyfile`, `deploy/deploy.sh`, `deploy/README.md`)
- **Protected deploy surfaces** (`deploy/Dockerfile`, `deploy/docker-compose.yml`, `deploy/systemd/`) → human maintenance lane only
- **Outside the repo** → off-limits (VPS state, secrets, systemd units, server processes)

### Zero-downtime is a hard requirement

Production must never 502 during a deploy. That's why blue/green exists. Any change to `deploy/` must preserve this property:

- Both `app-blue` and `app-green` services must be defined in `docker-compose.yml` with identical config except for `container_name`
- Each must have a `HEALTHCHECK` that only succeeds when the app is ready to serve traffic (not just "process started")
- Neither publishes its port to the host — Caddy reaches them via the internal docker network by service name
- The host-local `deploy/upstream.conf` (untracked; generated from `upstream.conf.example`) is the single source of truth for which color is live. The deploy script rewrites it; the committed example pins `app-blue`.
- `Caddyfile` must contain `import /etc/caddy/upstream.conf` (and the caddy service must mount that file read-only)

If a PR breaks any of the above, the deploy script will either refuse to swap (bad) or swap to an unhealthy container (very bad). Validate locally with `docker compose --env-file /opt/dynachat/.env -f deploy/docker-compose.yml config` before requesting review.

---

## Known Footguns (Fix These When You Touch Them)

These are existing bugs / quirks in the repo. They are fair game for the factory to fix when an issue is filed, but be aware of them so you don't accidentally depend on the broken behavior:

1. **Ports must agree.** `vite.config.ts` (dev server 5173, proxy to 8000) and `config.py` (`FRONTEND_PORT = 5173`, `BACKEND_PORT = 8000`) currently agree. If you touch either, keep them agreeing; the CORS default is derived from `FRONTEND_PORT`.
2. **Runtime dependencies are unpinned in `pyproject.toml`** but pinned in `uv.lock`. Do not add upper bounds to `[project].dependencies` in an unrelated PR — uv's lockfile handles reproducibility already. The security guard refuses a manifest change without a matching lockfile change.
3. **SSE tokens are JSON-encoded** (wrapped in quotes, escaped newlines). This is non-standard but intentional — it safely handles tokens containing newlines. The parser in `useStreamingResponse.ts` expects this exact format. Do not switch to raw-text SSE without updating both sides.
4. **`DATABASE_URL` is mandatory at import time.** `config.py` raises if it is unset, so any script or test that imports the backend needs it (tests get a fake from `conftest.py`).
5. **The legacy `.archon/` directory is not a source of truth.** Its command files are kept for provenance only; the kernel loads prompts from `.factory/prompts/`. Do not edit or cite `.archon/` as current behavior.

---

## Commit and PR Conventions

- **Commit messages:** conventional commits style — `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`. Subject line under 72 characters. Body explains *why*, not *what*.
- **PR title:** same conventional-commits prefix as the first commit. Under 72 characters.
- **PR body:** must include `Fixes #N` (or `Closes #N` / `Resolves #N`) on its own line so the validator can extract the linked issue. Missing this link causes validation to fail at behavioral-validation.
- **New or changed dependencies:** PR body must include a heading titled exactly `## Dependency justification` whose text names each added or version-changed package and explains what it does, why existing dependencies don't work, and evidence of active maintenance. The security guard (`scripts/factory_security.py`) fails the PR if the heading is missing, if a changed package is not named under it, if the manifest changed without its lockfile, or if the source is a git/URL/path dependency rather than a registry. In a factory build the contract's `dependencies` array is the only way to satisfy this: the kernel renders it into the PR body and refreshes the planned lockfile itself. See FACTORY_RULES.md §2.
- **One issue per PR.** Do not bundle unrelated fixes. If you notice a bug while working on something else, file a new issue rather than fixing it in the current PR.

---

## Dos and Don'ts (Quick Reference)

**Do:**
- Read MISSION.md and FACTORY_RULES.md before starting any non-trivial task
- Run `python harness/ci.py --quick` (static + unit) before declaring a PR done; the factory's validator runs the full ladder including the browser E2E journey
- Keep all SQL in `db/repository.py` (or the auth/audit `*_repo.py` modules)
- Keep all fetch calls in `src/lib/api.ts` (or `authApi.ts` for auth)
- Add tests for every bug fix (regression test) and every new feature

**Don't:**
- Modify `MISSION.md`, `FACTORY_RULES.md`, or `CLAUDE.md` (this file) — see FACTORY_RULES.md §5
- Modify the factory trust root (`factory_kernel/`, `.factory/`, `harness/`, `scripts/factory_*`, `tests/factory/`) — see **Protected paths** below
- Modify `.github/` or any `.env*` file (real secrets live only on the prod host — see **Deployment**)
- Touch `/opt/dynachat/` on the prod host, or try to read the production `.env` — that's the app's runtime concern, not the factory's
- Modify `Dockerfile`s or files in `deploy/` **unless** the issue is explicitly about deployment work (app service, Caddy route, compose additions)
- Introduce a new LLM provider, embedding model, or vector database
- Add state management libraries to the frontend
- Add an ORM to the backend
- Write SQL outside the `db/` repository modules or fetch calls outside `src/lib/api.ts` / `authApi.ts`
- "Improve" code that wasn't part of the issue you're fixing — the implementation worker may only write files inside the compiled design envelope, and the kernel refuses to commit anything outside it

---

## Protected paths (factory auto-rejects PRs touching these)

The deterministic security guard `scripts/factory_security.py` judges every PR as the required `trust-root-authority` check, which runs **from `main`** (a `pull_request_target` workflow that never checks out the PR head), and again inside the head-based `quick-authority` check as defence in depth. It refuses any protected path in an **autonomous** PR (one opened by the factory's Bot identity, or by anyone without a repository role). A PR opened by a maintainer's GitHub user account may change these paths through the human maintenance lane described in FACTORY_RULES.md §5; every other check still runs, and a green maintainer PR merges itself without a click.

**Factory trust root** (the machinery that judges product PRs):

- `factory_kernel/**`, `.factory/kernel.json`, `.factory/evidence-spine.json`, `.factory/architecture.json`, `.factory/locks/floor.json`, `.factory/prompts/**`, `.factory/methods/**`, `.factory/holdout/**`, `.factory/benchmark/**`
- `harness/**`, `scripts/factory_*`, `scripts/frontier_filter.py`, `tests/factory/**`
- `.github/**`, `deploy/systemd/**`, any `Dockerfile`, any `docker-compose*.yml`, any `.env*` file
- `MISSION.md`, `FACTORY_RULES.md`, `CLAUDE.md`

**Application security surface** — the following files implement or gate security invariants from `MISSION.md` "Hard Invariants". The blinded holdout defends owner-only access, the single cap value and per-user lock keying behaviourally; the path list covers what has no behavioural detector (token issuance, password hashing, the admin dependency, the signup guard, CORS):

- `app/backend/auth/` (entire directory)
- `app/backend/routes/auth.py`
- `app/backend/routes/admin.py` — sole consumer of `get_current_admin`; auth-adjacent per FACTORY_RULES §5
- `app/backend/routes/conversations.py` — implements MISSION §10 #3 (owner-only conversations)
- `app/backend/routes/messages.py` — implements MISSION §10 #3 (owner-only conversations)
- `app/backend/db/users_repo.py`
- `app/backend/db/repository.py` — conversation/message `user_id` scoping functions specifically
- `app/backend/main.py` — auth router registration and `Depends(get_current_user)` wiring
- `app/backend/config.py` — `JWT_SECRET` / `DATABASE_URL` handling
- CORS middleware configuration anywhere in the backend
- `app/backend/rate_limit.py` — implements MISSION §10 invariant #1 (25 msg/user/24h cap, hardcoded)
- `app/backend/db/user_messages_repo.py` — audit-table access for the rate-limit counter
- `app/backend/routes/messages.py` — the rate-limit enforcement call site (also listed above for owner-only conversations)
- `app/backend/signup_rate_limit.py` — signup abuse guard (1/IP/hr + 25 global/10min, hardcoded; see issue #54)
- `app/backend/db/signup_attempts_repo.py` — audit-table access for the signup rate-limiter
- `deploy/Dockerfile` — uvicorn `--proxy-headers --forwarded-allow-ips="*"` flags; signup IP trust boundary depends on these (see module docstring in `signup_rate_limit.py`)
- `MISSION.md` §10 invariant #1 (the cap value itself — 25 is not configurable)
