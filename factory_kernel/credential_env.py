"""Capability-scoped environment construction for trusted factory subprocesses."""
from __future__ import annotations

import os
import time
from typing import Mapping

GITHUB_CREDENTIALS = ("GH_TOKEN", "GITHUB_TOKEN")
# The autonomous identity: a short-lived GitHub App installation token, minted per job. It is a
# capability, not an ambient credential, and it is deliberately NOT one of GITHUB_CREDENTIALS --
# `scope="github"` must never carry it. Only the three GitHub mutations that need normal event
# semantics may spend it: pushing an autonomous branch, opening/updating an autonomous PR, and
# the exact-head merge. Everything else observes GitHub with the ordinary GITHUB_TOKEN.
#
# GitHub creates no workflow run for an event GITHUB_TOKEN caused, so a PR opened with it gets no
# `pull_request_target` run at all and its `pull_request` run is held for approval. Both are
# required contexts on `main` with no bypass actors, which is why no `factory/*` PR had ever
# merged. See docs/DARK_FACTORY_2_AUTONOMOUS_GITHUB_IDENTITY.md.
GITHUB_MUTATION_CREDENTIAL = "DARK_FACTORY_APP_TOKEN"
# When the installation token above was minted, as a Unix timestamp, exported by the workflow
# step that minted it. A GitHub App installation token lives 60 minutes and the kernel cannot
# see its expiry, so the mint time is the only thing that lets a spend fail closed BEFORE the
# API rather than after it. Its absence is not a free pass: `identity_age_seconds` returns None
# and the caller refuses, because an identity that cannot say how old it is cannot be shown to
# be fresh (ACP-004).
GITHUB_MUTATION_MINTED_AT = "DARK_FACTORY_APP_TOKEN_MINTED_AT"
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
SCOPES = {"none", "github", "validation", "github+validation", "github-mutation"}


def identity_age_seconds(
    now: float | None = None, source: Mapping[str, str] | None = None
) -> float | None:
    """How old the autonomous identity is, in seconds, or None when that cannot be established.

    None is a refusal, not a default. The whole point of this value is that run 34151427980
    spent a 95-minute-old token and learned it was dead from a 401; an identity that cannot
    state its age is exactly as unprovable as one that is known to be stale.
    """
    original = dict(os.environ if source is None else source)
    raw = original.get(GITHUB_MUTATION_MINTED_AT, "").strip()
    if not raw:
        return None
    try:
        minted = float(raw)
    except ValueError:
        return None
    if minted <= 0:
        return None
    age = (time.time() if now is None else now) - minted
    # A clock that runs backwards is not evidence of freshness.
    return age if age >= 0 else None


def _sensitive(name: str) -> bool:
    return (
        name in GITHUB_CREDENTIALS
        or name == GITHUB_MUTATION_CREDENTIAL
        # Not a secret, but it belongs to the identity: stripped by default and put back only
        # for `github-mutation`, so the age of a token can only be read where the token is.
        or name == GITHUB_MUTATION_MINTED_AT
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
    # `github-mutation` grants the App token ALONE: GH_TOKEN and GITHUB_TOKEN are stripped above
    # and not put back. A caller holding this scope therefore cannot silently fall back to the
    # Actions token when the App token is absent -- there is nothing to fall back to.
    if scope == "github-mutation" and original.get(GITHUB_MUTATION_CREDENTIAL):
        child[GITHUB_MUTATION_CREDENTIAL] = original[GITHUB_MUTATION_CREDENTIAL]
        if original.get(GITHUB_MUTATION_MINTED_AT):
            child[GITHUB_MUTATION_MINTED_AT] = original[GITHUB_MUTATION_MINTED_AT]

    if extra:
        for key, value in extra.items():
            if _sensitive(str(key)):
                raise ValueError(
                    f"credential {key!r} must be granted through credential scope, not extra env"
                )
            child[str(key)] = str(value)
    return child
