"""The one place a service learns which project and repository it serves (section 12, WP01).

Until this module, the repository name, owner, App login, project ID and publication origin
were constants copied into `publication_policy.py` and read from there by ten modules. The
profile keeps those exact values as the first profile, read from protected configuration in
the trusted checkout, never from request JSON. A second project is a second profile file
selected by trusted configuration, not a parameter a caller can pass.

The profile also carries what the platform must independently confirm before any effect:
the repository's exact numeric identity, its visibility and its default branch.
`verify_destination` compares a platform observation against those; a name match alone is
not a destination match, because names can be reassigned and numbers are not.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

SCHEMA = "dark-factory/project-profile"
SCHEMA_VERSION = "1.0"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_PATH = ROOT / ".factory" / "project-profile.json"
FIELDS = frozenset({
    "schema", "schema_version", "repository", "repository_id", "owner", "visibility", "default_branch",
    "project", "publication_origin", "app_login", "publication_workflow", "publication_artifact",
    "programme_branch_prefix", "product_roots", "validation_adapters", "mission_binding",
})
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
_PROJECT = re.compile(r"[a-z][a-z0-9-]{0,63}")
_APP_LOGIN = re.compile(r"[a-zA-Z0-9-]+\[bot\]")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")


class ProfileRefused(ValueError):
    pass


@dataclass(frozen=True)
class ProjectProfile:
    repository: str
    repository_id: int
    owner: str
    visibility: str
    default_branch: str
    project: str
    publication_origin: str
    app_login: str
    publication_workflow: str
    publication_artifact: str
    programme_branch_prefix: str
    product_roots: tuple[str, ...]
    validation_adapters: tuple[str, ...]
    mission_binding: Mapping[str, str]

    @property
    def publication_workflow_path(self) -> str:
        return ".github/workflows/" + self.publication_workflow

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "repository": self.repository,
                "repository_id": self.repository_id, "owner": self.owner, "visibility": self.visibility,
                "default_branch": self.default_branch, "project": self.project,
                "publication_origin": self.publication_origin, "app_login": self.app_login,
                "publication_workflow": self.publication_workflow, "publication_artifact": self.publication_artifact,
                "programme_branch_prefix": self.programme_branch_prefix,
                "product_roots": list(self.product_roots), "validation_adapters": list(self.validation_adapters),
                "mission_binding": dict(self.mission_binding)}


def _text(value: Any, name: str, pattern: re.Pattern[str] | None = None, limit: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\n" in value:
        raise ProfileRefused(f"profile {name} must be bounded single-line text")
    if pattern is not None and not pattern.fullmatch(value):
        raise ProfileRefused(f"profile {name} has an invalid shape")
    return value


def _relative_paths(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > 20 or len(set(value)) != len(value):
        raise ProfileRefused(f"profile {name} must be a nonempty list of distinct paths")
    for item in value:
        _text(item, name, limit=200)
        if item.startswith("/") or ".." in item.split("/") or "\\" in item or ":" in item:
            raise ProfileRefused(f"profile {name} entries must be repository-relative")
    return tuple(value)


def load_profile(source: str | Path | Mapping[str, Any]) -> ProjectProfile:
    """Strictly load a profile from a protected file or an already-read mapping.

    Exact key set, closed enumerations, bounded text and an integer repository identity that
    is not a boolean. Unknown schema versions refuse: a newer profile is not silently read
    as an older one.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_symlink():
            raise ProfileRefused("profile path must not be a symlink")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProfileRefused(f"profile unreadable: {exc}") from exc
        if len(raw) > 20000:
            raise ProfileRefused("profile exceeds its size bound")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ProfileRefused(f"duplicate profile key: {key}")
                result[key] = value
            return result

        try:
            value = json.loads(raw, object_pairs_hook=pairs)
        except json.JSONDecodeError as exc:
            raise ProfileRefused("profile is not valid JSON") from exc
    else:
        value = dict(source)
    if not isinstance(value, dict) or set(value) != FIELDS:
        missing = sorted(FIELDS - set(value)) if isinstance(value, dict) else sorted(FIELDS)
        extra = sorted(set(value) - FIELDS) if isinstance(value, dict) else []
        raise ProfileRefused(f"profile must carry exactly the declared fields; missing={missing} extra={extra}")
    if value["schema"] != SCHEMA or value["schema_version"] != SCHEMA_VERSION:
        raise ProfileRefused("unsupported profile schema or version")
    repository_id = value["repository_id"]
    if type(repository_id) is not int or repository_id <= 0:
        raise ProfileRefused("profile repository_id must be a positive integer")
    if value["visibility"] not in {"public", "private"}:
        raise ProfileRefused("profile visibility must be public or private")
    binding = value["mission_binding"]
    if (not isinstance(binding, dict) or set(binding) != {"mission", "product_requirements"}
            or any(not isinstance(item, str) for item in binding.values())):
        raise ProfileRefused("profile mission_binding must name mission and product_requirements")
    for key, item in binding.items():
        _relative_paths([item], f"mission_binding.{key}")
    origin = _text(value["publication_origin"], "publication_origin", limit=200)
    if not origin.startswith("https://") or origin.endswith("/"):
        raise ProfileRefused("profile publication_origin must be an https origin without a trailing slash")
    repository = _text(value["repository"], "repository", _REPOSITORY)
    owner = _text(value["owner"], "owner", limit=39)
    if repository.split("/")[0] != owner:
        raise ProfileRefused("profile owner must be the repository owner")
    return ProjectProfile(
        repository=repository, repository_id=repository_id, owner=owner, visibility=value["visibility"],
        default_branch=_text(value["default_branch"], "default_branch", _BRANCH),
        project=_text(value["project"], "project", _PROJECT),
        publication_origin=origin, app_login=_text(value["app_login"], "app_login", _APP_LOGIN),
        publication_workflow=_text(value["publication_workflow"], "publication_workflow", re.compile(r"[a-z0-9-]+\.yml")),
        publication_artifact=_text(value["publication_artifact"], "publication_artifact", re.compile(r"[a-z0-9-]+")),
        programme_branch_prefix=_text(value["programme_branch_prefix"], "programme_branch_prefix", re.compile(r"[a-z0-9/-]+")),
        product_roots=_relative_paths(value["product_roots"], "product_roots"),
        validation_adapters=_relative_paths(value["validation_adapters"], "validation_adapters"),
        mission_binding=dict(binding),
    )


def verify_destination(profile: ProjectProfile, platform_observation: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse unless the platform's own record of the repository matches the profile exactly:
    numeric identity, full name, owner, visibility and default branch. The observation is a
    trusted platform read (`gh api repos/<name>`), never a request field."""
    if not isinstance(platform_observation, Mapping):
        raise ProfileRefused("platform observation must be a mapping")
    identity = platform_observation.get("id")
    if type(identity) is not int or identity != profile.repository_id:
        raise ProfileRefused("platform repository identity differs from the profile")
    if platform_observation.get("full_name") != profile.repository:
        raise ProfileRefused("platform repository name differs from the profile")
    owner = platform_observation.get("owner")
    login = owner.get("login") if isinstance(owner, Mapping) else owner
    if login != profile.owner:
        raise ProfileRefused("platform repository owner differs from the profile")
    private = platform_observation.get("private")
    if type(private) is not bool or (("private" if private else "public") != profile.visibility):
        raise ProfileRefused("platform repository visibility differs from the profile")
    if platform_observation.get("default_branch") != profile.default_branch:
        raise ProfileRefused("platform default branch differs from the profile")
    return {"repository": profile.repository, "repository_id": profile.repository_id,
            "visibility": profile.visibility, "default_branch": profile.default_branch, "verified": True}


_CURRENT: ProjectProfile | None = None


def current_profile(path: str | Path | None = None) -> ProjectProfile:
    """The configured profile of this checkout, loaded once from the protected file."""
    global _CURRENT
    if path is not None:
        return load_profile(path)
    if _CURRENT is None:
        _CURRENT = load_profile(DEFAULT_PROFILE_PATH)
    return _CURRENT
