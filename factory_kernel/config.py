"""Fail-closed configuration for the repo-owned Dark Factory runtime."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
from typing import Mapping


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    binary: str
    model: str
    timeout_seconds: int
    architecture_model: str = ""
    # How many times a stage is re-launched after an explicitly transient provider error
    # (a dropped stream, a 5xx, a rate limit). 0..3; terminal errors are never retried (D-031).
    transient_retries: int = 2


TRANSIENT_RETRIES_MAX = 3


@dataclass(frozen=True)
class RuntimeConfig:
    max_attempts: int
    active_lease_ttl_seconds: int
    legacy_lease_ttl_seconds: int
    work_root: Path


@dataclass(frozen=True)
class ValidationConfig:
    """Only what the kernel itself runs. The full gate, holdout and mutation runners are invoked
    by the evidence and post-merge programs, not by the kernel, and the repair pass count is a
    constitutional constant (exactly one) rather than configuration; keys for them here were
    validated and never read, which is a claim without an enforcer."""

    quick_command: tuple[str, ...]


@dataclass(frozen=True)
class KernelConfig:
    version: str
    repository: str
    default_branch: str
    provider: ProviderConfig
    runtime: RuntimeConfig
    labels: Mapping[str, str]
    prompts: Mapping[str, str]
    validation: ValidationConfig

    def prompt_path(self, role: str, repo_root: Path) -> Path:
        try:
            rel = self.prompts[role]
        except KeyError as exc:
            raise ValueError(f"kernel prompt role is not configured: {role}") from exc
        path = (repo_root / rel).resolve()
        if repo_root.resolve() not in (path, *path.parents) or not path.is_file():
            raise ValueError(f"kernel prompt is missing or unsafe: {rel}")
        return path


def _mapping(raw: object, name: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"kernel {name} must be an object")
    return raw


def _positive_int(raw: object, name: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        raise ValueError(f"kernel {name} must be a positive integer")
    return raw


def _bounded_int(raw: object, name: str, *, low: int, high: int) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < low or raw > high:
        raise ValueError(f"kernel {name} must be an integer between {low} and {high}")
    return raw


def _string(raw: object, name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"kernel {name} must be a non-empty string")
    return raw.strip()


def _command(raw: object, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or any(not isinstance(x, str) or not x for x in raw):
        raise ValueError(f"kernel {name} must be a non-empty argv array")
    return tuple(raw)


def _relative_prompt(raw: object, name: str) -> str:
    value = _string(raw, name)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"kernel {name} must be a safe repo-relative path")
    if not value.startswith(".factory/prompts/"):
        raise ValueError(f"kernel {name} must live under .factory/prompts/")
    return value


def load_config(path: str | Path) -> KernelConfig:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read kernel configuration: {exc}") from exc
    root = _mapping(raw, "configuration")
    if root.get("version") != "1.0":
        raise ValueError("kernel configuration version must be 1.0")

    provider = _mapping(root.get("provider"), "provider")
    runtime = _mapping(root.get("runtime"), "runtime")
    labels = _mapping(root.get("labels"), "labels")
    prompts = _mapping(root.get("prompts"), "prompts")
    validation = _mapping(root.get("validation"), "validation")

    required_labels = {
        "accepted", "rejected", "rate_limited", "in_progress", "needs_review",
        "needs_fix", "needs_human", "stop",
    }
    if set(labels) != required_labels:
        missing = sorted(required_labels - set(labels))
        extra = sorted(set(labels) - required_labels)
        raise ValueError(f"kernel labels must be exact; missing={missing} extra={extra}")
    parsed_labels = {key: _string(value, f"labels.{key}") for key, value in labels.items()}
    if len(set(parsed_labels.values())) != len(parsed_labels):
        raise ValueError("kernel labels must be unique")

    required_prompts = {
        "triage", "plan", "investigate", "contract", "context", "architecture",
        "test_author", "implement", "review-spec", "review-standards", "repair", "conformance",
        "holdout",
    }
    if set(prompts) != required_prompts:
        missing = sorted(required_prompts - set(prompts))
        extra = sorted(set(prompts) - required_prompts)
        raise ValueError(f"kernel prompts must be exact; missing={missing} extra={extra}")
    parsed_prompts = {
        key: _relative_prompt(value, f"prompts.{key}") for key, value in prompts.items()
    }

    configured_work_root = _string(runtime.get("work_root"), "runtime.work_root")
    work_root = Path(os.environ.get("FACTORY_WORKDIR", configured_work_root)).expanduser()
    if not work_root.is_absolute():
        raise ValueError("kernel runtime work root must be absolute")

    return KernelConfig(
        version="1.0",
        repository=_string(root.get("repository"), "repository"),
        default_branch=_string(root.get("default_branch"), "default_branch"),
        provider=ProviderConfig(
            provider_id=_string(provider.get("id"), "provider.id"),
            binary=_string(provider.get("binary"), "provider.binary"),
            model=_string(provider.get("model"), "provider.model"),
            architecture_model=_string(
                provider.get("architecture_model"), "provider.architecture_model"
            ),
            timeout_seconds=_positive_int(provider.get("timeout_seconds"), "provider.timeout_seconds"),
            transient_retries=_bounded_int(
                provider.get("transient_retries"), "provider.transient_retries",
                low=0, high=TRANSIENT_RETRIES_MAX,
            ),
        ),
        runtime=RuntimeConfig(
            max_attempts=_positive_int(runtime.get("max_attempts"), "runtime.max_attempts"),
            active_lease_ttl_seconds=_positive_int(
                runtime.get("active_lease_ttl_seconds"), "runtime.active_lease_ttl_seconds"
            ),
            legacy_lease_ttl_seconds=_positive_int(
                runtime.get("legacy_lease_ttl_seconds"), "runtime.legacy_lease_ttl_seconds"
            ),
            work_root=work_root,
        ),
        labels=parsed_labels,
        prompts=parsed_prompts,
        validation=ValidationConfig(
            quick_command=_command(validation.get("quick_command"), "validation.quick_command"),
        ),
    )