"""A refusal never names an authority that did not produce it (DFE-014).

Run 34151427980 validated PR #134 end to end -- the evidence stage returned `outcome=ok`
after 4970.989 s and merge pre-authorization returned `outcome=ok` after 0.708 s -- and then
`gh pr merge` returned 401 Bad credentials because the App installation token, minted 95
minutes earlier, had expired. The refusal posted to the PR said:

    Refused by: merge pre-authorization (harness/merge_verify.py pre) (`merge_preauth`)

`runtime.py` set `stage = "merge_preauth"` before the merge_verify call and never advanced it;
`merge_squash` is invoked twenty lines later with the cursor unchanged. The failure was a
RuntimeError rather than a ToolRefused, so `classify` skipped the isinstance branch that holds
the correct discriminator and fell through to `if stage in STAGE_CODES: return stage`. The
refusal was correct and its attribution was false. Four days, and four repeat reports on issue
#119, were spent reasoning from it.

The fix is structural rather than heuristic: the cursor names the authority CURRENTLY
EXECUTING, `_exec` closes it the moment that authority returns, and `classify` refuses to
attribute when it is closed. Nothing here consults the stage-timings artifact -- that file is
observability only, and reading it to decide attribution would make an observational artifact
into a semi-trusted input (DFC-072).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import refusal as R  # noqa: E402
from factory_kernel.runtime import NeedsHuman  # noqa: E402

CONSTITUTION = ROOT / "docs" / "atlas" / "01-CONSTITUTION.md"


class ClosedCursorNeverAttributes(unittest.TestCase):
    def test_the_defect_itself_no_longer_names_merge_preauth(self):
        """The exact shape of run 34151427980: a 401 from `gh pr merge`, raised after
        merge pre-authorization had returned."""
        exc = RuntimeError(
            "gh pr merge 134 -R ShaishiBear/dark-factory-2.0 --squash "
            "--match-head-commit 7feacadbec60cf7ba36d0936628431a86dba559c failed rc=1: "
            'non-200 OK status code: 401 Unauthorized body: "{\\"message\\": \\"Bad credentials\\"}"'
        )
        self.assertEqual(R.classify(None, exc), "unknown")
        described = R.describe(None, exc)
        self.assertNotIn("merge_verify", described.authority)
        self.assertNotIn("merge pre-authorization", described.authority)

    def test_no_stage_code_survives_a_closed_cursor(self):
        """Not one of them, so the next instance cannot be a different stage."""
        for code in sorted(R.STAGE_CODES):
            with self.subTest(code):
                self.assertEqual(R.classify(None, RuntimeError("anything")), "unknown")
                self.assertNotIn(code, R.describe(None, RuntimeError("anything")).authority)

    def test_an_open_cursor_still_attributes(self):
        """The fix must not make the classifier useless: a kernel-raised refusal while the
        authority is executing is still that authority's, and all four such stages keep
        working."""
        for code in ("attached_evidence", "code_holdout", "architecture_holdout", "identity"):
            with self.subTest(code):
                self.assertEqual(R.classify(code, NeedsHuman("something went wrong")), code)

    def test_the_unattributed_authority_string_does_not_read_as_a_name(self):
        text = R.AUTHORITY["unknown"]
        self.assertIn("unattributed", text)
        self.assertNotIn(".py", text, "an unattributed failure must not name a program")

    def test_the_record_separates_attribution_from_context(self):
        """`stage_context` says where the run had reached; it is never read as blame."""
        record = R.refusal_record(
            R.describe(None, RuntimeError("401 Unauthorized")),
            pr=134, head="a" * 40, base="b" * 40,
            stage=None, stage_context="merge_preauth", timestamp="2026-09-07T19:58:00Z",
        )
        self.assertEqual(record["stage"], "")
        self.assertEqual(record["stage_context"], "merge_preauth")
        self.assertEqual(record["reason_code"], "unknown")
        marker = R.render_refusal_marker(record)
        self.assertIn('"stage_context": "merge_preauth"', marker)
        self.assertIn('"reason_code": "unknown"', marker)


class TheCursorIsClosedByTheAuthorityThatReturns(unittest.TestCase):
    def test_exec_closes_the_cursor_on_a_successful_return(self):
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        body = source.split("    def _exec(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("self._authority_cursor = None", body,
                      "_exec must close the cursor when an authority returns")
        raise_at = body.index("raise ToolRefused")
        close_at = body.index("self._authority_cursor = None")
        self.assertLess(raise_at, close_at,
                        "a refusing authority must leave its cursor OPEN, so it is still named")

    def test_the_merge_is_not_inside_any_authority_cursor(self):
        """The window this defect lived in: everything from the merge onward runs with no
        authority executing, so nothing there can borrow a name."""
        source = (ROOT / "factory_kernel" / "runtime.py").read_text(encoding="utf-8")
        after_merge_pre = source.split('self._authority_cursor = stage_context = "merge_preauth"', 1)[1]
        merge_call = after_merge_pre.index("self.github.merge_squash(")
        opened = after_merge_pre.find("self._authority_cursor = ", 0, merge_call)
        self.assertEqual(opened, -1, "no cursor may be opened between merge-pre and the merge")


class TheInvariantIsWrittenDown(unittest.TestCase):
    @unittest.skipUnless(CONSTITUTION.exists(), "repo-shaped copy without the atlas")
    def test_the_constitution_carries_the_diagnosis_invariant(self):
        text = CONSTITUTION.read_text(encoding="utf-8")
        self.assertIn("Never attribute a failure to an authority that did not produce it", text)
        self.assertIn("Diagnosis", text)


if __name__ == "__main__":
    unittest.main()
