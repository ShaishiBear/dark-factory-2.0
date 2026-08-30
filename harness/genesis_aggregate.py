#!/usr/bin/env python3
"""Combine per-stage genesis results into one validation result, or refuse.

Fanning the validation stages out to one disposable runner each buys isolation, and costs a new
obligation: the pieces must be reassembled without letting anything go missing, arrive twice, or
come from somewhere else. A stage that never ran and a stage whose artifact was quietly dropped
look identical downstream unless something insists on the full set.

So this authority refuses unless every artifact agrees on the candidate commit, the candidate
tree, the driver digest and the recipe digest; unless every stage name appears exactly once; and
unless the set of stages present is exactly the set the recipe defines. It does not decide what
*should* have run -- the frozen external genesis policy does that, and the genesis verifier
re-checks the assembled result against it. This program's job is narrower: assemble faithfully,
and fail closed rather than paper over a gap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

SELF = Path(__file__).resolve()


def fail(message: str) -> None:
    print(f"AGGREGATION_REFUSED {message}", file=sys.stderr)
    raise SystemExit(1)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read stage artifact {path.name}: {exc}")
    if not isinstance(value, dict):
        fail(f"stage artifact must be a JSON object: {path.name}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--stage-results", required=True, nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    recipe_path = Path(args.recipe).resolve()
    if not recipe_path.is_file():
        fail(f"validation recipe does not exist: {recipe_path}")
    recipe_bytes = recipe_path.read_bytes()
    try:
        recipe = json.loads(recipe_bytes)
    except json.JSONDecodeError as exc:
        fail(f"validation recipe is not valid JSON: {exc}")
    expected = [str(s.get("name") or "") for s in recipe.get("stages") or []]
    if not expected or not all(expected):
        fail("validation recipe defines no stages")
    recipe_sha = digest(recipe_bytes)

    paths: list[Path] = []
    for item in args.stage_results:
        path = Path(item)
        paths.extend(sorted(path.rglob("*.json")) if path.is_dir() else [path])
    if not paths:
        fail("no stage artifacts were supplied")

    stages: dict[str, dict] = {}
    driver_sha: str | None = None
    tree: str | None = None
    for path in paths:
        value = read_json(path)
        if value.get("version") != "2.0":
            fail(f"stage artifact {path.name} has an unsupported version")
        if str(value.get("candidate_sha") or "") != args.commit:
            fail(f"stage artifact {path.name} is for a different commit")
        if str(value.get("recipe_sha256") or "") != recipe_sha:
            fail(f"stage artifact {path.name} did not execute the recipe being aggregated")
        this_driver = str(value.get("driver_sha256") or "")
        this_tree = str(value.get("candidate_tree") or "")
        if driver_sha is None:
            driver_sha, tree = this_driver, this_tree
        elif this_driver != driver_sha or this_tree != tree:
            fail(f"stage artifact {path.name} disagrees on the driver digest or candidate tree")

        stage = value.get("stage")
        if not isinstance(stage, dict) or not str(stage.get("name") or ""):
            fail(f"stage artifact {path.name} carries no named stage")
        name = str(stage["name"])
        if name in stages:
            fail(f"stage {name!r} was reported more than once")
        stages[name] = stage

    missing = sorted(set(expected) - set(stages))
    unexpected = sorted(set(stages) - set(expected))
    if missing:
        fail("stage results are missing for: " + ", ".join(missing))
    if unexpected:
        fail("stage results include stages the recipe does not define: " + ", ".join(unexpected))

    ordered = [stages[name] for name in expected]
    failed = [s["name"] for s in ordered if s.get("exit") != 0]
    payload = {
        "version": "1.0",
        "aggregator_sha256": digest(SELF.read_bytes()),
        "driver_sha256": driver_sha,
        "recipe_sha256": recipe_sha,
        "candidate_sha": args.commit,
        "candidate_tree": tree,
        "stage_isolation": "one-disposable-runner-per-stage",
        "stages": ordered,
        "failed_stages": failed,
        "verdict": "pass" if not failed else "fail",
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failed:
        fail("validation stages failed: " + ", ".join(failed))
    print(
        f"AGGREGATION_OK candidate={args.commit} tree={tree} stages={len(ordered)} "
        f"driver={driver_sha[:12]} recipe={recipe_sha[:12]}"
    )


if __name__ == "__main__":
    main()
