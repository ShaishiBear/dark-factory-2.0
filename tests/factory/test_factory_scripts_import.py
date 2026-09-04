"""Every deterministic script imports the kernel from any working directory.

Canary attempt 10 (worker run 33920886708) completed the whole build and opened PR #74, then
`scripts/factory_provenance.py publish` died with `ModuleNotFoundError: No module named
'factory_kernel'`: the kernel runs its scripts from a detached PR-head worktree with no
PYTHONPATH, and the script imported the kernel without putting the repository root on
`sys.path`. The scripts are also run standalone by CI and humans, so the fix lives in each
script, not in the caller.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    sorted(ROOT.glob("scripts/factory_*.py"))
    + [ROOT / "scripts" / "frontier_filter.py"]
    # The two harness programs the kernel runs as authorities import the kernel too.
    + [ROOT / "harness" / "merge_verify.py", ROOT / "harness" / "post_merge.py"]
)
KERNEL_IMPORT = re.compile(r"^\s*(?:from|import)\s+factory_kernel\b", re.M)
# The code root is put on sys.path from beside the script (D-036: the tree under test is the
# working directory, so the import root must not be derived from it).
BOOTSTRAP = re.compile(r"sys\.path\.insert\(0, str\(HERE\.parent\)\)")
# Scripts whose --help needs no environment; the rest are exercised through a subcommand that
# fails after imports, or through the import-only check below.
HELP_OK = {"factory_provenance.py", "factory_evidence_spine.py", "factory_security.py",
           "factory_lease.py", "factory_protocol.py", "factory_artifacts.py",
           "factory_architecture.py", "factory_impact.py", "merge_verify.py", "post_merge.py"}


def run_from_outside(script: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = ""
    with tempfile.TemporaryDirectory(prefix="dark-factory-outside-") as cwd:
        return subprocess.run(
            [sys.executable, str(script), *args], cwd=cwd, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )


class ScriptImportTests(unittest.TestCase):
    def test_kernel_importing_scripts_bootstrap_the_repo_root_first(self):
        found = []
        for script in SCRIPTS:
            text = script.read_text(encoding="utf-8")
            match = KERNEL_IMPORT.search(text)
            if not match:
                continue
            found.append(script.name)
            with self.subTest(script=script.name):
                boot = BOOTSTRAP.search(text)
                self.assertIsNotNone(boot, f"{script.name} imports factory_kernel without the sys.path bootstrap")
                self.assertLess(boot.start(), match.start(), f"{script.name} bootstraps after its first factory_kernel import")
        self.assertIn("factory_provenance.py", found)
        self.assertIn("factory_evidence_spine.py", found)
        self.assertIn("merge_verify.py", found)
        self.assertIn("post_merge.py", found)

    def test_every_script_imports_from_a_cwd_outside_the_repo(self):
        """`--help` exits 0 after the module body ran, so any import error would surface."""
        for script in SCRIPTS:
            if script.name not in HELP_OK:
                continue
            with self.subTest(script=script.name):
                proc = run_from_outside(script, "--help")
                self.assertNotIn("ModuleNotFoundError", proc.stderr + proc.stdout, script.name)
                self.assertEqual(proc.returncode, 0, (proc.stdout + proc.stderr)[-800:])

    def test_publish_reaches_its_own_argument_check_from_outside_the_repo(self):
        """The exact failing invocation shape: publish with a missing artifacts dir must fail on
        the script's own argument handling, not on importing the kernel."""
        proc = run_from_outside(ROOT / "scripts" / "factory_provenance.py", "publish", "--pr", "1",
                                "--artifacts", "/nonexistent/artifacts")
        self.assertNotIn("ModuleNotFoundError", proc.stderr + proc.stdout)
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
