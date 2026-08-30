"""Structural authority separation for independently certified evidence-spine claims.

Independence is a structural property, not a label. A claim that the protected spine policy
marks ``independent_required`` may only be satisfied by a certificate that

* is issued by the authority registered for that exact claim;
* certifies the exact subject artifact of that claim;
* restates the exact predecessor hashes the claim is bound to;
* is bound to the exact candidate head/base under judgement;
* and is **not** derived from any builder-produced artifact.

The last rule is the one that matters. Before this module existed the post-code
``architecture-conformance`` artifact -- produced on the builder path -- was reused to fill the
independent slots of the pre-code ``design`` and ``architecture-governor`` claims. That is
circular self-certification: the builder path certified its own design and governance decision
while the manifest reported an independence level nobody had earned.

Relatedness is not independence. A post-code conformance judgement is a *different claim* about
a *different artifact* produced by a *different authority class* than an independent
certification of the pre-code design or the governor's proceed decision, and this module refuses
to let one stand in for the other however it is labelled.

The ``contract`` claim carried a quieter version of the same error. Its independent slot was
filled by the blinded *code* holdout, which is validator-side and so not circular, but which
judges whether the diff satisfies the contract while *presupposing* the contract is right. The
contract claim asks the opposite question -- is this compiled contract a faithful and complete
capture of the issue -- and the two are anti-correlated: a contract that silently drops half the
issue makes the code holdout *more* likely to pass, not less. A dedicated certifier now answers
the contract question, blinded to the implementation so it cannot rationalise from the diff.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .canonical import sha256_value

CERTIFICATE_KIND = "independent-certification"
CERTIFICATE_VERSION = "1.0"
ACCEPTED_VERDICTS = frozenset({"pass", "conform"})
SHA256_LEN = 64


@dataclass(frozen=True)
class IndependentAuthority:
    """The one authority permitted to fill a claim's independent slot.

    ``sees`` is what the authority is actually shown. It is not documentation: the kernel builds
    the authority's input from this field, and ``__post_init__`` refuses any entry whose authority
    is not shown every artifact the claim is bound to. Independence without competence is not
    independence -- an authority that never sees the design cannot certify conformance to it,
    however structurally separate from the builder it is.
    """

    claim_id: str
    authority_id: str
    subject_claim: str
    binds: tuple[str, ...]
    sees: tuple[str, ...]
    externally_supplied: bool
    extra_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unseen = sorted(set(self.binds) - set(self.sees))
        if unseen:
            raise ValueError(
                f"independent authority {self.authority_id!r} cannot certify {self.claim_id!r}: "
                "it is never shown " + ", ".join(unseen)
            )
        if self.subject_claim not in self.sees:
            raise ValueError(
                f"independent authority {self.authority_id!r} cannot certify {self.claim_id!r}: "
                f"it is never shown the {self.subject_claim} artifact it must judge"
            )


# Protected registry. Changing an entry changes who is allowed to certify a claim, so it lives in
# the factory trust root and is mutation-tested rather than in builder-controlled evidence data.
REGISTRY: tuple[IndependentAuthority, ...] = (
    IndependentAuthority(
        claim_id="contract",
        authority_id="blinded-contract-certifier",
        subject_claim="contract",
        binds=("contract",),
        sees=("contract",),
        externally_supplied=True,
        # The issue is what the contract is judged against; it is not itself a spine claim.
        extra_inputs=("issue",),
    ),
    IndependentAuthority(
        claim_id="design",
        authority_id="blinded-design-certifier",
        subject_claim="design",
        binds=("contract", "context", "architecture-policy", "design"),
        sees=("contract", "context", "architecture-policy", "design"),
        externally_supplied=True,
    ),
    IndependentAuthority(
        claim_id="architecture-governor",
        authority_id="blinded-governor-certifier",
        subject_claim="architecture-governor",
        binds=("contract", "context", "architecture-policy", "design", "architecture-governor"),
        sees=("contract", "context", "architecture-policy", "design", "architecture-governor"),
        externally_supplied=True,
    ),
    IndependentAuthority(
        claim_id="architecture-drift",
        authority_id="architecture-holdout",
        subject_claim="architecture-drift",
        binds=("architecture-policy", "design", "architecture-drift"),
        sees=("architecture-policy", "design", "architecture-drift"),
        externally_supplied=False,
        extra_inputs=("changed_files", "diff"),
    ),
    IndependentAuthority(
        claim_id="architecture-conformance",
        authority_id="architecture-holdout",
        subject_claim="architecture-conformance",
        binds=(
            "architecture-policy", "design", "architecture-governor", "architecture-conformance",
        ),
        sees=(
            "architecture-policy", "design", "architecture-governor", "architecture-conformance",
        ),
        externally_supplied=False,
        extra_inputs=("changed_files", "diff"),
    ),
)

_BY_CLAIM = {entry.claim_id: entry for entry in REGISTRY}


def authority_for(claim_id: str) -> IndependentAuthority:
    """Return the registered independent authority, or fail closed for an unregistered claim."""
    try:
        return _BY_CLAIM[claim_id]
    except KeyError as exc:
        raise ValueError(
            f"no independent authority is registered for claim {claim_id!r}; "
            "policy requires independence that no authority can supply"
        ) from exc


def authority_inputs(claim_ids: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The exact claim artifacts and extra inputs one authority must be shown for these claims.

    One authority may certify several claims. Its input is the union of what each of those claims
    requires, so a shared authority is competent for every claim it serves, not just the first.
    """
    seen: list[str] = []
    extra: list[str] = []
    for claim_id in claim_ids:
        spec = authority_for(claim_id)
        seen.extend(name for name in spec.sees if name not in seen)
        extra.extend(name for name in spec.extra_inputs if name not in extra)
    return tuple(seen), tuple(extra)


def claims_for_authority(authority_id: str) -> tuple[str, ...]:
    return tuple(entry.claim_id for entry in REGISTRY if entry.authority_id == authority_id)


def externally_supplied_claims() -> frozenset[str]:
    """Claims whose certificate must be produced and persisted outside evidence closure."""
    return frozenset(entry.claim_id for entry in REGISTRY if entry.externally_supplied)


def build_certificate(
    *,
    claim_id: str,
    claim_hashes: Mapping[str, str],
    head_sha: str,
    base_sha: str,
    judgement: Mapping[str, Any],
) -> dict:
    """Build the canonical certificate envelope around a non-authoritative judgement.

    The issuing authority supplies only the verdict and its findings. Every binding in the
    envelope is filled in by the kernel from hashes it computed itself, so a model cannot state
    what its own judgement is bound to.
    """
    spec = authority_for(claim_id)
    verdict = str(judgement.get("verdict") or "")
    missing = [name for name in (*spec.binds, spec.subject_claim) if name not in claim_hashes]
    if missing:
        raise ValueError(
            f"cannot certify {claim_id}: unknown predecessor hashes: " + ", ".join(sorted(set(missing)))
        )
    return {
        "version": CERTIFICATE_VERSION,
        "kind": CERTIFICATE_KIND,
        "claim_id": claim_id,
        "authority_id": spec.authority_id,
        "subject_sha256": claim_hashes[spec.subject_claim],
        "bindings": {name: claim_hashes[name] for name in spec.binds},
        "head_sha": head_sha,
        "base_sha": base_sha,
        "verdict": verdict,
        "judgement_certifies": judgement.get("certifies"),
        "judgement_sha256": sha256_value(dict(judgement)),
    }


def _hash(value: object, label: str) -> str:
    text = str(value or "")
    if len(text) != SHA256_LEN:
        raise ValueError(f"independent certificate {label} is not a sha256 digest")
    return text


def verify_certificate(
    certificate: Mapping[str, Any],
    *,
    claim_id: str,
    claim_hashes: Mapping[str, str],
    builder_artifact_hashes: Mapping[str, str],
    head_sha: str,
    base_sha: str,
) -> dict:
    """Verify one independent certificate, or raise.

    ``builder_artifact_hashes`` is the exact set of artifacts carried on the builder provenance
    path. Any certificate whose judgement is one of those artifacts is rejected: the builder
    cannot certify its own claim under a different name.
    """
    spec = authority_for(claim_id)
    if not isinstance(certificate, Mapping):
        raise ValueError(f"independent certificate for {claim_id} must be an object")
    if certificate.get("version") != CERTIFICATE_VERSION:
        raise ValueError(f"independent certificate for {claim_id} has an unsupported version")
    if certificate.get("kind") != CERTIFICATE_KIND:
        raise ValueError(
            f"artifact offered for the {claim_id} independent slot is not an independent "
            f"certification (kind={certificate.get('kind')!r})"
        )
    if certificate.get("claim_id") != claim_id:
        raise ValueError(
            f"independent certificate certifies {certificate.get('claim_id')!r}, not {claim_id!r}"
        )
    if certificate.get("authority_id") != spec.authority_id:
        raise ValueError(
            f"{claim_id} independent slot requires authority {spec.authority_id!r}, "
            f"got {certificate.get('authority_id')!r}"
        )

    # Structural origin outranks every softer check. An artifact produced anywhere on the builder
    # path can never fill an independent slot, whatever verdict or bindings it carries, and
    # whatever authority name is stamped on the envelope around it.
    judgement = _hash(certificate.get("judgement_sha256"), "judgement_sha256")
    builder_origin = {digest: name for name, digest in builder_artifact_hashes.items()}
    if judgement in builder_origin:
        raise ValueError(
            f"independent slot for {claim_id} is filled by builder-produced artifact "
            f"{builder_origin[judgement]!r}; a builder claim cannot certify itself"
        )

    # A certificate the kernel did not construct in-process must name its own subject from
    # inside the hashed judgement. `claim_id` is set by whoever built the envelope; this is the
    # authority's own declaration, so a judgement produced for some other purpose -- a code
    # holdout's verdict, say -- cannot be re-wrapped as certification of this claim.
    if spec.externally_supplied and certificate.get("judgement_certifies") != claim_id:
        raise ValueError(
            f"judgement offered for the {claim_id} independent slot does not certify {claim_id}; "
            f"it declares {certificate.get('judgement_certifies')!r}"
        )

    subject = _hash(certificate.get("subject_sha256"), "subject_sha256")
    expected_subject = claim_hashes.get(spec.subject_claim)
    if expected_subject is None or subject != expected_subject:
        raise ValueError(
            f"independent certificate for {claim_id} does not certify the {spec.subject_claim} "
            "artifact in this run"
        )
    if subject == judgement:
        raise ValueError(f"independent certificate for {claim_id} certifies its own judgement")

    bindings = certificate.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ValueError(f"independent certificate for {claim_id} has no bindings")
    for name in spec.binds:
        expected = claim_hashes.get(name)
        if expected is None or str(bindings.get(name) or "") != expected:
            raise ValueError(
                f"independent certificate for {claim_id} is not bound to this run's {name}"
            )

    if str(certificate.get("head_sha") or "") != head_sha:
        raise ValueError(
            f"independent certificate for {claim_id} was issued against a different head SHA"
        )
    if str(certificate.get("base_sha") or "") != base_sha:
        raise ValueError(
            f"independent certificate for {claim_id} was issued against a different base SHA"
        )
    if certificate.get("verdict") not in ACCEPTED_VERDICTS:
        raise ValueError(f"independent authority for {claim_id} did not pass")

    return {
        "authority_id": spec.authority_id,
        "subject_sha256": subject,
        "judgement_sha256": judgement,
        "bindings": {name: str(bindings[name]) for name in spec.binds},
        "head_sha": head_sha,
        "base_sha": base_sha,
        "verdict": str(certificate["verdict"]),
    }
