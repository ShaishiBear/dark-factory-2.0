"""Every kernel configuration field is read by the kernel.

`.factory/kernel.json` once carried `full_command`, `holdout_command`, `mutation_command` and
`max_repair_attempts`: parsed, validated, documented, and read by nothing. A setting nobody
consumes is a claim with no enforcer. Each dataclass field in `factory_kernel/config.py` must be
referenced by name somewhere in the kernel outside config.py, and the checked-in policy must
carry exactly the validation keys the schema defines.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import config as config_module  # noqa: E402
from factory_kernel.config import KernelConfig, ProviderConfig, RuntimeConfig, ValidationConfig  # noqa: E402

KERNEL = ROOT / "factory_kernel"
POLICY = ROOT / ".factory" / "kernel.json"


def kernel_sources_outside_config() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in KERNEL.glob("*.py") if p.name != "config.py"
    )


class ConsumedConfigTests(unittest.TestCase):
    def test_every_config_field_is_read_by_the_kernel(self):
        sources = kernel_sources_outside_config()
        for cls in (ProviderConfig, RuntimeConfig, ValidationConfig):
            for field in dataclasses.fields(cls):
                with self.subTest(cls=cls.__name__, field=field.name):
                    self.assertRegex(sources, rf"\.{re.escape(field.name)}\b",
                                     f"{cls.__name__}.{field.name} is parsed but never read")

    def test_validation_schema_has_only_the_quick_command(self):
        self.assertEqual([f.name for f in dataclasses.fields(ValidationConfig)], ["quick_command"])

    @unittest.skipUnless(POLICY.exists(), "repo-shaped copy without the policy")
    def test_checked_in_policy_carries_exactly_the_schema_keys(self):
        raw = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(sorted(raw["validation"]), sorted(f.name for f in dataclasses.fields(ValidationConfig)))
        self.assertEqual(sorted(raw["runtime"]), sorted(f.name for f in dataclasses.fields(RuntimeConfig)))

    def test_no_dead_key_is_parsed(self):
        source = (KERNEL / "config.py").read_text(encoding="utf-8")
        for dead in ("full_command", "holdout_command", "mutation_command", "max_repair_attempts"):
            self.assertNotIn(dead, source, dead)
        self.assertIsNotNone(config_module)
        self.assertTrue(dataclasses.is_dataclass(KernelConfig))


if __name__ == "__main__":
    unittest.main()
