"""Compare compiled decompositions without changing execution or inheriting proof.

This review is a proposal artifact, never a transition capability. The existing
publication lane still refuses replacement of an active programme.
"""
from __future__ import annotations

import re

from .canonical import sha256_value
from .programme import ProgrammeRefused, compile_programme


def review_replan(current_input, proposed_input, *, repository, source_sha):
    if any(not isinstance(value, dict) or value.get("version") != "1.0"
           for value in (current_input, proposed_input)):
        raise ProgrammeRefused("replanning review currently requires programme input v1.0")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ProgrammeRefused("replanning review requires an exact protected source")
    current = compile_programme(current_input, repository=repository)
    proposed = compile_programme(proposed_input, repository=repository)
    if current.spec != proposed.spec:
        raise ProgrammeRefused("replanning cannot change approved scope")
    if current.app_login != proposed.app_login:
        raise ProgrammeRefused("replanning cannot change the execution identity")
    old = {item["id"]: item for item in current.items}
    new = {item["id"]: item for item in proposed.items}
    old_owners = {ac: item["id"] for item in current.items for ac in item["acceptance"]}
    new_owners = {ac: item["id"] for item in proposed.items for ac in item["acceptance"]}
    # Both compilers independently enforce total, unique coverage and an acyclic DAG.
    # A stable item name alone is not unchanged scope or unchanged dependency ordering.
    changed = sorted(key for key in old.keys() & new.keys() if old[key] != new[key])
    return {
        "schema": "dark-factory/programme-replan-review", "schema_version": "1.0",
        "source_sha": source_sha, "spec_sha256": sha256_value(current.spec),
        "current_programme_sha256": current.sha256, "proposed_programme_sha256": proposed.sha256,
        "disposition": "unchanged" if current.sha256 == proposed.sha256 else "decomposition-change",
        "added_items": sorted(new.keys() - old.keys()), "retired_items": sorted(old.keys() - new.keys()),
        "changed_items": changed,
        "coverage": [{"acceptance": ac, "current_item": old_owners[ac], "proposed_item": new_owners[ac]}
                     for ac in sorted(old_owners)],
        "dependency_changes": [{"item": key, "current": old[key]["blocked_by"],
                                "proposed": new[key]["blocked_by"]}
                               for key in changed if old[key]["blocked_by"] != new[key]["blocked_by"]],
        "authority": "review-only", "activation": "requires-governed-transition",
        "evidence": "historical-outcomes-remain-bound-to-original-programme",
    }
