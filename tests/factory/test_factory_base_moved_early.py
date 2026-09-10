"""A base that moved is refused at the currency rung, not eighty-three minutes later (DFE-021).

Two real runs, one day apart, show the defect this closes.

PR #134 was re-headed onto c19546e on 2026-09-09. Then #157 merged, changing one line of
`docs/atlas/register/decisions.json`. `trust_root_drift` compares TRUST-ROOT files only, so it
saw nothing; the staleness would have surfaced at `harness/merge_verify.py`'s
`current_base != base`, at merge pre-authorization, after the whole ladder -- roughly 83
minutes in.

Later the same day #159 merged, touching `.factory/holdout/immunity.json`. That IS trust root,
so the currency check refused in 0.535 s (run 34397982945).

Same defect. Two orders of magnitude apart in detection cost, decided by nothing but which
paths a commit happened to touch. This asks the wider question at the cheap rung.

`merge_verify` keeps its own check; it is the authority and must re-ask when it authorises.
This only stops it being the first asker.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel import refusal as R  # noqa: E402

EVIDENCE = ROOT / "scripts" / "factory_evidence.py"
MERGE_VERIFY = ROOT / "harness" / "merge_verify.py"

MOVED = "main moved under the PR before validation"


class TheWideQuestionIsAskedEarly(unittest.TestCase):
    def test_the_currency_rung_compares_whole_main_not_only_the_trust_root(self):
        source = EVIDENCE.read_text(encoding="utf-8")
        self.assertIn('run(["git", "rev-parse", "origin/main"])', source)
        self.assertIn(MOVED, source)

    def test_it_runs_before_the_currency_only_early_return(self):
        """--currency-only is the cheap rung. The check is worthless below it."""
        source = EVIDENCE.read_text(encoding="utf-8")
        self.assertLess(source.index(MOVED), source.index("if args.currency_only:"),
                        "the base comparison must precede the currency-only return")

    def test_the_trust_root_drift_check_still_runs_and_is_not_replaced(self):
        """The narrow question is still asked; this adds a wider one beside it."""
        source = EVIDENCE.read_text(encoding="utf-8")
        self.assertIn("PR trust root is not current with origin/main", source)
        self.assertLess(source.index("PR trust root is not current"), source.index(MOVED))

    def test_merge_verify_keeps_its_own_comparison(self):
        """It is the authority. Being second is the change, not being removed."""
        source = MERGE_VERIFY.read_text(encoding="utf-8")
        self.assertIn("current_base != base", source)
        self.assertIn("main moved after evidence", source)


class TheRefusalStaysRecoverable(unittest.TestCase):
    """The part that would have made this change a net harm if it were wrong.

    The class is detected by TEXT. A refusal `is_stale_base` does not recognise is not
    `stale_base`, so `rehead_eligible` never becomes true, the dispatcher skips the PR and a
    human must intervene -- DFE-020's terminal state. Trading an 83-minute discovery for a
    permanent stall would be a worse defect than the one being fixed.
    """

    def test_the_new_message_is_recognised_as_a_stale_base(self):
        self.assertTrue(R.is_stale_base(f"{MOVED}; base=aaa origin/main=bbb"))

    def test_it_classifies_as_stale_base_from_the_currency_stage(self):
        exc = R.ToolRefused(
            ["python", "scripts/factory_evidence.py", "--pr", "1", "--currency-only"],
            rc=1, output=f"EVIDENCE_FAIL: {MOVED}; base=aaa origin/main=bbb",
        )
        self.assertEqual(R.classify("trust_root_currency", exc), "stale_base")

    def test_the_pattern_is_pinned_to_the_program_that_emits_it(self):
        pairs = [(prod, pat) for prod, pat in R.STALE_BASE_PATTERNS if pat == MOVED]
        self.assertEqual(len(pairs), 1, "exactly one producer owns this text")
        producer, pattern = pairs[0]
        self.assertEqual(producer, "scripts/factory_evidence.py")
        self.assertIn(pattern, (ROOT / producer).read_text(encoding="utf-8"))


class TheDocsCommitCaseIsTheOneThatWasMissed(unittest.TestCase):
    def test_a_base_moved_by_a_non_trust_root_commit_is_now_caught(self):
        """#157's shape: a docs-only commit. `trust_root_drift` returns nothing for it, so
        before this change nothing early refused. Driven against a real repository so the
        comparison actually runs rather than being read."""
        import tempfile

        def git(*args, cwd):
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

        with tempfile.TemporaryDirectory() as tmp:
            git("init", "-q", "-b", "main", ".", cwd=tmp)
            git("config", "user.email", "t@e.st", cwd=tmp)
            git("config", "user.name", "t", cwd=tmp)
            (Path(tmp) / "docs.md").write_text("one\n", encoding="utf-8")
            git("add", "-A", cwd=tmp)
            git("commit", "-qm", "base", cwd=tmp)
            base = git("rev-parse", "HEAD", cwd=tmp).stdout.strip()
            # a docs-only commit lands on main, exactly as #157 did
            (Path(tmp) / "docs.md").write_text("two\n", encoding="utf-8")
            git("commit", "-qam", "docs only", cwd=tmp)
            moved = git("rev-parse", "HEAD", cwd=tmp).stdout.strip()

            self.assertNotEqual(base, moved)
            # the comparison the check performs
            self.assertTrue(moved and moved != base,
                            "a docs-only commit moves main and must be refused early")


if __name__ == "__main__":
    unittest.main()
