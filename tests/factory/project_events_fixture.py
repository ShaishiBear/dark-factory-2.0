"""Deterministic journal flows, recorded once from the pinned baseline and replayed by tests.

WP01 acceptance asks that old event fixtures reload byte-identically after the journal
extraction. The flows below drive the four appending services (intake, exploration, budget,
replacement) with a pinned clock and fixed inputs, then return the project's history file
bytes. Run as a script on the baseline checkout this writes the fixtures; the test replays
the same flows on the current code and compares bytes. Nothing here is a production path.

    python tests/factory/project_events_fixture.py --record tests/factory/fixtures/project-events
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime as _datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FLOWS = ("intake", "budget", "exploration", "replacement")
CLOCK_MODULES = ("factory_kernel.frontdoor_intent", "factory_kernel.exploration_records",
                 "factory_kernel.execution_budget", "factory_kernel.replacement_intent")


class PinnedClock:
    """`datetime.now(tz)` returns a fixed instant advanced by one second per call, so every
    appended event carries a deterministic `created_at` on any code version."""
    calls = 0

    @classmethod
    def now(cls, tz=None):
        cls.calls += 1
        return _datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz or timezone.utc) + timedelta(seconds=cls.calls)

    @classmethod
    def reset(cls):
        cls.calls = 0


def _pinned():
    """Patch the clock in every module that stamps events on either code version. A module
    that no longer stamps events (after the extraction) is patched harmlessly."""
    patches = []
    for name in CLOCK_MODULES:
        try:
            module = __import__(name, fromlist=["datetime"])
        except ImportError:
            continue
        if hasattr(module, "datetime"):
            patches.append(patch.object(module, "datetime", PinnedClock))
    return patches


def _intent_file(store, project="citations"):
    return (store.directory / f"{project}.json").read_bytes()


def flow_intake(directory):
    from factory_kernel.frontdoor_intent import IntentRefused, IntentStore, Principal
    from tests.factory.test_frontdoor_intent import REPO, example_spec
    owner, worker = Principal("maintainer", "owner"), Principal("intake-agent", "proposal")
    store = IntentStore(directory, repository=REPO, owner=owner.identity)

    def run(version, operation, payload, *, key, principal=owner):
        return store.execute("citations", {"idempotency_key": key, "expected_project_version": version,
                                           "operation": operation, "payload": payload}, principal=principal)
    run(0, "record-intent", {"wording": "Show me the cited words.\nKeep video playback."}, key="k1")
    draft = run(1, "propose-spec", {"spec": example_spec(), "assumptions": ["Keep the current modal."],
                                    "open_questions": [], "technical_questions": ["Choose a focus control."]},
                key="k2", principal=worker)["draft"]
    run(2, "approve-spec", {"draft_version": draft["draft_version"], "spec_sha256": draft["spec_sha256"],
                            "wording": "Approve this scope."}, key="k3")
    run(3, "add-exploration", {"wording": "Consider a keyboard focus trap."}, key="k4")
    # Replay of an earlier command appends nothing; a changed body under the same key refuses.
    run(0, "record-intent", {"wording": "Show me the cited words.\nKeep video playback."}, key="k1")
    try:
        run(4, "add-exploration", {"wording": "different"}, key="k4")
    except IntentRefused:
        pass
    return _intent_file(store)


def flow_budget(directory):
    from tests.factory import test_execution_budget as budget_tests
    case = budget_tests.BudgetTests()
    case.setUp()
    try:
        case.approve()
        from factory_kernel.publication_source import observe_publication_source
        command = case.command("first", {"programme_sha256": case.programme.sha256, "role": "plan",
                                         "microusd": 1_000_000, "attempt": 1, "execution_id": "build"})
        observe = lambda: observe_publication_source(case.github)  # noqa: E731
        case.budget.reserve("citations", command, principal=case.owner, observe=observe)
        case.budget.start("citations", case.command("start-first", {"id": "first", "execution_id": "build"}),
                          principal=case.owner)
        case.observe("first")
        # Replay of the exact original command: history, not another capability, no new event.
        case.budget.reserve("citations", command, principal=case.owner, observe=observe)
        return _intent_file(case.store)
    finally:
        case.doCleanups()


def flow_exploration(directory):
    from tests.factory import test_exploration as exploration_tests
    case = exploration_tests.ExplorationTests()
    case.setUp()
    try:
        case.add()
        return _intent_file(case.store)
    finally:
        case.doCleanups()


def flow_replacement(directory):
    from tests.factory import test_replacement_intent as replacement_tests
    case = replacement_tests.ReplacementIntentTests()
    case.setUp()
    try:
        command = case.command()
        case.freeze(command)
        case.freeze(command)  # replay of the exact command returns the historical plan, appends nothing
        return _intent_file(case.case.store)
    finally:
        case.doCleanups()


def record_flows(scratch: Path, flows: tuple[str, ...] = FLOWS) -> dict[str, bytes]:
    results = {}
    for name in flows:
        PinnedClock.reset()
        directory = Path(scratch) / name
        directory.mkdir(parents=True, exist_ok=True)
        patches = _pinned()
        for item in patches:
            item.start()
        try:
            results[name] = globals()["flow_" + name](directory)
        finally:
            for item in reversed(patches):
                item.stop()
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True, help="directory receiving <flow>.json")
    parser.add_argument("--flows", nargs="*", default=list(FLOWS), choices=FLOWS)
    args = parser.parse_args(argv)
    import tempfile
    os.environ.setdefault("FACTORY_WORKDIR", str(ROOT / ".factory-work"))
    recorded = {}
    for name in args.flows:
        with tempfile.TemporaryDirectory(prefix="project-events-fixture-") as tmp:
            try:
                recorded.update(record_flows(Path(tmp), (name,)))
            except Exception as exc:  # noqa: BLE001 - a recorder reports, it does not hide
                print(f"FAILED {name}: {type(exc).__name__}: {exc}")
    args.record.mkdir(parents=True, exist_ok=True)
    for name, raw in recorded.items():
        (args.record / f"{name}.json").write_bytes(raw)
        print(f"recorded {name}: {len(raw)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
