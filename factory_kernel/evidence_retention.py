"""Bounded observation-only retention of existing evidence, never a proof authority.

The raw package is short-lived Actions data. Its separate index contains only fixed paths,
hashes and source identities and can enter the durable trajectory archive. Neither is an
issuer attestation, and checkout observations are not identities of every executed tool.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import re
import subprocess

from .canonical import canonical_bytes, sha256_bytes
from .carry import CARRY_POLICY_PATHS, policy_digest
from .evidence_closure import PRODUCERS
from .programme import parse_json
from .provenance import BUILDER_CLAIMS
from .trajectory import MAX_RECORD, TrajectoryRefused, oid, positive

MAX_FILE = 250000
MAX_BYTES = 8000000
MAX_FILES = 256
KERNEL_RUN = r"(?:issue-[0-9]+-a[0-9]+-[0-9a-f]{10}|(?:pr|merge|rehead|resume)-[0-9]+-[0-9a-f]{12})"
EVIDENCE_PATHS = frozenset({
    "evidence-bundle.json", "evidence-bundle-core-v5.json", "spine/run-manifest.json",
    "spine/evidence-index.json", "spine/builder-provenance.json", "spine/validator/immunity-verification.json",
    "spine/attestations/index.json",
    "holdout.json", "architecture-holdout.json", "validation-refusal.json", "factory-feedback.json",
    *(f"spine/{'builder' if claim in BUILDER_CLAIMS else 'validator'}/{claim}.json" for claim in PRODUCERS),
    *(f"spine/certifications/{claim}-{kind}.json" for claim in PRODUCERS
      for kind in ("deterministic", "independent")),
    *(f"independent/{claim}.json" for claim in ("contract", "design", "architecture-governor")),
})


def _path(root, relative):
    path = Path(root)
    # Reject symlinked ancestors, including a supplied root, before any read/write.
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise TrajectoryRefused("unsafe evidence root")
    for part in relative.split("/"):
        if not part or part in {".", ".."} or "\\" in part or ":" in part:
            raise TrajectoryRefused("unsafe evidence path")
        path = path / part
        if path.is_symlink():
            raise TrajectoryRefused("unsafe evidence path")
    return path


def _json_bytes(path, limit=MAX_FILE):
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise TrajectoryRefused("evidence file is absent or over bound")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit or not isinstance(parse_json(raw.decode("utf-8")), dict):
        raise TrajectoryRefused("invalid evidence JSON")
    return raw


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise TrajectoryRefused("invalid evidence identity")
    return value


def source_binding(*, repository, run_id, attempt, source_revision, phase):
    if repository != "ShaishiBear/dark-factory-2.0" or phase not in {"dispatch", "merge"}:
        raise TrajectoryRefused("unknown evidence source")
    return {"repository": repository, "run_id": positive(run_id), "run_attempt": positive(attempt),
            "source_revision": oid(source_revision), "phase": phase}


def observe_checkout(checkout):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(checkout), *args], text=True, timeout=10)
    revision = oid(git("rev-parse", "HEAD").strip())
    return {"checkout_revision": revision,
            "trust_root_tree_sha256": policy_digest(git("ls-tree", "-r", revision, "--", *CARRY_POLICY_PATHS)),
            "python_version": platform.python_version()}


def stage(*, runs, destination, index_directory, binding, checkout_observation):
    """Copy only protected names as inert bytes. Never reconstruct missing proof records."""
    binding = source_binding(repository=binding["repository"], run_id=binding["run_id"],
                             attempt=binding["run_attempt"], source_revision=binding["source_revision"],
                             phase=binding["phase"])
    runs, destination, index_directory = map(Path, (runs, destination, index_directory))
    for root in (runs, destination, index_directory):
        _path(root, "root-check")
    if (destination.exists() or index_directory.exists()
            or destination.resolve().is_relative_to(runs.resolve())
            or index_directory.resolve().is_relative_to(runs.resolve())
            or destination.resolve() == index_directory.resolve()):
        raise TrajectoryRefused("retention requires fresh separate output directories")
    attempts = sorted(runs.iterdir()) if runs.exists() else []
    if len(attempts) > 20:
        raise TrajectoryRefused("too many evidence attempts")
    files, gaps, total = [], [], 0
    destination.mkdir(parents=True)
    for attempt_dir in attempts:
        if not re.fullmatch(KERNEL_RUN, attempt_dir.name) or not attempt_dir.is_dir() or attempt_dir.is_symlink():
            gaps.append({"reason": "unrecognized-kernel-attempt"})
            continue
        counts = {"absent": 0, "invalid": 0, "over-bound": 0}
        for relative in sorted(EVIDENCE_PATHS):
            name = f"{attempt_dir.name}/artifacts/{relative}"
            try:
                source = _path(runs, name)
                if not source.exists():
                    counts["absent"] += 1
                    continue
                raw = _json_bytes(source)
            except (ValueError, OSError, UnicodeError):
                counts["invalid"] += 1
                continue
            if total + len(raw) > MAX_BYTES or len(files) >= MAX_FILES:
                counts["over-bound"] += 1
                continue
            target = _path(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            files.append({"path": name, "sha256": sha256_bytes(raw), "bytes": len(raw)})
            total += len(raw)
        gaps.append({"kernel_run": attempt_dir.name, "counts": counts})
    index = {"schema": "dark-factory/evidence-retention", "schema_version": "1.0",
             "authority": "observation-only", "proof_reuse_allowed": False, "source": binding,
             "checkout_observation": {key: checkout_observation[key] for key in
                                      ("checkout_revision", "trust_root_tree_sha256", "python_version")},
             "files": files, "gaps": gaps,
             "retained_bytes": total}
    # Validate our own public-metadata projection before publishing either artifact.
    raw = canonical_bytes(index)
    safe_index(raw, binding=binding)
    index_directory.mkdir(parents=True)
    (index_directory / "retention-index.json").write_bytes(raw)
    return index


def safe_index(raw, *, binding):
    """Strictly project untrusted index data; no free text enters the durable archive."""
    if len(raw) > MAX_RECORD:
        raise TrajectoryRefused("retention index exceeds bound")
    value = parse_json(raw.decode("utf-8"))
    source = value["source"]
    source_binding(repository=source["repository"], run_id=source["run_id"], attempt=source["run_attempt"],
                   source_revision=source["source_revision"], phase=source["phase"])
    if (value.get("schema") != "dark-factory/evidence-retention" or value.get("schema_version") != "1.0"
            or value.get("authority") != "observation-only" or value.get("proof_reuse_allowed") is not False
            or value.get("source") != binding):
        raise TrajectoryRefused("retention index source mismatch")
    observation = value["checkout_observation"]
    python = observation["python_version"]
    if not isinstance(python, str) or not re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,3}", python):
        raise TrajectoryRefused("invalid interpreter observation")
    checkout = {"checkout_revision": oid(observation["checkout_revision"]),
                "trust_root_tree_sha256": _sha(observation["trust_root_tree_sha256"]), "python_version": python}
    rows = value["files"]
    if not isinstance(rows, list) or len(rows) > MAX_FILES:
        raise TrajectoryRefused("retention file inventory exceeds bound")
    files, seen, total = [], set(), 0
    for row in rows:
        name = row["path"]
        if not isinstance(name, str):
            raise TrajectoryRefused("invalid retained path")
        parts = name.split("/artifacts/")
        if (len(parts) != 2 or not re.fullmatch(KERNEL_RUN, parts[0])
                or parts[1] not in EVIDENCE_PATHS or name in seen):
            raise TrajectoryRefused("unknown or duplicate retained path")
        size = positive(row["bytes"])
        if size > MAX_FILE:
            raise TrajectoryRefused("retained file over bound")
        seen.add(name)
        total += size
        files.append({"path": name, "sha256": _sha(row["sha256"]), "bytes": size})
    if total > MAX_BYTES or type(value["retained_bytes"]) is not int or value["retained_bytes"] != total:
        raise TrajectoryRefused("retained byte count mismatch")
    gaps = value["gaps"]
    if not isinstance(gaps, list) or len(gaps) > 20:
        raise TrajectoryRefused("retention gap inventory exceeds bound")
    safe_gaps = []
    for gap in gaps:
        if gap == {"reason": "unrecognized-kernel-attempt"}:
            safe_gaps.append(gap)
            continue
        kernel_run = gap["kernel_run"]
        if not isinstance(kernel_run, str) or not re.fullmatch(KERNEL_RUN, kernel_run):
            raise TrajectoryRefused("unknown retained attempt")
        counts = {key: gap["counts"][key] for key in ("absent", "invalid", "over-bound")}
        if any(type(count) is not int or not 0 <= count <= len(EVIDENCE_PATHS) for count in counts.values()):
            raise TrajectoryRefused("invalid retention gap count")
        safe_gaps.append({"kernel_run": kernel_run, "counts": counts})
    return {"authority": "observation-only", "proof_reuse_allowed": False, "source": binding,
            "checkout_observation": checkout, "files": files, "gaps": safe_gaps, "retained_bytes": total,
            "file_identity_source": "recorded-index", "index_sha256": sha256_bytes(raw)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runs", "destination", "index-directory", "checkout"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--phase", choices=("dispatch", "merge"), required=True)
    args = parser.parse_args()
    binding = source_binding(repository=os.environ["GITHUB_REPOSITORY"], run_id=int(os.environ["GITHUB_RUN_ID"]),
                             attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]),
                             source_revision=os.environ["GITHUB_SHA"], phase=args.phase)
    stage(runs=args.runs, destination=args.destination, index_directory=args.index_directory,
          binding=binding, checkout_observation=observe_checkout(args.checkout))


if __name__ == "__main__":
    main()
