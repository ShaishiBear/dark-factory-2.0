#!/usr/bin/env python3
"""Provision the locked browser fixture in an explicitly local validation database.

This is validation infrastructure, not an application fallback. It is only allowed to
operate against the dedicated local database name used by the GitHub-hosted worker and
only with a synthetic validation account. The real Supadata + OpenRouter ingestion path
is reused so the browser gate still proves the production retrieval/citation pipeline.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
BACKEND = APP / "backend"
CONFIG = json.loads((ROOT / "harness" / "harness.config.json").read_text(encoding="utf-8"))
FIXTURE_VIDEO_ID = str(CONFIG["browser"]["fixture_video_id"])
FIXTURE_URL = f"https://www.youtube.com/watch?v={FIXTURE_VIDEO_ID}"
VALIDATION_DB_NAME = "dark_factory_validation"
# The synthetic account must be one the login route accepts. `POST /api/auth/login`
# validates the address with pydantic's EmailStr, which refuses special-use and reserved
# names (`.invalid`, `.localhost`, `.test`) with 422. This bootstrap writes the user row
# directly, so a reserved name would provision an account that can exist but never log in;
# that was the second production E2E failure (D-047). `example.com` is reserved for
# documentation by RFC 2606 yet is not on the validator's special-use list, so it is
# accepted, and it is guaranteed never to route to a real mailbox.
VALIDATION_EMAIL = "dark-factory-e2e@example.com"


def safe_local_validation_database(dsn: str) -> bool:
    """Return true only for the dedicated loopback validation database."""
    try:
        parsed = urlparse(dsn)
    except ValueError:
        return False
    return (
        parsed.scheme in {"postgres", "postgresql"}
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.path.lstrip("/") == VALIDATION_DB_NAME
    )


def _run_migrations() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "--config",
            str(BACKEND / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=APP,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode:
        detail = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
        raise RuntimeError(f"validation migrations failed: {detail}")


async def _bootstrap() -> int:
    if os.environ.get("DARK_FACTORY_E2E_BOOTSTRAP") != "1":
        print("E2E_BOOTSTRAP_REFUSED explicit DARK_FACTORY_E2E_BOOTSTRAP=1 required")
        return 1

    dsn = os.environ.get("DATABASE_URL", "")
    if not safe_local_validation_database(dsn):
        print("E2E_BOOTSTRAP_REFUSED database must be loopback dark_factory_validation")
        return 1

    email = os.environ.get("DARK_FACTORY_E2E_EMAIL", "").strip()
    password = os.environ.get("DARK_FACTORY_E2E_PASSWORD", "")
    required = {
        "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
        "SUPADATA_API_KEY": os.environ.get("SUPADATA_API_KEY", ""),
        "DARK_FACTORY_E2E_EMAIL": email,
        "DARK_FACTORY_E2E_PASSWORD": password,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"E2E_BOOTSTRAP_REFUSED missing={','.join(missing)}")
        return 1
    if email != VALIDATION_EMAIL:
        print(f"E2E_BOOTSTRAP_REFUSED email must be {VALIDATION_EMAIL}")
        return 1

    _run_migrations()
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))

    from pydantic import ValidationError

    from backend.auth.password import hash_password
    from backend.db import users_repo
    from backend.db.postgres import close_pg_pool, get_pg_pool, init_pg_pool
    from backend.routes.auth import LoginRequest
    from backend.routes.ingest import IngestRequest, ingest_video
    from backend.services.video_ingest import fetch_video_for_ingest

    # Provision only what the login route itself would accept. The row is written below
    # the route's validation, so this is the only place the mismatch can be refused.
    try:
        LoginRequest(email=email, password=password)
    except ValidationError as exc:
        reasons = "; ".join(str(err.get("msg", "")) for err in exc.errors())
        print(f"E2E_BOOTSTRAP_REFUSED login route would reject the validation account: {reasons}")
        return 1

    await init_pg_pool()
    try:
        pool = get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM users WHERE email = $1", email)
            stale = await conn.fetch("SELECT id FROM videos WHERE url = $1", FIXTURE_URL)
            for row in stale:
                await conn.execute("DELETE FROM videos WHERE id = $1", row["id"])

        await users_repo.create_user(email=email, password_hash=hash_password(password))

        fetched = await fetch_video_for_ingest(FIXTURE_URL, lang="en")
        if fetched.get("youtube_video_id") != FIXTURE_VIDEO_ID:
            raise RuntimeError("Supadata fixture identity did not match locked video id")
        transcript = str(fetched.get("transcript") or "").strip()
        if not transcript:
            raise RuntimeError("locked validation fixture returned an empty transcript")

        result = await ingest_video(
            IngestRequest(
                title=str(fetched.get("title") or f"Video {FIXTURE_VIDEO_ID}"),
                description=str(fetched.get("description") or f"Ingested from {FIXTURE_URL}"),
                url=FIXTURE_URL,
                transcript=transcript,
                segments=fetched.get("segments") or None,
            )
        )
        if result.status != "ok" or result.chunks_created < 1:
            raise RuntimeError(
                f"locked validation fixture did not produce chunks: "
                f"status={result.status} chunks={result.chunks_created}"
            )
        print(
            f"E2E_BOOTSTRAP_OK fixture_video_id={FIXTURE_VIDEO_ID} "
            f"chunks={result.chunks_created}"
        )
        return 0
    finally:
        await close_pg_pool()


def main() -> int:
    try:
        return asyncio.run(_bootstrap())
    except Exception as exc:  # fail closed with one diagnosable marker
        print(f"E2E_BOOTSTRAP_FAILED {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
