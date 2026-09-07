"""Capability-scoped environment construction for trusted factory subprocesses."""
from __future__ import annotations

import os
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


def _sensitive(name: str) -> bool:
    return (
        name in GITHUB_CREDENTIALS
        or name == GITHUB_MUTATION_CREDENTIAL
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

    if extra:
        for key, value in extra.items():
            if _sensitive(str(key)):
                raise ValueError(
                    f"credential {key!r} must be granted through credential scope, not extra env"
                )
            child[str(key)] = str(value)
    return child
