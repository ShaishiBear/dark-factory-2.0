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
    max_turns: int | None = None
    max_budget_usd: float | None = None
    # The wall clock one CLI process for this request may run, in seconds: the role's own wall
    # (`worker_policy.stage_timeout_seconds`), never above the provider's configured maximum.
    # Absent, the provider falls back to that maximum (D-054).
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("agent role must be non-empty")
        if not self.prompt.strip():
            raise ValueError("agent prompt must be non-empty")
        if not self.cwd.strip():
            raise ValueError("agent cwd must be non-empty")
        if self.max_turns is not None and (
            isinstance(self.max_turns, bool) or not isinstance(self.max_turns, int) or self.max_turns <= 0
        ):
            raise ValueError("agent max_turns must be a positive integer when set")
        if self.max_budget_usd is not None and (
            isinstance(self.max_budget_usd, bool)
            or not isinstance(self.max_budget_usd, (int, float))
            or self.max_budget_usd <= 0
        ):
            raise ValueError("agent max_budget_usd must be a positive number when set")
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("agent timeout_seconds must be a positive integer when set")


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
    num_turns: int | None = None
    duration_ms: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    # How many CLI processes this stage took (1 unless a transient provider error was retried),
    # and the error text of each attempt that was retried. Token/turn/cost fields above are the
    # sum across attempts.
    attempts: int = 1
    transient_errors: tuple[str, ...] = ()
    # How many stream events the provider read from the CLI across every attempt (the
    # progress the stage showed while it ran); `None` for a provider that does not stream.
    events_seen: int | None = None


class AgentProvider(Protocol):
    """Untrusted reasoning adapter.

    Implementations may call Claude, Codex, Gemini, Archon, or another agent. Returning a
    result never certifies a claim: deterministic/independent authorities do that later.
    """

    provider_id: str
    capabilities: ProviderCapabilities

    def run(
        self,
        request: AgentRequest,
        *,
        before_retry: Callable[[int], None] | None = None,
    ) -> AgentResult:
        """`before_retry(attempt)` is called by a provider that re-launches a stage after a
        transient provider error, before the new process starts; the kernel uses it to restore
        the worktree. A provider that never retries may ignore it."""
        ...


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
