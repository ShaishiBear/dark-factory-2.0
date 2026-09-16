"""Persistent protected-main fence, independent of transient process ownership.

Presence stops execution. No field, malformed payload, local deletion or cached
observation may open the fence. Creating/removing it needs protected governance;
this reader does neither and is not an atomic activation lock.
"""
import re
import subprocess

FENCE_PATH = ".factory/programmes/execution-fence.json"


class ExecutionFenced(RuntimeError):
    pass


def fence_status(github, default_branch="main"):
    try:
        prefix = f"repos/{github.repository}"
        branch = github.json(["api", f"{prefix}/branches/{default_branch}"])
        revision = branch["commit"]["sha"]
        if branch.get("protected") is not True or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ExecutionFenced("execution fence requires an exact protected branch")
        tree = github.json(["api", f"{prefix}/git/trees/{revision}?recursive=1"])
        if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
            raise ExecutionFenced("execution fence inventory is incomplete")
        paths = [row["path"] for row in tree["tree"]]
        if any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths):
            raise ExecutionFenced("execution fence inventory is malformed")
        # A symlink, directory, duplicate or uninterpretable payload must never open a fence.
        fenced = any(path == FENCE_PATH or path.startswith(FENCE_PATH + "/") for path in paths)
        latest = github.json(["api", f"{prefix}/branches/{default_branch}"])
        if latest.get("protected") is not True or latest["commit"]["sha"] != revision:
            raise ExecutionFenced("execution fence source changed during observation")
        return {"state": "fenced" if fenced else "clear", "source_sha": revision, "path": FENCE_PATH}
    except ExecutionFenced:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        raise ExecutionFenced("execution fence state unavailable; execution remains stopped") from None


def require_execution_open(github, default_branch="main"):
    result = fence_status(github, default_branch)
    if result["state"] != "clear":
        raise ExecutionFenced("protected programme transition fence is present; execution remains stopped")
    return result["source_sha"]
