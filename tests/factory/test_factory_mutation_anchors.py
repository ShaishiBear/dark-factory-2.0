"""A mutation defect that can no longer be injected is a detector that has silently gone (D-076).

The mutation catalogues anchor on exact source text. A refactor that moves the anchor does not
break anything visible: the runner reports the defect `not_injected`, the catalogue still lists
it, and the property it stood for has had no detector since. The only thing that notices is the
mutation rung, which runs in the full harness roughly fifty minutes into a validation, and the
daily main regression fails at an earlier rung and never reaches it.

On 2026-09-07 twenty-three of the 409 factory defects had drifted out of `main` at once and
`FACTORY_MUTATIONS_NOT_INJECTED=23` blocked every pull request at the mutation gate (PR #134,
run 34088776764). Diagnosing it took a local re-run of the whole catalogue, because the
runner's failure named a count and the per-defect lines had been truncated away by the time
the refusal reached an artifact.

These tests pin both halves of the fix: `harness/mutation_anchors.py` refuses every way a
defect can fail to inject and names each one, the static rung runs it, and a failing mutation
family names its escaped and uninjected members at its tail where truncation cannot reach them.

EVERY FIXTURE HERE IS SYNTHETIC. This file is in the factory runner's copy set, so it runs
inside all 409 mutation copies, and a copy has exactly one real anchor deliberately removed. A
test that checked the real catalogue would go red in every copy and report all 409 defects as
caught, which would destroy the family's entire signal. The real catalogue is checked by the
static rung, which no copy runs.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_checker() -> types.ModuleType:
    """The checker as a module, loaded by path: `harness` is not an importable package."""
    spec = importlib.util.spec_from_file_location(
        "mutation_anchors_under_test", ROOT / "harness" / "mutation_anchors.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


anchors = _load_checker()


def _fake_runner(defects: list[dict], copy_files: tuple[str, ...],
                 copy_dirs: tuple[str, ...] = (),
                 test_files: tuple[str, ...] | None = None) -> types.SimpleNamespace:
    """A stand-in for `harness/factory_mutations/run.py` with a catalogue of our choosing."""
    if test_files is None:
        test_files = tuple(
            rel for rel in copy_files
            if rel.startswith("tests/") and Path(rel).name.startswith("test_")
        )
    return types.SimpleNamespace(
        load_defects=lambda: defects, COPY_FILES=copy_files, COPY_DIRS=copy_dirs,
        TEST_FILES=test_files,
    )


class CopySetTests(unittest.TestCase):
    """Membership is by exact name or by directory boundary, never by bare prefix."""

    def test_an_exact_file_is_in_the_copy_set(self):
        self.assertTrue(anchors._in_copy_set("scripts/factory_proof.py",
                                             ("scripts/factory_proof.py",), ()))

    def test_a_file_under_a_copied_directory_is_in_the_copy_set(self):
        self.assertTrue(anchors._in_copy_set("factory_kernel/runtime.py", (), ("factory_kernel",)))

    def test_the_copied_directory_itself_is_in_the_copy_set(self):
        self.assertTrue(anchors._in_copy_set("factory_kernel", (), ("factory_kernel",)))

    def test_a_sibling_sharing_a_prefix_is_not_in_the_copy_set(self):
        # `factory_kernel_notes/x.py` starts with `factory_kernel` and is a different tree.
        self.assertFalse(
            anchors._in_copy_set("factory_kernel_notes/x.py", (), ("factory_kernel",))
        )

    def test_an_unlisted_file_is_not_in_the_copy_set(self):
        self.assertFalse(anchors._in_copy_set("app/backend/main.py", ("harness/ci.py",), ()))


class FactoryFamilyTests(unittest.TestCase):
    """The factory runner copies the trust root and requires a UNIQUE anchor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_unique_anchor_passes(self):
        self.write("scripts/guard.py", "alpha\nbeta\ngamma\n")
        runner = _fake_runner(
            [{"id": "d1", "file": "scripts/guard.py", "find": "beta\n", "replace": ""}],
            ("scripts/guard.py",),
        )
        failures, total = anchors.check_factory(runner, self.root)
        self.assertEqual(failures, [])
        self.assertEqual(total, 1)

    def test_a_missing_anchor_fails_and_names_the_defect(self):
        self.write("scripts/guard.py", "alpha\ngamma\n")
        runner = _fake_runner(
            [{"id": "anchor-gone", "file": "scripts/guard.py", "find": "beta\n", "replace": ""}],
            ("scripts/guard.py",),
        )
        failures, _ = anchors.check_factory(runner, self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("anchor-gone", failures[0])
        self.assertIn("occurs 0x", failures[0])
        self.assertIn("scripts/guard.py", failures[0])

    def test_an_ambiguous_anchor_fails(self):
        # Two matches is not the defect the catalogue names: the runner would rewrite the first
        # and the property under test would be whichever one that happened to be.
        self.write("scripts/guard.py", "beta\nbeta\n")
        runner = _fake_runner(
            [{"id": "two-places", "file": "scripts/guard.py", "find": "beta\n", "replace": ""}],
            ("scripts/guard.py",),
        )
        failures, _ = anchors.check_factory(runner, self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("two-places", failures[0])
        self.assertIn("occurs 2x", failures[0])

    def test_a_defect_whose_file_is_absent_fails(self):
        runner = _fake_runner(
            [{"id": "no-file", "file": "scripts/gone.py", "find": "x", "replace": ""}],
            ("scripts/gone.py",),
        )
        failures, _ = anchors.check_factory(runner, self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("does not exist", failures[0])

    def test_a_defect_outside_the_copy_set_fails_even_when_the_anchor_matches(self):
        # The runner's copy would not contain the file, so the anchor's presence in the real
        # tree proves nothing: the defect can never be injected.
        self.write("app/backend/main.py", "beta\n")
        runner = _fake_runner(
            [{"id": "uncopied", "file": "app/backend/main.py", "find": "beta\n", "replace": ""}],
            ("harness/ci.py",),
        )
        failures, _ = anchors.check_factory(runner, self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("not in the runner's copy set", failures[0])

    def test_every_failing_defect_is_reported_not_only_the_first(self):
        self.write("scripts/guard.py", "alpha\n")
        runner = _fake_runner(
            [
                {"id": "one", "file": "scripts/guard.py", "find": "beta", "replace": ""},
                {"id": "two", "file": "scripts/guard.py", "find": "gamma", "replace": ""},
                {"id": "three", "file": "scripts/guard.py", "find": "alpha", "replace": ""},
            ],
            ("scripts/guard.py",),
        )
        failures, total = anchors.check_factory(runner, self.root)
        self.assertEqual(total, 3)
        self.assertEqual(len(failures), 2)
        self.assertTrue(any("one" in line for line in failures))
        self.assertTrue(any("two" in line for line in failures))


class NamedDetectorTests(unittest.TestCase):
    """A defect whose stated detector never runs escapes whatever that test proves (D-078).

    `tests/factory/test_factory_rehead_guard_files.py` was written, committed and green, and was
    never in `COPY_FILES`. A copy runs the files in `COPY_FILES` and nothing else, so all four
    defects whose `why` named it escaped every run of the family that existed to catch them --
    three of them silently, from D-072 until the first run to report escapes by name.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def runner(self, defects: list[dict], test_files: tuple[str, ...]):
        return _fake_runner(defects, test_files, test_files=test_files)

    def test_a_named_detector_in_the_suite_passes(self):
        runner = self.runner(
            [{"id": "d", "file": "x", "find": "y", "replace": "",
              "why": "caught by tests/factory/test_factory_thing.py (it goes red)"}],
            ("tests/factory/test_factory_thing.py",),
        )
        self.assertEqual(anchors.check_named_detectors(runner), [])

    def test_a_named_detector_outside_the_suite_fails(self):
        runner = self.runner(
            [{"id": "unwired", "file": "x", "find": "y", "replace": "",
              "why": "caught by tests/factory/test_factory_missing.py (it goes red)"}],
            ("tests/factory/test_factory_thing.py",),
        )
        failures = anchors.check_named_detectors(runner)
        self.assertEqual(len(failures), 1)
        self.assertIn("unwired", failures[0])
        self.assertIn("tests/factory/test_factory_missing.py", failures[0])

    def test_a_why_that_names_no_detector_is_not_a_failure(self):
        # Most defects describe the property in prose without naming a file. That is allowed.
        runner = self.runner(
            [{"id": "d", "file": "x", "find": "y", "replace": "",
              "why": "The guard could be made advisory."}],
            (),
        )
        self.assertEqual(anchors.check_named_detectors(runner), [])

    def test_a_defect_with_no_why_at_all_is_not_a_failure(self):
        runner = self.runner([{"id": "d", "file": "x", "find": "y", "replace": ""}], ())
        self.assertEqual(anchors.check_named_detectors(runner), [])

    def test_every_named_detector_is_reported_not_only_the_first(self):
        runner = self.runner(
            [
                {"id": "one", "file": "x", "find": "y", "replace": "",
                 "why": "caught by tests/factory/test_factory_a.py"},
                {"id": "two", "file": "x", "find": "y", "replace": "",
                 "why": "caught by tests/factory/test_factory_b.py"},
            ],
            (),
        )
        failures = anchors.check_named_detectors(runner)
        self.assertEqual(len(failures), 2)
        self.assertTrue(any("one" in line for line in failures))
        self.assertTrue(any("two" in line for line in failures))

    def test_one_defect_naming_two_detectors_reports_both(self):
        runner = self.runner(
            [{"id": "d", "file": "x", "find": "y", "replace": "",
              "why": "caught by tests/factory/test_factory_a.py and "
                     "tests/factory/test_factory_b.py"}],
            ("tests/factory/test_factory_a.py",),
        )
        failures = anchors.check_named_detectors(runner)
        self.assertEqual(len(failures), 1)
        self.assertIn("test_factory_b.py", failures[0])

    def test_the_message_distinguishes_unwired_from_nonexistent(self):
        # A detector that exists is a wiring mistake; one that does not is a wrong name. The
        # fix differs, so the line says which.
        runner = self.runner(
            [{"id": "d", "file": "x", "find": "y", "replace": "",
              "why": "caught by tests/factory/test_factory_definitely_absent.py"}],
            (),
        )
        self.assertIn("does not exist", anchors.check_named_detectors(runner)[0])


class RealCatalogueTests(unittest.TestCase):
    """The one assertion in this file that may read the real catalogue.

    The anchor check may NOT: a copy has exactly one anchor deliberately removed, so asserting
    every anchor injects would go red in all of them and report every defect as caught. The
    detector-reference check is different in kind. It reads `why` strings and `TEST_FILES`, and
    injecting a source anchor touches neither, so it answers the same in every copy as it does
    on `main` -- except in the copies that mutate the copy set itself, where going red is
    exactly right (D-078).
    """

    def test_every_detector_the_catalogue_names_is_in_the_suite(self):
        self.assertEqual(anchors.check_named_detectors(anchors._factory_runner()), [])


class ApplicationFamilyTests(unittest.TestCase):
    """The application runner mutates the live tree and requires only PRESENCE."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def manifest(self, defects: list[dict]) -> Path:
        path = self.root / "app-defects.json"
        path.write_text(json.dumps({"defects": defects}), encoding="utf-8")
        return path

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_present_anchor_passes(self):
        self.write("app/x.py", "uid = _as_uuid(user_id)\n")
        manifest = self.manifest(
            [{"id": "d", "file": "app/x.py", "find": "_as_uuid", "replace": ""}]
        )
        failures, notes, total = anchors.check_application(manifest, self.root)
        self.assertEqual((failures, notes, total), ([], [], 1))

    def test_an_absent_anchor_fails(self):
        self.write("app/x.py", "uid = user_id\n")
        manifest = self.manifest(
            [{"id": "gone", "file": "app/x.py", "find": "_as_uuid", "replace": ""}]
        )
        failures, _, _ = anchors.check_application(manifest, self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("gone", failures[0])

    def test_a_repeated_anchor_is_a_note_not_a_failure(self):
        # `uuid-normaliser-dropped` is deliberately this shape: it changes the first of two
        # call sites so that two entry points derive different lock keys.
        self.write("app/x.py", "_as_uuid(a)\n_as_uuid(b)\n")
        manifest = self.manifest(
            [{"id": "first-of-two", "file": "app/x.py", "find": "_as_uuid", "replace": ""}]
        )
        failures, notes, _ = anchors.check_application(manifest, self.root)
        self.assertEqual(failures, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("first-of-two", notes[0])
        self.assertIn("the first is mutated", notes[0])

    def test_a_defect_whose_file_is_absent_fails(self):
        manifest = self.manifest(
            [{"id": "no-file", "file": "app/gone.py", "find": "x", "replace": ""}]
        )
        failures, _, _ = anchors.check_application(manifest, self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("does not exist", failures[0])


class MarkerTests(unittest.TestCase):
    """A failure prints a marker AND the name of every defect behind it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def run_main(self, factory_defects, copy_files, app_defects) -> tuple[int, str]:
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts" / "guard.py").write_text("alpha\n", encoding="utf-8")
        manifest = self.root / "app-defects.json"
        manifest.write_text(json.dumps({"defects": app_defects}), encoding="utf-8")
        runner = _fake_runner(factory_defects, copy_files)
        buffer = io.StringIO()
        original_root = anchors.ROOT
        original_manifest = anchors.APPLICATION_DEFECTS
        anchors.ROOT = self.root
        anchors.APPLICATION_DEFECTS = manifest
        try:
            with contextlib.redirect_stdout(buffer):
                rc = anchors.main(runner=runner)
        finally:
            anchors.ROOT = original_root
            anchors.APPLICATION_DEFECTS = original_manifest
        return rc, buffer.getvalue()

    def test_a_clean_catalogue_reports_ok_with_both_totals(self):
        rc, out = self.run_main(
            [{"id": "d1", "file": "scripts/guard.py", "find": "alpha", "replace": ""}],
            ("scripts/guard.py",),
            [{"id": "a1", "file": "scripts/guard.py", "find": "alpha", "replace": ""}],
        )
        self.assertEqual(rc, 0)
        self.assertIn("MUTATION_ANCHORS_OK factory=1 application=1", out)

    def test_a_drifted_catalogue_fails_and_names_each_defect(self):
        rc, out = self.run_main(
            [
                {"id": "drifted-one", "file": "scripts/guard.py", "find": "beta", "replace": ""},
                {"id": "drifted-two", "file": "scripts/guard.py", "find": "gamma", "replace": ""},
            ],
            ("scripts/guard.py",),
            [{"id": "a1", "file": "scripts/guard.py", "find": "alpha", "replace": ""}],
        )
        self.assertEqual(rc, 1)
        self.assertIn("MUTATION_ANCHORS_FAILED", out)
        self.assertIn("uninjectable=2", out)
        # The names, not only the count. A count is what let twenty-three accumulate unseen.
        self.assertIn("drifted-one", out)
        self.assertIn("drifted-two", out)


class StaticRungTests(unittest.TestCase):
    """The check is only a check if a rung runs it."""

    def test_the_static_rung_runs_the_anchor_checker(self):
        text = (ROOT / "harness" / "static.py").read_text(encoding="utf-8")
        self.assertIn("mutation-anchors", text)
        self.assertIn("mutation_anchors.py", text)

    def test_the_checker_exists_where_the_static_rung_names_it(self):
        self.assertTrue((ROOT / "harness" / "mutation_anchors.py").is_file())


class RunnerReportTests(unittest.TestCase):
    """A failing family names its members at its TAIL, where truncation cannot reach them."""

    def test_the_factory_runner_names_escaped_and_uninjected_defects(self):
        text = (ROOT / "harness" / "factory_mutations" / "run.py").read_text(encoding="utf-8")
        self.assertIn("FACTORY_MUTATIONS_ESCAPED", text)
        self.assertIn("FACTORY_MUTATIONS_UNINJECTED", text)
        # After the counts and before the family's failure marker: the last thing printed is
        # what survives a consumer that keeps only the end of the stream.
        self.assertLess(text.index("FACTORY_MUTATIONS_ESCAPED"),
                        text.index("FACTORY_MUTATIONS_FAILED - factory trust-root bypass"))

    def test_the_application_runner_names_escaped_and_uninjected_defects(self):
        text = (ROOT / "harness" / "mutations" / "run.py").read_text(encoding="utf-8")
        self.assertIn("MUTATIONS_ESCAPED", text)
        self.assertIn("MUTATIONS_UNINJECTED", text)


if __name__ == "__main__":
    unittest.main()
