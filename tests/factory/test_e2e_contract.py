from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

spec = importlib.util.spec_from_file_location("factory_e2e", HARNESS / "e2e.py")
assert spec and spec.loader
factory_e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factory_e2e)


class E2EContractTests(unittest.TestCase):
    def test_ref_extracts_accessible_element(self) -> None:
        snap = '- textbox "Ask anything about the video library…" [ref=e12]'
        self.assertEqual(factory_e2e._ref(snap, "Ask anything"), "e12")

    def test_ref_fails_closed_when_element_missing(self) -> None:
        with self.assertRaises(factory_e2e.E2EFailure):
            factory_e2e._ref('- button "Other" [ref=e1]', "Send message")

    def test_citation_extracts_timestamp_and_ref(self) -> None:
        snap = '- button "12:34 — Locked Fixture" [ref=e42]'
        ref, label = factory_e2e._citation(snap)
        self.assertEqual(ref, "e42")
        self.assertEqual(factory_e2e._timestamp_seconds(label), 754)

    def test_youtube_urls_must_match_id_and_exact_time(self) -> None:
        external = "https://www.youtube.com/watch?v=pjF-0dliYhg&t=754s"
        embed = "https://www.youtube.com/embed/pjF-0dliYhg?start=754&autoplay=1"
        self.assertTrue(factory_e2e._youtube_matches(external, embed, "pjF-0dliYhg", 754))
        self.assertFalse(factory_e2e._youtube_matches(external, embed, "pjF-0dliYhg", 755))

    def test_scalar_preserves_json_encoded_multiline_metadata(self) -> None:
        raw = '"Video at 12:34\\nQuoted transcript evidence"'
        self.assertEqual(factory_e2e._scalar(raw), "Video at 12:34\nQuoted transcript evidence")

    def test_browser_opens_the_frontend_on_the_secure_context_host(self) -> None:
        """The session cookie is Secure (app/backend/tests/test_auth.py pins it). A browser
        stores a Secure cookie on plain HTTP only for `localhost`, not for 127.0.0.1; opened at
        the loopback literal, login succeeds, the cookie is dropped, /me is 401 and the app
        returns to the login form (D-046, the first production E2E). Every browser origin the
        harness builds must therefore be `localhost`, and the explicit-URL path must refuse
        the loopback literal."""
        source = (HARNESS / "e2e.py").read_text(encoding="utf-8")
        self.assertIn('BROWSER_ORIGIN_HOST = "localhost"', source)
        self.assertNotIn('f"http://127.0.0.1:{frontend_port}"', source)
        self.assertNotIn('f"http://127.0.0.1:{args.frontend_port}"', source)
        self.assertEqual(source.count("http://{BROWSER_ORIGIN_HOST}:"), 2)
        self.assertIn("parsed_frontend.hostname == BROWSER_ORIGIN_HOST", source)
        self.assertNotIn('hostname in {"127.0.0.1", "localhost"}', source)
        # The backend probe may keep using the loopback literal; only the browser origin matters.
        self.assertIn('f"http://127.0.0.1:{self.port}{path}"', source)

    @unittest.skipUnless((ROOT / "app" / "backend" / "tests" / "test_auth.py").is_file(),
                         "repo-shaped copy without the application (mutation runner)")
    def test_secure_cookie_invariant_is_still_pinned_by_the_app(self) -> None:
        """The harness fix moves the origin, not the cookie. The app-side test that requires
        the Secure attribute must keep existing so the E2E cannot be made green by weakening
        the cookie instead."""
        auth_tests = (ROOT / "app" / "backend" / "tests" / "test_auth.py").read_text(encoding="utf-8")
        self.assertIn('assert "secure" in lowered', auth_tests)

    @unittest.skipUnless((ROOT / ".archon" / "commands" / "dark-factory-behavioral-e2e.md").is_file(),
                         "repo-shaped copy without legacy .archon sources (mutation runner)")
    def test_validator_delegates_to_one_base_branch_browser_authority(self) -> None:
        command = (
            ROOT / ".archon" / "commands" / "dark-factory-behavioral-e2e.md"
        ).read_text(encoding="utf-8")
        harness_source = (HARNESS / "e2e.py").read_text(encoding="utf-8")

        self.assertIn("git archive origin/main harness", command)
        self.assertIn('python "$HOLDOUT_ROOT/harness/e2e.py"', command)
        self.assertIn("--backend-port", harness_source)
        self.assertIn("--frontend-port", harness_source)
        self.assertIn("E2E_PASSED steps=", harness_source)

        for competing_procedure in (
            "agent-browser open",
            "agent-browser snapshot",
            "agent-browser screenshot",
        ):
            self.assertNotIn(competing_procedure, command)


if __name__ == "__main__":
    unittest.main()
