"""The wrapper must accept every call the kernel makes of the program it wraps (D-079).

`factory_kernel/worker_runtime.py` routes every `python scripts/factory_evidence.py ...` to
`scripts/factory_evidence_spine.py`, deliberately and unconditionally: production CLI commands
instantiate that class, so no autonomous merge can fall back to the legacy Evidence Bundle path.
The rule is blanket, and nothing checked that the wrapper's argument surface covers what the
kernel actually sends.

D-077 added a second call of that program -- the early trust-root currency check -- and it was
routed like every other. The spine's parser demanded the whole bundle's arguments, so the very
first production run of the check refused a real pull request in 0.081 s with:

    factory_evidence_spine.py: error: the following arguments are required:
    --verdict, --architecture-verdict, --output

That is a refusal of a real PR for a reason that has nothing to do with the PR, and it happened
because a wrapper and its callers drifted apart with no detector between them. Nothing in the
local suite could see it: the rehearsal records `_exec` without routing, and the routing itself
lives one class above.

These tests close the gap generally. They read every `_exec` call in the kernel that names the
wrapped program, route it exactly as the kernel does, and require the wrapper to parse it.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WRAPPED = "scripts/factory_evidence.py"
WRAPPER = "scripts/factory_evidence_spine.py"


def _spine() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("spine_under_test", ROOT / WRAPPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _kernel_calls(program: str) -> list[list[str]]:
    """Every `_exec` argv literal in the kernel whose second element is `program`.

    Only the flags are read. A flag's VALUE is usually an expression (a path, a PR number), so
    each flag is given a placeholder: what is under test is whether the wrapper accepts the
    SHAPE of the call, not what the kernel puts in it.
    """
    tree = ast.parse((ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8"))
    found: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.List) or len(first.elts) < 2:
            continue
        head = [e.value for e in first.elts[:2]
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if head != ["python", program]:
            continue
        argv: list[str] = []
        for element in first.elts[2:]:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                argv.append(element.value)
            else:
                argv.append("PLACEHOLDER")
        found.append(argv)
    return found


class RoutingTests(unittest.TestCase):
    def test_the_kernel_still_routes_the_wrapped_program_to_the_wrapper(self):
        source = (ROOT / "factory_kernel" / "worker_runtime.py").read_text(encoding="utf-8")
        self.assertIn(f'routed[1] == "{WRAPPED}"', source.replace("routed[1] = ", "routed[1] == "))
        self.assertIn(WRAPPER, source)

    def test_the_kernel_calls_the_wrapped_program_at_least_twice(self):
        # If this ever drops to one, the whole class of defect is gone and so is the reason for
        # this file; if it grows, each new call is covered by the test below without editing.
        self.assertGreaterEqual(len(_kernel_calls(WRAPPED)), 2)

    def test_the_wrapper_accepts_every_call_the_kernel_makes(self):
        parser = _spine().build_parser()
        for argv in _kernel_calls(WRAPPED):
            with self.subTest(argv=" ".join(argv)):
                try:
                    parser.parse_args(argv)
                except SystemExit as exc:
                    self.fail(
                        f"the wrapper refuses a call the kernel makes: "
                        f"{WRAPPER} {' '.join(argv)} (exit {exc.code})"
                    )

    def test_one_of_those_calls_is_the_currency_check(self):
        self.assertTrue(
            any("--currency-only" in argv for argv in _kernel_calls(WRAPPED)),
            "the early trust-root currency check is no longer a call of this program",
        )


class WrapperSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.parser = _spine().build_parser()

    def test_the_currency_call_parses(self):
        args = self.parser.parse_args(["--pr", "134", "--currency-only"])
        self.assertTrue(args.currency_only)
        self.assertEqual(args.pr, "134")

    def test_the_bundle_call_parses(self):
        args = self.parser.parse_args(
            ["--pr", "134", "--verdict", "v.json",
             "--architecture-verdict", "a.json", "--output", "o.json"]
        )
        self.assertFalse(args.currency_only)
        self.assertEqual(args.output, "o.json")

    def test_a_bundle_call_missing_its_arguments_is_still_refused(self):
        # Relaxing the three from `required=True` must not let a bundle run start without them.
        source = (ROOT / WRAPPER).read_text(encoding="utf-8")
        self.assertIn(
            "--verdict, --architecture-verdict and --output are required without --currency-only",
            source,
        )

    def test_the_wrapper_forwards_the_currency_call_to_the_program_that_owns_it(self):
        spine = _spine()
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = list(argv)
            seen["scope"] = kwargs.get("env", {}).get("_scope")
            return types.SimpleNamespace(returncode=0, stdout="EVIDENCE_CURRENCY_OK\n", stderr="")

        with mock.patch.object(spine.subprocess, "run", fake_run), \
                mock.patch.object(spine, "scoped_environment", lambda scope: {"_scope": scope}):
            spine.currency_only("134")
        self.assertIn("--currency-only", seen["argv"])
        self.assertIn("--pr", seen["argv"])
        self.assertIn("134", seen["argv"])
        self.assertTrue(seen["argv"][1].endswith("factory_evidence.py"))
        self.assertEqual(seen["scope"], "github")

    def test_a_refusal_by_the_forwarded_program_is_a_refusal_by_the_wrapper(self):
        # It must not fabricate a pass. A non-zero exit has to leave as a non-zero exit, or a
        # stale trust root is waved through by the very check added to catch it.
        spine = _spine()

        def fake_run(argv, **kwargs):
            return types.SimpleNamespace(
                returncode=1, stdout="", stderr="EVIDENCE_FAIL: PR trust root is not current\n"
            )

        with mock.patch.object(spine.subprocess, "run", fake_run), \
                mock.patch.object(spine, "scoped_environment", lambda scope: {}):
            with self.assertRaises(SystemExit) as caught:
                spine.currency_only("134")
        self.assertEqual(caught.exception.code, 1)

    def test_the_refused_text_reaches_the_caller_so_the_class_survives(self):
        # `is_stale_base` classifies by the sentence the inner program prints. If the wrapper
        # swallowed it, an early stale base would stop being a stale base and no re-head would
        # follow -- the refusal would sit on the PR waiting for a human instead.
        spine = _spine()
        message = "EVIDENCE_FAIL: PR trust root is not current with origin/main; rebase required"

        def fake_run(argv, **kwargs):
            return types.SimpleNamespace(returncode=1, stdout="", stderr=message + "\n")

        err = io.StringIO()
        with mock.patch.object(spine.subprocess, "run", fake_run), \
                mock.patch.object(spine, "scoped_environment", lambda scope: {}), \
                contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                spine.currency_only("134")
        self.assertIn("PR trust root is not current with origin/main", err.getvalue())

    def test_the_forwarded_call_runs_with_no_more_credentials_than_it_needs(self):
        source = (ROOT / WRAPPER).read_text(encoding="utf-8")
        start = source.index("def currency_only")
        window = source[start:source.index("def build_parser")]
        self.assertIn('scoped_environment(scope="github")', window)
        self.assertNotIn("github+validation", window)


if __name__ == "__main__":
    unittest.main()
