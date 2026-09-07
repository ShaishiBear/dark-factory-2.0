"""The factory suite runs from a copy of the trust root that is not a repository (D-074).

The factory mutation family copies the trust root into a plain temporary directory and runs
this suite there, once per defect. Its first real run (validation 34073357593 of PR #134)
refused with `focused baseline is red` on a single error: a test handed
`WorkerControlledRuntime._agent` the tree this file lives in, and that stage ends by running
`git status` in the directory it was given. In a real checkout that happens to work; in the
copy there is no `.git`, so the stage raised `not a git repository` and the whole family --
391 defects, every one of them running this suite -- became unusable.

A test that only passes because the tree around it is a repository is not a hermetic test. The
check below is static, and it is static on purpose: the honest behavioural version of it is
"build a non-repository copy and run the suite in it", which is exactly what the mutation
family already does 391 times, and re-doing it inside the suite would make each of those 391
copies build and run a copy of its own. The AST check costs milliseconds, runs in every copy,
and is exact about the mechanism that actually broke -- executing `git` in a directory the
test did not create.

Scope. The rule covers the entry points that unconditionally shell out to `git` in whatever
directory they are handed: the worker runtime's `_agent`, `_git`, and a direct `git`
subprocess. `_exec` is deliberately not in that set: it runs whatever argv it is given, its
subprocess boundary is mocked wherever the suite points it at the checkout, and a rule over it
would flag tests that never execute anything.

The rule is applied to itself: `PositiveControlTests` feeds the analyser sources that must be
flagged, so a scan that has been quietly reduced to "return nothing" fails here rather than
passing silently.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "tests" / "factory"

# Calls that run `git` in the working directory they are given, and where that directory sits
# in the call. `_agent(role, cwd, paths, ...)` takes it second; a `_git` helper takes it first.
GIT_CWD_POSITION = {"_agent": 1, "_git": 0}
SUBPROCESS_ENTRIES = {"run", "Popen", "call", "check_call", "check_output"}


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _attrs(node: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def ambient_expressions(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names and attributes in this module that denote the checkout the file lives in.

    A module reaches its own tree through `__file__`; everything derived from that name is the
    ambient checkout too. Attributes are tracked only when every assignment to that attribute
    in the file is ambient, so a helper that sometimes stores a test-created directory in the
    same attribute is not mistaken for the checkout.
    """
    assignments: list[tuple[list[ast.expr], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            assignments.append((node.targets, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append(([node.target], node.value))

    names = {"__file__"}
    attr_ambient: dict[str, bool] = {}
    for _ in range(len(assignments) + 1):
        grew = False
        attr_ambient = {}
        for targets, value in assignments:
            ambient = bool(_names(value) & names) or bool(
                {a for a in _attrs(value) if attr_ambient.get(a)}
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    if ambient and target.id not in names:
                        names.add(target.id)
                        grew = True
                elif isinstance(target, ast.Attribute):
                    attr_ambient[target.attr] = attr_ambient.get(target.attr, True) and ambient
        if not grew:
            break
    names.discard("__file__")
    return names, {a for a, ambient in attr_ambient.items() if ambient}


def _is_ambient(node: ast.expr | None, names: set[str], attrs: set[str]) -> bool:
    if node is None:
        return False
    return bool(_names(node) & names) or bool(_attrs(node) & attrs)


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _is_git_argv(node: ast.expr | None) -> bool:
    return (
        isinstance(node, (ast.List, ast.Tuple))
        and bool(node.elts)
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "git"
    )


def ambient_repository_uses(source: str) -> list[str]:
    """Every place in `source` that runs `git` in the checkout the file lives in."""
    tree = ast.parse(source)
    names, attrs = ambient_expressions(tree)
    found: list[str] = []
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        func = call.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called in GIT_CWD_POSITION:
            position = GIT_CWD_POSITION[called]
            bound = isinstance(func, ast.Attribute) and called == "_git"
            args = call.args[1:] if bound else call.args
            cwd = args[position] if len(args) > position else _keyword(call, "cwd")
            if _is_ambient(cwd, names, attrs):
                found.append(f"line {call.lineno}: {called}() runs git in the ambient checkout")
            continue
        if called in SUBPROCESS_ENTRIES and _is_git_argv(call.args[0] if call.args else None):
            argv = call.args[0]
            if _is_ambient(_keyword(call, "cwd"), names, attrs) or _is_ambient(argv, names, attrs):
                found.append(f"line {call.lineno}: git subprocess in the ambient checkout")
    return sorted(found)


class SuiteIsHermeticTests(unittest.TestCase):
    def test_no_factory_test_runs_git_in_the_tree_it_lives_in(self):
        offenders: dict[str, list[str]] = {}
        for path in sorted(SUITE.glob("test_*.py")):
            hits = ambient_repository_uses(path.read_text(encoding="utf-8"))
            if hits:
                offenders[path.name] = hits
        self.assertEqual(
            offenders, {},
            "these tests only pass where the tree around them is a git repository; the mutation "
            "family runs this suite from a copy that is not one (D-074). Give the stage a "
            "repository the test creates.",
        )

    def test_the_suite_the_mutation_family_copies_is_covered(self):
        """The files the family actually runs are the files this rule has to reach."""
        self.assertTrue(SUITE.is_dir())
        self.assertIn("test_factory_worker_throughput.py", {p.name for p in SUITE.glob("test_*.py")})


class PositiveControlTests(unittest.TestCase):
    """The analyser is exercised against sources it must flag and sources it must not.

    Without these, `ambient_repository_uses` could be reduced to `return []` and the scan above
    would still be green.
    """

    HEADER = "from pathlib import Path\nROOT = Path(__file__).resolve().parents[2]\n"

    def test_the_defect_that_broke_the_family_is_flagged(self):
        source = self.HEADER + 'rt._agent("conformance", ROOT, paths, env=env)\n'
        self.assertEqual(len(ambient_repository_uses(source)), 1, source)

    def test_a_derived_ambient_path_is_flagged(self):
        source = self.HEADER + 'SUB = ROOT / "app"\nrt._agent("conformance", SUB, paths, env=env)\n'
        self.assertEqual(len(ambient_repository_uses(source)), 1, source)

    def test_an_ambient_attribute_is_flagged(self):
        source = self.HEADER + "rt.repo_root = ROOT\nrt._git('status', cwd=rt.repo_root)\n"
        self.assertEqual(len(ambient_repository_uses(source)), 1, source)

    def test_a_direct_git_subprocess_in_the_checkout_is_flagged(self):
        for call in (
            'subprocess.run(["git", "status"], cwd=ROOT)\n',
            'subprocess.run(["git", "-C", str(ROOT), "status"])\n',
        ):
            with self.subTest(call):
                self.assertEqual(len(ambient_repository_uses(self.HEADER + call)), 1, call)

    def test_a_test_created_repository_is_not_flagged(self):
        source = (
            self.HEADER
            + "repo = git_repo(Path(tmp))\n"
            + 'rt._agent("conformance", repo, paths, env=env)\n'
            + 'subprocess.run(["git", "init"], cwd=repo)\n'
        )
        self.assertEqual(ambient_repository_uses(source), [], source)

    def test_an_attribute_that_also_holds_a_test_directory_is_not_ambient(self):
        source = self.HEADER + "a.root = ROOT\nb.root = Path(tmp)\nrt._git('status', cwd=b.root)\n"
        self.assertEqual(ambient_repository_uses(source), [], source)

    def test_reading_the_checkout_is_not_running_git_in_it(self):
        source = self.HEADER + '(ROOT / "FACTORY.md").read_text(encoding="utf-8")\n'
        self.assertEqual(ambient_repository_uses(source), [], source)


if __name__ == "__main__":
    unittest.main()
