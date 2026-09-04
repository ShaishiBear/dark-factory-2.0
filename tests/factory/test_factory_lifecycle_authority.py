"""There is one lifecycle definition, and it is the one the runtime executes.

`factory_kernel/state.py` used to describe an abstract stage machine that nothing consumed
while FACTORY.md called it the kernel's state machine. The evidence spine's ordered
`required_claims` are what the runtime closes and what merge verification enforces; the
control surface must not grow a second, unexecuted representation again.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPINE = ROOT / ".factory" / "evidence-spine.json"
MERGE_VERIFY = ROOT / "harness" / "merge_verify.py"
FACTORY_MD = ROOT / "FACTORY.md"


class LifecycleAuthorityTests(unittest.TestCase):
    def test_no_unexecuted_state_machine_module_exists(self):
        self.assertFalse((ROOT / "factory_kernel" / "state.py").exists())
        for rel in ("factory_kernel/runtime.py", "factory_kernel/worker_runtime.py", "factory_kernel/cli.py"):
            self.assertNotIn("from .state import", (ROOT / rel).read_text(encoding="utf-8"))

    def test_control_surface_has_no_lifecycle_shadow_command(self):
        from factory_kernel import cli

        with mock.patch.object(sys, "argv", ["python -m factory_kernel", "state-next", "--issue", "1"]):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()
        self.assertEqual(ctx.exception.code, 2, "argparse must reject the command as unknown")
        self.assertNotIn("state-next", (ROOT / "factory_kernel" / "cli.py").read_text(encoding="utf-8"))

    def test_the_spine_is_an_ordered_executable_sequence(self):
        policy = json.loads(SPINE.read_text(encoding="utf-8"))
        claims = policy["required_claims"]
        ids = [c["id"] for c in claims]
        self.assertGreaterEqual(len(ids), 10)
        self.assertEqual(len(ids), len(set(ids)), "claim ids must be unique so the sequence is exact")
        self.assertIn("exact required claim sequence", MERGE_VERIFY.read_text(encoding="utf-8"))

    @unittest.skipUnless(FACTORY_MD.exists(), "repo-shaped copy without FACTORY.md (mutation runner)")
    def test_documentation_names_the_spine_as_the_lifecycle(self):
        text = FACTORY_MD.read_text(encoding="utf-8")
        self.assertIn("the only executable lifecycle definition", text)
        self.assertNotIn("factory_kernel/state.py` |", text)


if __name__ == "__main__":
    unittest.main()
