"""Canonical provenance spine for one Dark Factory run.

A model may propose a claim; this manifest records only explicit artifacts and the authority
that certified them. The manifest is intentionally orchestration-agnostic so GitHub Actions,
Archon, or another runner can produce the same evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
import re
from typing import Mapping

from .canonical import canonical_bytes, sha256_value

SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


@dataclass(frozen=True)
class ArtifactRef:
    name: str
    path: str
    sha256: str
    media_type: str = "application/json"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("artifact name must be non-empty")
        p = PurePosixPath(self.path)
        if not self.path or p.is_absolute() or ".." in p.parts:
            raise ValueError(f"artifact path must be safe and relative: {self.path!r}")
        if not SHA256.fullmatch(self.sha256):
            raise ValueError(f"artifact sha256 is invalid: {self.sha256!r}")
        if not self.media_type.strip():
            raise ValueError("artifact media_type must be non-empty")


@dataclass(frozen=True)
class ClaimRecord:
    """One material engineering claim and the authority chain behind it."""

    claim_id: str
    stage: str
    producer: str
    artifact: ArtifactRef
    validator: str | None = None
    validation_artifact: ArtifactRef | None = None
    exact_head_sha: str | None = None
    bindings: Mapping[str, str] = field(default_factory=dict)
    independent_required: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("claim_id", self.claim_id),
            ("stage", self.stage),
            ("producer", self.producer),
        ):
            if not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.validator is not None and not self.validator.strip():
            raise ValueError("validator must be non-empty when supplied")
        if self.validator is not None and self.validator == self.producer:
            raise ValueError("producer cannot certify its own material claim")
        if self.independent_required and self.validator is None:
            raise ValueError("independent claim requires a validator")
        if self.validator is None and self.validation_artifact is not None:
            raise ValueError("validation artifact requires a validator")
        if self.validator is not None and self.validation_artifact is None:
            raise ValueError("validator requires a validation artifact")
        if self.exact_head_sha is not None and not GIT_OID.fullmatch(self.exact_head_sha):
            raise ValueError("exact_head_sha must be a full git object id")
        for key, value in self.bindings.items():
            if not key.strip() or not SHA256.fullmatch(value):
                raise ValueError(f"invalid binding {key!r}={value!r}")

    def to_dict(self) -> dict:
        value = asdict(self)
        value["bindings"] = dict(sorted(self.bindings.items()))
        return value


@dataclass
class RunManifest:
    version: str
    run_id: str
    issue: int
    base_sha: str
    claims: list[ClaimRecord] = field(default_factory=list)

    @classmethod
    def create(cls, *, run_id: str, issue: int, base_sha: str) -> "RunManifest":
        return cls(version="1.0", run_id=run_id, issue=issue, base_sha=base_sha)

    def __post_init__(self) -> None:
        if self.version != "1.0":
            raise ValueError("run manifest version must be 1.0")
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        if self.issue <= 0:
            raise ValueError("issue must be a positive integer")
        if not GIT_OID.fullmatch(self.base_sha):
            raise ValueError("base_sha must be a full git object id")
        seen: set[str] = set()
        for claim in self.claims:
            if claim.claim_id in seen:
                raise ValueError(f"duplicate claim id: {claim.claim_id}")
            seen.add(claim.claim_id)

    def add(self, claim: ClaimRecord) -> None:
        if any(existing.claim_id == claim.claim_id for existing in self.claims):
            raise ValueError(f"duplicate claim id: {claim.claim_id}")
        known_hashes = {
            ref.sha256
            for existing in self.claims
            for ref in (existing.artifact, existing.validation_artifact)
            if ref is not None
        }
        unknown = sorted(set(claim.bindings.values()) - known_hashes)
        if unknown:
            raise ValueError(
                "claim bindings must reference earlier manifest artifacts: " + ", ".join(unknown)
            )
        self.claims.append(claim)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "issue": self.issue,
            "base_sha": self.base_sha,
            "claims": [claim.to_dict() for claim in self.claims],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256_value(self.to_dict())
