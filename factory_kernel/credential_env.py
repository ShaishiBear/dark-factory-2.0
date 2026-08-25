"""Capability-scoped environment construction for trusted factory subprocesses."""
from __future__ import annotations

import os
from typing import Mapping

GITHUB_CREDENTIALS = ("GH_TOKEN", "GITHUB_TOKEN")
VALIDATION_CREDENTIALS = (
    "DATABASE_URL",
    "OPENROUTER_API_KEY",
    "JWT_SECRET",
    "SUPADATA_API_KEY",
    "YOUTUBE_CHANNEL_ID",
    "DARK_FACTORY_E2E_EMAIL",
    "DARK_FACTORY_E2E_PASSWORD",
)
PROVIDER_CREDENTIAL_PREFIXES = (
    "ANTHROPIC_",
    "CLAUDE_",
    "AWS_",
    "GOOGLE_",
    "AZURE_",
)
SCOPES = {"none", "github", "validation", "github+validation"}


def _sensitive(name: str) -> bool:
    return (
        name in GITHUB_CREDENTIALS
        or name in VALIDATION_CREDENTIALS
        or name.startswith(PROVIDER_CREDENTIAL_PREFIXES)
    )


def scoped_environment(
    extra: Mapping[str, str] | None = None,
    *,
    scope: str = "none",
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment with only explicitly authorized credentials.

    Provider/model credentials are intentionally never reintroduced here. Model workers
    have their own stricter provider boundary. Deterministic subprocesses may receive
    GitHub authority, dedicated application-validation credentials, both, or neither.
    """
    if scope not in SCOPES:
        raise ValueError(f"invalid credential scope: {scope}")
    original = dict(os.environ if source is None else source)
    child = {key: value for key, value in original.items() if not _sensitive(key)}

    if scope in {"github", "github+validation"}:
        for key in GITHUB_CREDENTIALS:
            if original.get(key):
                child[key] = original[key]
    if scope in {"validation", "github+validation"}:
        for key in VALIDATION_CREDENTIALS:
            if original.get(key):
                child[key] = original[key]

    if extra:
        for key, value in extra.items():
            if _sensitive(str(key)):
                raise ValueError(
                    f"credential {key!r} must be granted through credential scope, not extra env"
                )
            child[str(key)] = str(value)
    return child
