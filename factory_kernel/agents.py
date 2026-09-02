"""Provider-neutral agent execution contracts.

The factory kernel owns this interface. Provider SDKs are adapters and must not leak into
provenance, validation, or merge authority.

Design influenced by Archon's MIT-licensed provider registry, but intentionally smaller and
Python-native for Dark Factory 2.0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True)
class ProviderCapabilities:
    structured_output: bool = False
    session_resume: bool = False
    session_fork: bool = False
    tool_restrictions: bool = False
    web_search: bool = False

    def validate(self) -> None:
        if self.session_fork and not self.session_resume:
            raise ValueError("session_fork requires session_resume")


@dataclass(frozen=True)
class AgentRequest:
    role: str
    prompt: str
    cwd: str
    model: str | None = None
    effort: str | None = None
    structured_schema: Mapping[str, Any] | None = None
    allowed_tools: tuple[str, ...] | None = None
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("agent role must be non-empty")
        if not self.prompt.strip():
            raise ValueError("agent prompt must be non-empty")
        if not self.cwd.strip():
            raise ValueError("agent cwd must be non-empty")


@dataclass(frozen=True)
class AgentResult:
    provider_id: str
    model: str
    content: str
    structured_output: Any | None = None
    session_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class AgentProvider(Protocol):
    """Untrusted reasoning adapter.

    Implementations may call Claude, Codex, Gemini, Archon, or another agent. Returning a
    result never certifies a claim: deterministic/independent authorities do that later.
    """

    provider_id: str
    capabilities: ProviderCapabilities

    def run(self, request: AgentRequest) -> AgentResult: ...


@dataclass(frozen=True)
class ProviderRegistration:
    provider_id: str
    display_name: str
    factory: Callable[[], AgentProvider]
    capabilities: ProviderCapabilities


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderRegistration] = {}

    def register(self, registration: ProviderRegistration) -> None:
        provider_id = registration.provider_id.strip()
        if not provider_id:
            raise ValueError("provider id must be non-empty")
        if provider_id in self._providers:
            raise ValueError(f"provider {provider_id!r} is already registered")
        registration.capabilities.validate()
        self._providers[provider_id] = registration

    def is_registered(self, provider_id: str) -> bool:
        return provider_id in self._providers

    def capabilities(self, provider_id: str) -> ProviderCapabilities:
        return self.registration(provider_id).capabilities

    def registration(self, provider_id: str) -> ProviderRegistration:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._providers)) or "<none>"
            raise KeyError(f"unknown provider {provider_id!r}; registered: {known}") from exc

    def create(self, provider_id: str) -> AgentProvider:
        registration = self.registration(provider_id)
        provider = registration.factory()
        if provider.provider_id != provider_id:
            raise ValueError(
                f"provider factory identity mismatch: registered={provider_id!r} "
                f"actual={provider.provider_id!r}"
            )
        provider.capabilities.validate()
        return provider

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))
