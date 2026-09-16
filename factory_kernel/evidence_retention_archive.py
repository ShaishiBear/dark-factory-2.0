"""Project fixed retention metadata into trajectories without downloading raw proof contents."""
from pathlib import Path
import re

from .evidence_retention import MAX_BYTES, _json_bytes, _path, safe_index, source_binding
from .trajectory import MAX_RECORD, TrajectoryRefused, positive, timestamp


def artifact_observation(row, *, source, name, limit):
    run = row["workflow_run"]
    repository_id = positive(source["repository"]["id"])
    if (row["name"] != name or run["id"] != source["id"]
            or run["head_sha"] != source["head_sha"] or run["head_branch"] != "main"
            or run["repository_id"] != repository_id or run["head_repository_id"] != repository_id
            or type(row["expired"]) is not bool):
        raise TrajectoryRefused("retained artifact source mismatch")
    size = positive(row["size_in_bytes"])
    digest = row["digest"]
    if size > limit or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise TrajectoryRefused("invalid retained artifact identity")
    return {"id": positive(row["id"]), "name": name, "digest": digest, "bytes": size,
            "created_at": timestamp(row["created_at"]), "expires_at": timestamp(row["expires_at"]),
            "expired_at_observation": row["expired"]}


def collect_retention(github, *, source, inventory, directory):
    run_id, attempt = source["id"], source["run_attempt"]
    # Preserve byte-for-byte legacy records and their explicit historical gaps.
    expected_names = {f"dark-factory-{kind}-{phase}-{run_id}-{attempt}"
                      for kind in ("evidence", "evidence-index") for phase in ("dispatch", "merge")}
    if not any(row.get("name") in expected_names for row in inventory):
        return None
    phases = []
    for phase in ("dispatch", "merge"):
        result = {"phase": phase, "authority": "observation-only", "proof_reuse_allowed": False,
                  "artifacts": {}, "gaps": []}
        binding = source_binding(repository=github.repository, run_id=run_id, attempt=attempt,
                                 source_revision=source["head_sha"], phase=phase)
        for kind in ("evidence", "evidence-index"):
            name = f"dark-factory-{kind}-{phase}-{run_id}-{attempt}"
            matches = [row for row in inventory if row.get("name") == name]
            if not matches:
                result["gaps"].append({"artifact": kind, "reason": "artifact-unavailable"})
                continue
            try:
                if len(matches) != 1:
                    raise TrajectoryRefused("ambiguous retained artifact")
                observed = artifact_observation(matches[0], source=source, name=name,
                                                limit=MAX_RECORD + 10000 if kind == "evidence-index" else MAX_BYTES + 100000)
                result["artifacts"][kind] = observed
                if observed["expired_at_observation"]:
                    result["gaps"].append({"artifact": kind, "reason": "artifact-expired"})
                    continue
                if kind == "evidence-index":
                    target = Path(directory) / f"retention-{phase}"
                    target.mkdir()
                    github.run(["run", "download", str(run_id), "-R", github.repository,
                                "-n", name, "-D", str(target)])
                    # v4 artifact names are immutable until deletion. Refuse an overwrite race.
                    fresh = github.json(["api", f"repos/{github.repository}/actions/artifacts/{observed['id']}"])
                    if artifact_observation(fresh, source=source, name=name, limit=MAX_RECORD + 10000) != observed:
                        raise TrajectoryRefused("retention index changed during observation")
                    raw = _json_bytes(_path(target, "retention-index.json"), MAX_RECORD)
                    result["index"] = safe_index(raw, binding=binding)
            except (ValueError, OSError, RuntimeError, UnicodeError, KeyError, TypeError, AttributeError):
                result["gaps"].append({"artifact": kind, "reason": "artifact-metadata-refused"})
        phases.append(result)
    return phases
