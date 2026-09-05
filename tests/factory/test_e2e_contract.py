from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
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


PASSWORD = "fake-probe-pw-Xy7"
LOGIN_FORM = (
    '- textbox "Email" [ref=e1]\n'
    '- textbox "Password" [ref=e2]\n'
    '- button "Log in" [ref=e3]\n'
)


class _App:
    """Only the two calls run_e2e makes before the browser, plus the login probe."""

    port = 8765

    def __init__(self, login_status: int, login_body: str, headers: dict[str, str]):
        self.login = (login_status, login_body, headers)
        self.posts: list[tuple[str, str]] = []

    def get(self, path: str):
        if path == "/api/health":
            return 200, '{"status":"ok"}', {}
        if path == "/api/version":
            return 200, "{}", {}
        return 401, "{}", {}

    def post(self, path: str, body: str):
        self.posts.append((path, body))
        if path == "/api/auth/login":
            return self.login
        return 401, "{}", {}


class E2ELoginEvidenceTests(unittest.TestCase):
    """D-047: the second production E2E stalled on the login form with no reason in the
    log. The route had refused the synthetic account's `.invalid` address with 422, the
    interactive snapshot never shows alert text, and nothing captured the page. The
    harness now asks the route first, prints its answer, and dumps what the browser saw
    whenever the journey breaks."""

    def setUp(self) -> None:
        self._env = {k: os.environ.get(k) for k in ("ARTIFACTS_DIR",)}
        self._orig_browser = factory_e2e._browser
        self._orig_env = factory_e2e._load_validation_env
        factory_e2e._load_validation_env = lambda: ("dark-factory-e2e@example.com", PASSWORD)

    def tearDown(self) -> None:
        factory_e2e._browser = self._orig_browser
        factory_e2e._load_validation_env = self._orig_env
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run(self, app: _App) -> tuple[object, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            steps = factory_e2e.run_e2e(app, frontend_url="http://localhost:5173")
        return steps, out.getvalue()

    def test_probe_refuses_before_any_browser_and_names_the_route_reason(self) -> None:
        body = json.dumps({"detail": [{
            "type": "value_error", "loc": ["body", "email"],
            "msg": "value is not a valid email address: The part after the @-sign is a "
                   "special-use or reserved name that cannot be used with email.",
            "input": PASSWORD,
        }]})
        app = _App(422, body, {"content-type": "application/json"})

        def no_browser(session, *args, **kwargs):
            raise factory_e2e.E2EFailure("browser launched before the route accepted the account")

        factory_e2e._browser = no_browser
        steps, output = self._run(app)

        self.assertIsNone(steps)
        self.assertIn("E2E_LOGIN_PROBE status=422 session_cookie=false", output)
        self.assertIn("special-use or reserved name", output)
        self.assertIn("E2E_FAIL  backend accepts the validation account", output)
        self.assertNotIn("browser launched", output)
        self.assertNotIn(PASSWORD, output)
        self.assertIn("***", output)
        self.assertEqual([p for p, _ in app.posts if p == "/api/auth/login"], ["/api/auth/login"])
        sent = json.loads(next(b for p, b in app.posts if p == "/api/auth/login"))
        self.assertEqual(sent, {"email": "dark-factory-e2e@example.com", "password": PASSWORD})

    def test_probe_requires_a_session_cookie_not_just_200(self) -> None:
        app = _App(200, '{"id":"u1"}', {"content-type": "application/json"})
        factory_e2e._browser = lambda session, *args, **kwargs: (_ for _ in ()).throw(
            factory_e2e.E2EFailure("browser launched without a session cookie"))
        steps, output = self._run(app)
        self.assertIsNone(steps)
        self.assertIn("E2E_LOGIN_PROBE status=200 session_cookie=false", output)
        self.assertIn("E2E_FAIL  backend accepts the validation account", output)

    def test_browser_failure_dumps_scrubbed_evidence_into_the_artifact_dir(self) -> None:
        app = _App(200, '{"id":"u1"}', {"Set-Cookie": "session=tok; HttpOnly; Secure; Path=/"})
        calls: list[tuple[str, ...]] = []
        canned = {
            ("get", "url"): "http://localhost:5173/login\n",
            ("snapshot",): f'- alert: value is not a valid email address\n{LOGIN_FORM}',
            ("get", "html"): f'<input value="{PASSWORD}"><div role="alert">nope</div>',
            ("console",): "[error] Failed to load resource: 422\n",
            ("errors",): "",
            ("network", "requests"): "POST /api/auth/login 422\n",
            ("cookies", "get"): json.dumps([{"name": "session", "value": "tok", "secure": True}]),
        }

        def fake_browser(session, *args, timeout=30, check=True):
            calls.append(tuple(args))
            if args[0] == "snapshot" and "-i" in args:
                return LOGIN_FORM
            if args[0] == "click":
                raise factory_e2e.E2EFailure("click @e3 failed rc=1: element detached")
            if args[0] == "screenshot":
                Path(args[1]).write_bytes(b"png")
                return ""
            return canned.get(tuple(args), "")

        factory_e2e._browser = fake_browser
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ARTIFACTS_DIR"] = tmp
            steps, output = self._run(app)
            artifacts = Path(tmp)
            names = sorted(p.name for p in artifacts.iterdir())
            page = (artifacts / "page.html").read_text(encoding="utf-8")
            cookies = (artifacts / "cookies.txt").read_text(encoding="utf-8")
            snapshot = (artifacts / "snapshot.txt").read_text(encoding="utf-8")
            reason = (artifacts / "failure.txt").read_text(encoding="utf-8")

        self.assertIsNone(steps)
        self.assertIn("E2E_LOGIN_PROBE status=200 session_cookie=true", output)
        self.assertIn("E2E_EVIDENCE_DUMP dir=", output)
        self.assertIn("E2E_FAIL  click @e3 failed", output)
        for expected in ("url.txt", "snapshot.txt", "page.html", "console.txt", "errors.txt",
                         "network.txt", "cookies.txt", "failure.png", "failure.txt"):
            self.assertIn(expected, names)
        self.assertNotIn(PASSWORD, page)
        self.assertIn('value="***"', page)
        self.assertIn('"value": "***"', cookies)
        self.assertIn('"name": "session"', cookies)
        self.assertIn("alert: value is not a valid email address", snapshot)
        self.assertIn("element detached", reason)
        self.assertIn(("close",), calls)
        # The dump runs before the session closes, so the page is still there to capture.
        self.assertLess(calls.index(("get", "url")), calls.index(("close",)))

    def test_cookie_scrubber_keeps_names_and_attributes(self) -> None:
        scrubbed = factory_e2e._scrub_cookie_values("session=abc.def; HttpOnly\nother=1\n")
        self.assertEqual(scrubbed, "session=*** HttpOnly\nother=***\n")


class E2EAccountEmailTests(unittest.TestCase):
    """The account the worker provisions must be one the login route accepts. The
    bootstrap pins the literal; both workflows must agree with it; and it must not sit
    under a special-use or reserved name, which pydantic's EmailStr refuses (D-047). The
    application-side pin lives in app/backend/tests/test_e2e_account_email.py, where the
    real validator is importable."""

    RESERVED = {"invalid", "localhost", "test", "example", "local", "onion", "arpa"}

    def _pinned(self) -> str:
        source = (HARNESS / "bootstrap_e2e.py").read_text(encoding="utf-8")
        match = re.search(r'^VALIDATION_EMAIL = "([^"]+)"$', source, re.MULTILINE)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_pinned_email_is_not_under_a_reserved_name(self) -> None:
        email = self._pinned()
        local, _, domain = email.partition("@")
        self.assertTrue(local and domain)
        self.assertNotIn(domain.rsplit(".", 1)[-1].lower(), self.RESERVED)
        self.assertGreaterEqual(domain.count("."), 1)

    def test_bootstrap_refuses_what_the_login_route_would_refuse(self) -> None:
        source = (HARNESS / "bootstrap_e2e.py").read_text(encoding="utf-8")
        self.assertIn("from backend.routes.auth import LoginRequest", source)
        self.assertIn("LoginRequest(email=email, password=password)", source)
        self.assertIn("E2E_BOOTSTRAP_REFUSED login route would reject", source)
        self.assertLess(source.index("LoginRequest(email=email"), source.index("await init_pg_pool()"))

    def test_both_workflows_provision_the_pinned_email(self) -> None:
        email = self._pinned()
        for rel in ("dark-factory-worker.yml", "dark-factory-main-regression.yml"):
            path = ROOT / ".github" / "workflows" / rel
            if not path.is_file():
                self.skipTest(f"repo-shaped copy without {rel} (mutation runner)")
            workflow = path.read_text(encoding="utf-8")
            self.assertIn(f"DARK_FACTORY_E2E_EMAIL={email}", workflow, rel)
            self.assertNotIn("localhost.invalid", workflow, rel)

    def test_harness_probes_login_before_the_browser(self) -> None:
        source = (HARNESS / "e2e.py").read_text(encoding="utf-8")
        self.assertIn("_probe_login(app, email, password)", source)
        self.assertLess(source.index("_probe_login(app, email, password)"),
                        source.index('_browser(session, "open", frontend_url'))
        self.assertIn('"E2E_LOGIN_PROBE status=', source)


if __name__ == "__main__":
    unittest.main()
