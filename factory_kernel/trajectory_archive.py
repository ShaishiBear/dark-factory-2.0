"""Completed-run observation and append-only App publication to one data-only branch."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .canonical import canonical_bytes, sha256_bytes
from .evidence_retention_archive import collect_retention
from .github_cli import GitHubClient
from .programme import parse_json
from .trajectory import MAX_RECORD, TrajectoryRefused, oid, positive, summarize, validate_source

REPOSITORY = "ShaishiBear/dark-factory-2.0"
ARCHIVE_REF = "refs/heads/factory/trajectories"
WORKFLOW_PATH = ".github/workflows/dark-factory-trajectory.yml"


def authorize_context(environ, event):
    if (environ.get("GITHUB_REPOSITORY") != REPOSITORY or environ.get("GITHUB_REF") != "refs/heads/main"
            or environ.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/main"):
        raise TrajectoryRefused("archive requires its canonical protected-main workflow")
    if environ.get("GITHUB_EVENT_NAME") == "workflow_run":
        source = event["workflow_run"]
        return positive(source["id"]), positive(source["run_attempt"])
    if (environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environ.get("GITHUB_ACTOR") == "ShaishiBear"
            and environ.get("GITHUB_TRIGGERING_ACTOR") == "ShaishiBear"):
        return positive(int(event["inputs"]["run_id"])), positive(int(event["inputs"]["attempt"]))
    raise TrajectoryRefused("archive recovery requires the repository owner")


def collect(github, *, run_id, attempt):
    repository = github.repository
    source = github.json(["api", f"repos/{repository}/actions/runs/{positive(run_id)}/attempts/{positive(attempt)}"])
    validate_source(source, repository=repository, run_id=run_id, attempt=attempt)
    # Read public policy as data at the source revision; never execute a downloaded checkout.
    policy = github.json(["api", f"repos/{repository}/contents/.factory/kernel.json?ref={source['head_sha']}"])
    if policy.get("encoding") != "base64" or not 0 < policy.get("size", 0) <= 100000:
        raise TrajectoryRefused("source policy cannot be bounded")
    config = parse_json(base64.b64decode(policy["content"]).decode("utf-8"))["provider"]
    models = {config["model"], config["architecture_model"], *config.get("model_overrides", {}).values()}
    response = github.json(["api", f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100"])
    if type(response.get("total_count")) is not int or response["total_count"] > 100:
        raise TrajectoryRefused("artifact inventory exceeds the archive bound")
    gaps, directories = [], {}
    with tempfile.TemporaryDirectory(prefix="factory-trajectory-") as directory:
        for phase, prefix in (("dispatch", "dark-factory-run"), ("merge", "dark-factory-merge")):
            name = f"{prefix}-{run_id}-{attempt}"
            matches = [row for row in response["artifacts"] if row.get("name") == name]
            if not matches:
                gaps.append({"phase": phase, "reason": "artifact-unavailable"})
                continue
            if len(matches) != 1:
                raise TrajectoryRefused("ambiguous source artifact")
            row = matches[0]
            if row.get("expired") is not False:
                gaps.append({"phase": phase, "reason": "artifact-expired"})
                continue
            if type(row.get("size_in_bytes")) is not int or not 0 < row["size_in_bytes"] <= 5000000:
                gaps.append({"phase": phase, "reason": "artifact-over-bound"})
                continue
            target = Path(directory) / phase
            target.mkdir()
            try:
                github.run(["run", "download", str(run_id), "-R", repository, "-n", name, "-D", str(target)])
                directories[phase] = target
            except (RuntimeError, OSError):
                gaps.append({"phase": phase, "reason": "artifact-download-failed"})
        try:
            record = summarize(source, repository=repository, directories=directories, models=models, gaps=gaps)
        except (ValueError, OSError, UnicodeError):
            # Malformed/oversized diagnostic files must not erase the platform-observed failed
            # attempt. Retain a bounded source-only observation without reading outside scope.
            record = summarize(source, repository=repository, directories={}, models=models,
                               gaps=[*gaps, {"reason": "artifact-metadata-refused"}])
        retention = collect_retention(github, source=source, inventory=response["artifacts"], directory=directory)
        if retention is not None:
            record["evidence_retention"] = retention
            if len(canonical_bytes(record)) > MAX_RECORD:
                # Keep the completed attempt even when combined observations exceed the bound.
                del record["evidence_retention"]
                record["gaps"].append({"reason": "retention-metadata-over-bound"})
                if len(canonical_bytes(record)) > MAX_RECORD:
                    record = summarize(source, repository=repository, directories={}, models=models,
                                       gaps=[{"reason": "retention-metadata-over-bound"}])
        return record


class TrajectoryArchive:
    """Append one immutable observation; no main writes, force updates, issues or proof effects."""

    def __init__(self, github):
        self.github = github

    def _inventory(self):
        repo = self.github.repository
        refs = self.github.json(["api", f"repos/{repo}/git/matching-refs/heads/factory/trajectories"])
        if not isinstance(refs, list):
            raise TrajectoryRefused("archive ref inventory unavailable")
        matches = [row for row in refs if row.get("ref") == ARCHIVE_REF]
        if not matches:
            return None, None, {}
        if len(matches) != 1 or matches[0].get("object", {}).get("type") != "commit":
            raise TrajectoryRefused("ambiguous archive ref")
        head = oid(matches[0]["object"]["sha"])
        commit = self.github.json(["api", f"repos/{repo}/git/commits/{head}"])
        tree_sha = oid(commit["tree"]["sha"])
        tree = self.github.json(["api", f"repos/{repo}/git/trees/{tree_sha}"])
        if tree.get("truncated") is not False or len(tree["tree"]) > 10000:
            raise TrajectoryRefused("archive tree inventory is incomplete")
        entries = {}
        import re
        for entry in tree["tree"]:
            if (entry.get("type") != "blob" or entry.get("mode") != "100644"
                    or not re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*\.json", entry.get("path", ""))
                    or entry["path"] in entries):
                raise TrajectoryRefused("archive branch contains non-trajectory content")
            entries[entry["path"]] = oid(entry["sha"])
        return head, tree_sha, entries

    def _spend(self, endpoint, payload, *, method="POST"):
        with tempfile.TemporaryDirectory(prefix="trajectory-effect-") as directory:
            path = Path(directory) / "request.json"
            path.write_bytes(canonical_bytes(payload))
            return parse_json(self.github.run_as_app(
                ["api", f"repos/{self.github.repository}/git/{endpoint}", "--method", method, "--input", str(path)],
                operation="archive_trajectory"))

    def publish(self, record):
        if (record.get("schema") != "dark-factory/trajectory" or record.get("schema_version") != "1.0"
                or record.get("repository") != self.github.repository
                or record.get("authority") != "observation-only" or record.get("learning_scope") != "project-local"):
            raise TrajectoryRefused("only project-local observations may enter this archive")
        run_id, attempt = positive(record["run_id"]), positive(record["run_attempt"])
        if record.get("trajectory_id") != f"traj_{run_id}_{attempt}":
            raise TrajectoryRefused("trajectory identity mismatch")
        raw = canonical_bytes(record)
        if len(raw) > MAX_RECORD:
            raise TrajectoryRefused("trajectory is too large")
        name = f"{run_id}-{attempt}.json"
        # Git's object address is used only for byte equality, never as evidence authority.
        blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        head, tree, entries = self._inventory()
        if name in entries:
            if entries[name] != blob:
                raise TrajectoryRefused("an existing trajectory cannot be replaced")
            return {"state": "already-recorded", "sha256": sha256_bytes(raw), "ref": ARCHIVE_REF}
        if len(entries) >= 10000:
            raise TrajectoryRefused("archive capacity reached")
        payload = {"tree": [{"path": name, "mode": "100644", "type": "blob", "content": raw.decode("utf-8")}]}
        if tree is not None:
            payload["base_tree"] = tree
        new_tree = oid(self._spend("trees", payload)["sha"])
        commit = {"message": f"Record factory trajectory {run_id}/{attempt}", "tree": new_tree}
        if head is not None:
            commit["parents"] = [head]
        new_commit = oid(self._spend("commits", commit)["sha"])
        try:
            if head is None:
                self._spend("refs", {"ref": ARCHIVE_REF, "sha": new_commit})
            else:
                self._spend("refs/heads/factory/trajectories", {"sha": new_commit, "force": False}, method="PATCH")
        except (RuntimeError, OSError):
            # An uncertain publication is observed once; no repeat POST or forced update.
            if self._inventory()[2].get(name) != blob:
                raise TrajectoryRefused("archive publication uncertain; inspect before recovery") from None
        if self._inventory()[2].get(name) != blob:
            raise TrajectoryRefused("archive publication was not observed")
        return {"state": "recorded", "sha256": sha256_bytes(raw), "ref": ARCHIVE_REF}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("collect", "publish"))
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        run_id, attempt = authorize_context(os.environ, event)
        github = GitHubClient(REPOSITORY, cwd=Path.cwd())
        if args.phase == "collect":
            record = collect(github, run_id=run_id, attempt=attempt)
            args.record.write_bytes(canonical_bytes(record))
            print(f"TRAJECTORY_COLLECTED run={run_id} attempt={attempt} gaps={len(record['gaps'])}")
        else:
            if args.record.is_symlink() or args.record.stat().st_size > MAX_RECORD:
                raise TrajectoryRefused("invalid collected trajectory file")
            record = parse_json(args.record.read_text())
            if record.get("run_id") != run_id or record.get("run_attempt") != attempt:
                raise TrajectoryRefused("collected trajectory does not match this workflow event")
            result = TrajectoryArchive(github).publish(record)
            print(f"TRAJECTORY_ARCHIVED state={result['state']} sha256={result['sha256']}")
        return 0
    except Exception:
        print("TRAJECTORY_ARCHIVE_REFUSED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
