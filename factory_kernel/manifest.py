"""Canonical provenance spine for one Dark Factory run.

A model may propose a claim; this manifest records explicit artifacts and the authorities that
certified them. Requirements such as "independent verification is mandatory" live in the
protected spine policy, never in builder-controlled claim data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path, PurePosixPath
import re
from typing import Mapping

from .canonical import canonical_bytes, sha256_value

SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
CERT_KINDS = {"deterministic", "independent"}


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

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ArtifactRef":
        return cls(
            name=str(raw.get("name") or ""),
            path=str(raw.get("path") or ""),
            sha256=str(raw.get("sha256") or ""),
            media_type=str(raw.get("media_type") or "application/json"),
        )


@dataclass(frozen=True)
class Certification:
    kind: str
    authority_id: str
    artifact: ArtifactRef

    def __post_init__(self) -> None:
        if self.kind not in CERT_KINDS:
            raise ValueError(f"unknown certification kind: {self.kind!r}")
        if not self.authority_id.strip():
            raise ValueError("certification authority_id must be non-empty")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "Certification":
        artifact = raw.get("artifact")
        if not isinstance(artifact, Mapping):
            raise ValueError("certification artifact must be an object")
        return cls(
            kind=str(raw.get("kind") or ""),
            authority_id=str(raw.get("authority_id") or ""),
            artifact=ArtifactRef.from_dict(artifact),
        )


@dataclass(frozen=True)
class ClaimRecord:
    """One material engineering claim and its authority chain."""

    claim_id: str
    stage: str
    producer: str
    artifact: ArtifactRef
    deterministic: Certification | None = None
    independent: Certification | None = None
    exact_head_sha: str | None = None
    bindings: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("claim_id", self.claim_id),
            ("stage", self.stage),
            ("producer", self.producer),
        ):
            if not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.deterministic is not None:
            if self.deterministic.kind != "deterministic":
                raise ValueError("deterministic slot requires deterministic certification")
            if self.deterministic.authority_id == self.producer:
                raise ValueError("producer cannot deterministically certify its own material claim")
        if self.independent is not None:
            if self.independent.kind != "independent":
                raise ValueError("independent slot requires independent certification")
            if self.independent.authority_id == self.producer:
                raise ValueError("producer cannot independently certify its own material claim")
            if (
                self.deterministic is not None
                and self.independent.authority_id == self.deterministic.authority_id
            ):
                raise ValueError("independent verifier must differ from deterministic validator")
        if self.exact_head_sha is not None and not GIT_OID.fullmatch(self.exact_head_sha):
            raise ValueError("exact_head_sha must be a full git object id")
        for key, value in self.bindings.items():
            if not key.strip() or not SHA256.fullmatch(value):
                raise ValueError(f"invalid binding {key!r}={value!r}")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ClaimRecord":
        artifact_raw = raw.get("artifact")
        if not isinstance(artifact_raw, Mapping):
            raise ValueError("claim artifact must be an object")
        deterministic_raw = raw.get("deterministic")
        independent_raw = raw.get("independent")
        if deterministic_raw is not None and not isinstance(deterministic_raw, Mapping):
            raise ValueError("claim deterministic certification must be an object or null")
        if independent_raw is not None and not isinstance(independent_raw, Mapping):
            raise ValueError("claim independent certification must be an object or null")
        bindings_raw = raw.get("bindings", {})
        if not isinstance(bindings_raw, Mapping):
            raise ValueError("claim bindings must be an object")
        return cls(
            claim_id=str(raw.get("claim_id") or ""),
            stage=str(raw.get("stage") or ""),
            producer=str(raw.get("producer") or ""),
            artifact=ArtifactRef.from_dict(artifact_raw),
            deterministic=(
                Certification.from_dict(deterministic_raw)
                if deterministic_raw is not None
                else None
            ),
            independent=(
                Certification.from_dict(independent_raw)
                if independent_raw is not None
                else None
            ),
            exact_head_sha=(
                str(raw["exact_head_sha"]) if raw.get("exact_head_sha") is not None else None
            ),
            bindings={str(key): str(value) for key, value in bindings_raw.items()},
        )

    def to_dict(self) -> dict:
        value = asdict(self)
        value["bindings"] = dict(sorted(self.bindings.items()))
        return value

    def referenced_artifacts(self) -> tuple[ArtifactRef, ...]:
        refs = [self.artifact]
        if self.deterministic is not None:
            refs.append(self.deterministic.artifact)
        if self.independent is not None:
            refs.append(self.independent.artifact)
        return tuple(refs)


@dataclass
class RunManifest:
    version: str
    run_id: str
    issue: int
    base_sha: str
    claims: list[ClaimRecord] = field(default_factory=list)

    @classmethod
    def create(cls, *, run_id: str, issue: int, base_sha: str) -> "RunManifest":
        return cls(version="2.0", run_id=run_id, issue=issue, base_sha=base_sha)

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "RunManifest":
        claims_raw = raw.get("claims", [])
        if not isinstance(claims_raw, list):
            raise ValueError("run manifest claims must be a list")
        issue = raw.get("issue")
        if not isinstance(issue, int):
            raise ValueError("run manifest issue must be an integer")
        manifest = cls(
            version=str(raw.get("version") or ""),
            run_id=str(raw.get("run_id") or ""),
            issue=issue,
            base_sha=str(raw.get("base_sha") or ""),
        )
        for claim_raw in claims_raw:
            if not isinstance(claim_raw, Mapping):
                raise ValueError("run manifest claim must be an object")
            manifest.add(ClaimRecord.from_dict(claim_raw))
        return manifest

    @classmethod
    def load(cls, path: str | Path) -> "RunManifest":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read run manifest: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ValueError("run manifest must contain an object")
        return cls.from_dict(raw)

    def __post_init__(self) -> None:
        if self.version != "2.0":
            raise ValueError("run manifest version must be 2.0")
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
            for ref in existing.referenced_artifacts()
        }
        unknown = sorted(set(claim.bindings.values()) - known_hashes)
        if unknown:
            raise ValueError(
                "claim bindings must reference earlier manifest artifacts: " + ", ".join(unknown)
            )
        self.claims.append(claim)

    def claim(self, claim_id: str) -> ClaimRecord | None:
        return next((claim for claim in self.claims if claim.claim_id == claim_id), None)

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

    def write(self, path: str | Path) -> None:
        Path(path).write_bytes(self.canonical_bytes())
