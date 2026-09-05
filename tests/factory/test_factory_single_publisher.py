"""Provenance has exactly one publisher: the kernel.

`scripts/factory_protocol.py attach` used to invoke `factory_provenance.py publish` itself,
and the kernel's `_attach_and_publish` invoked it again afterwards. When #86 made `--base`
required, the first, argument-less caller died before the kernel's correct call could run,
and a build that had passed every gate lost its PR at the last step (run 33941987102, D-044).
The duplicate is gone; these tests keep it gone.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROTOCOL = ROOT / "scripts" / "factory_protocol.py"


def load_protocol():
    spec = importlib.util.spec_from_file_location("factory_protocol_under_test", PROTOCOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contract() -> dict:
    return {
        "version": "2.0",
        "issue": {"number": 49, "title": "fix(export): a citation fallback drops the snippet"},
        "summary": "The YouTube fallback returns of formatCitation omit the list marker and snippet.",
        "behaviors": [{"id": "AC-1", "given": "an unparseable video_url", "when": "formatCitation runs",
                       "then": "the result contains the snippet blockquote", "seam": "app/frontend/src/lib/exportMarkdown.ts#formatCitation"}],
        "invariants": ["every current test keeps passing"],
        "out_of_scope": ["backend"],
        "risks": ["none"],
        "ambiguities": [],
    }


class ProtocolAttachTests(unittest.TestCase):
    def test_attach_edits_the_body_and_invokes_no_provenance_program(self):
        m = load_protocol()
        calls: list[list[str]] = []
        body_holder = {"body": "Fixes #49\n"}

        def fake_output(argv, text=True, **kw):
            calls.append(list(argv))
            return body_holder["body"]

        def fake_call(argv, **kw):
            calls.append(list(argv))
            if argv[:3] == ["gh", "pr", "edit"]:
                body_holder["body"] = Path(argv[argv.index("--body-file") + 1]).read_text(encoding="utf-8")
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task-contract.json"
            path.write_text(json.dumps(contract()), encoding="utf-8")
            args = mock.Mock(contract=str(path), pr=88)
            with mock.patch.object(m.subprocess, "check_output", side_effect=fake_output), \
                 mock.patch.object(m.subprocess, "check_call", side_effect=fake_call):
                m.run_attach(args)
        programs = [c for c in calls if c and c[0] != "gh"]
        self.assertEqual(programs, [], f"attach ran a non-gh program: {programs}")
        self.assertIn("```factory-contract", body_holder["body"])
        self.assertTrue(any(c[:3] == ["gh", "pr", "edit"] for c in calls))

    def test_protocol_source_never_names_the_provenance_program(self):
        source = PROTOCOL.read_text(encoding="utf-8")
        self.assertNotIn("factory_provenance", source)


class SinglePublisherTests(unittest.TestCase):
    """Every `publish` invocation in the kernel and scripts passes --base, and there is one."""

    def test_exactly_one_publish_site_and_it_passes_base(self):
        sites = []
        for path in sorted(list((ROOT / "factory_kernel").glob("*.py")) + list((ROOT / "scripts").glob("*.py"))):
            if path.name == "factory_provenance.py":
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"factory_provenance\.py\",\s*\"publish\"", text):
                window = text[match.start():match.start() + 600]
                sites.append((path.relative_to(ROOT).as_posix(), "--base" in window))
        self.assertEqual([s[0] for s in sites], ["factory_kernel/runtime.py"], sites)
        self.assertTrue(all(has_base for _, has_base in sites), sites)


if __name__ == "__main__":
    unittest.main()
