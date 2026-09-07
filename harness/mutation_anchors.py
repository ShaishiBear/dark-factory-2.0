#!/usr/bin/env python3
"""Every mutation defect must still be injectable, and the static rung says so.

A mutation catalogue is a set of EXACT anchors into trust-root and application source. When
that source is refactored the anchor stops matching, the runner reports the defect
`not_injected`, and the property that defect stood for silently loses its detector. Nothing
is red: the catalogue still lists the defect, the run still prints a total, and the only
signal is a count nobody reads.

That is what happened to `main`. On 2026-09-07 twenty-three of the 409 factory defects had
drifted out of the tree at once, so `FACTORY_MUTATIONS_NOT_INJECTED=23` failed the mutation
gate and blocked every pull request from validating (PR #134, run 34088776764). The drift had
accumulated across ten merges, because the only thing that runs the mutation rung is the full
harness -- roughly fifty minutes into a validation -- and the daily main regression fails at
an earlier rung and never reaches it.

The check itself is pure text and costs milliseconds. It belongs in the static rung, where the
maintainer who moves an anchor meets it on their own pull request:

    python harness/mutation_anchors.py

Three ways a defect fails to inject, each reported by name and by file:

  * its file is outside the family's copy set, so the runner's copy never contains it;
  * its file does not exist;
  * its `find` text does not occur the number of times its runner requires.

The two families have deliberately different rules, and this program mirrors each runner
rather than inventing a third rule. `harness/factory_mutations/run.py` copies the trust root
and requires `text.count(anchor) == 1`, because a defect that could land in either of two
places is not the defect the catalogue names. `harness/mutations/run.py` mutates the live
worktree and requires only that the anchor is present, because an application defect may
deliberately change the first of several identical call sites -- `uuid-normaliser-dropped`
exists precisely to make two entry points disagree. An ambiguous application anchor is
reported as a note, not a failure.

THIS PROGRAM MUST NEVER RUN INSIDE A MUTATION COPY. `harness/factory_mutations/run.py` runs
`harness/focused.py` and nothing else, so putting this check in the static rung keeps it out
of the copies. A copy has exactly one anchor deliberately removed; a check of every anchor
would go red there for every defect and report all 409 of them as caught.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

APPLICATION_DEFECTS = HERE / "mutations" / "defects.json"


def _factory_runner():
    """The factory runner as a module, so its copy set and manifest list are read, not copied.

    Loaded by path rather than imported as a package: `harness` is not importable as one, and
    a second hand-maintained list of manifests here would be the exact failure this program
    exists to catch, one level up.
    """
    spec = importlib.util.spec_from_file_location(
        "factory_mutation_runner", HERE / "factory_mutations" / "run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_copy_set(rel: str, copy_files: tuple[str, ...], copy_dirs: tuple[str, ...]) -> bool:
    if rel in copy_files:
        return True
    return any(rel == d or rel.startswith(d + "/") for d in copy_dirs)


def check_factory(runner, root: Path = ROOT) -> tuple[list[str], int]:
    """Every factory defect, against the copy the factory runner would build."""
    failures: list[str] = []
    defects = runner.load_defects()
    copy_files = tuple(runner.COPY_FILES)
    copy_dirs = tuple(runner.COPY_DIRS)
    for defect in defects:
        rel = defect["file"]
        target = root / rel
        if not _in_copy_set(rel, copy_files, copy_dirs):
            failures.append(
                f"  {defect['id']:<56} {rel} is not in the runner's copy set"
            )
            continue
        if not target.is_file():
            failures.append(f"  {defect['id']:<56} {rel} does not exist")
            continue
        found = target.read_text(encoding="utf-8").count(defect["find"])
        if found != 1:
            failures.append(
                f"  {defect['id']:<56} anchor occurs {found}x in {rel}, must occur once"
            )
    return failures, len(defects)


def check_application(
    manifest: Path = APPLICATION_DEFECTS, root: Path = ROOT
) -> tuple[list[str], list[str], int]:
    """Every application defect, against the live worktree it is injected into."""
    failures: list[str] = []
    notes: list[str] = []
    defects = json.loads(manifest.read_text(encoding="utf-8"))["defects"]
    for defect in defects:
        rel = defect["file"]
        target = root / rel
        if not target.is_file():
            failures.append(f"  {defect['id']:<56} {rel} does not exist")
            continue
        found = target.read_text(encoding="utf-8").count(defect["find"])
        if found == 0:
            failures.append(f"  {defect['id']:<56} anchor is absent from {rel}")
        elif found > 1:
            notes.append(
                f"  {defect['id']:<56} anchor occurs {found}x in {rel}; the first is mutated"
            )
    return failures, notes, len(defects)


def main(runner=None) -> int:
    # `runner` is injectable so the detector's own tests can drive a synthetic catalogue; the
    # program itself always loads the real one.
    runner = runner if runner is not None else _factory_runner()
    factory_failures, factory_total = check_factory(runner, ROOT)
    app_failures, app_notes, app_total = check_application(APPLICATION_DEFECTS, ROOT)

    for note in app_notes:
        print(f"ANCHOR_NOTE{note}", flush=True)

    failures = factory_failures + app_failures
    if failures:
        print(
            f"MUTATION_ANCHORS_FAILED defects={factory_total + app_total} "
            f"uninjectable={len(failures)}",
            flush=True,
        )
        # Every one of them, by name. A count alone is what let twenty-three accumulate.
        for line in failures:
            print(line, flush=True)
        print(
            "Each line names a defect whose anchor no longer matches the source it guards. "
            "Re-anchor it onto the code that carries the property today, or -- if the property "
            "is gone -- say so in .factory/decisions.md and remove the defect deliberately.",
            flush=True,
        )
        return 1

    print(
        f"MUTATION_ANCHORS_OK factory={factory_total} application={app_total}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
