"""The factory's synthetic E2E account must be one the login route accepts.

The worker provisions the account by writing the user row directly
(harness/bootstrap_e2e.py), below the route's pydantic validation. The second
production browser E2E (D-047) stalled on the login form because the pinned
address sat under `.invalid`, which EmailStr refuses with 422. This pins the
agreement between the bootstrap's literal and the route model so it cannot
drift apart again without a red test on the application side.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

BOOTSTRAP = Path(__file__).resolve().parents[3] / "harness" / "bootstrap_e2e.py"
REJECTED_LEGACY_EMAIL = "dark-factory-e2e@localhost.invalid"


def _pinned_validation_email() -> str:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    match = re.search(r'^VALIDATION_EMAIL = "([^"]+)"$', source, re.MULTILINE)
    assert match, "harness/bootstrap_e2e.py no longer pins VALIDATION_EMAIL"
    return match.group(1)


@pytest.mark.skipif(not BOOTSTRAP.is_file(), reason="application checkout without the harness")
def test_pinned_validation_email_passes_the_login_route_model() -> None:
    from backend.routes.auth import LoginRequest

    request = LoginRequest(email=_pinned_validation_email(), password="any-password")
    assert request.email == _pinned_validation_email()


def test_reserved_name_is_still_refused_by_the_login_route_model() -> None:
    from backend.routes.auth import LoginRequest

    with pytest.raises(ValidationError) as excinfo:
        LoginRequest(email=REJECTED_LEGACY_EMAIL, password="any-password")
    assert "special-use or reserved" in str(excinfo.value)


async def test_login_route_answers_422_for_a_reserved_name(monkeypatch) -> None:
    """The refusal is a validation error, so it happens before any repository call:
    no user lookup, no password check, no session cookie."""
    from backend.db import postgres as pg
    from backend.db import users_repo
    from backend.main import app

    async def noop() -> None:
        return None

    async def must_not_be_called(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("validation must refuse before the repository is consulted")

    monkeypatch.setattr(pg, "close_pg_pool", noop)
    monkeypatch.setattr(users_repo, "get_user_by_email", must_not_be_called)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.post(
            "/api/auth/login",
            json={"email": REJECTED_LEGACY_EMAIL, "password": "any-password"},
        )
    assert response.status_code == 422
    assert "special-use or reserved" in response.text
    assert "set-cookie" not in {k.lower() for k in response.headers}
