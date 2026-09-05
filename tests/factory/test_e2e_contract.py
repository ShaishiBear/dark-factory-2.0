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
        self.assertEqual(factory_e2e._ref(snap, "textbox", "Ask anything"), "e12")

    def test_ref_fails_closed_when_element_missing(self) -> None:
        with self.assertRaises(factory_e2e.E2EFailure):
            factory_e2e._ref('- button "Other" [ref=e1]', "button", "Send message")

    def test_ref_resolves_by_role_and_name_never_by_line_text(self) -> None:
        """D-049: the first line of the real interactive snapshot is the React root, a
        `generic` whose name is every visible string on the page. Substring matching over
        raw lines handed the root back for "Email" and "Password"; `fill` on that div said
        Done and the form was submitted empty."""
        self.assertEqual(factory_e2e._ref(REAL_LOGIN_SNAPSHOT, "textbox", "Email"), "e4")
        self.assertEqual(factory_e2e._ref(REAL_LOGIN_SNAPSHOT, "textbox", "Password"), "e5")
        self.assertEqual(factory_e2e._ref(REAL_LOGIN_SNAPSHOT, "button", "Log in"), "e3")
        self.assertEqual(factory_e2e._ref(REAL_LOGIN_SNAPSHOT, "link", "Sign up"), "e6")
        # Every query above has its text inside the root's name, on the first line.
        first = REAL_LOGIN_SNAPSHOT.splitlines()[0]
        for text in ("Email", "Password", "Log in"):
            self.assertIn(text, first)
        self.assertIn("[ref=e1]", first)

    def test_ref_refuses_a_container_even_when_only_it_carries_the_text(self) -> None:
        with self.assertRaises(factory_e2e.E2EFailure) as ctx:
            factory_e2e._ref(REAL_LOGIN_SNAPSHOT, "textbox", "Need a")
        self.assertIn("only containers carry that text: generic [ref=e1]", str(ctx.exception))
        with self.assertRaises(factory_e2e.E2EFailure):
            factory_e2e._ref(REAL_LOGIN_SNAPSHOT, "generic", "DynaChat")

    def test_ref_does_not_confuse_a_heading_with_the_input_of_the_same_name(self) -> None:
        """After login the page carries an h2 and a textbox with the same leading text; the
        heading is listed first."""
        self.assertEqual(
            factory_e2e._ref(REAL_CHAT_SNAPSHOT, "textbox", "Ask anything about the video library"),
            "e14")
        self.assertEqual(factory_e2e._ref(REAL_CHAT_SNAPSHOT, "button", "Send message"), "e15")

    def test_ref_reads_a_ref_that_shares_its_bracket_with_other_attributes(self) -> None:
        self.assertEqual(
            factory_e2e._ref('- textbox "Email" [required, ref=e4]', "textbox", "Email"), "e4")
        self.assertEqual(
            factory_e2e._ref('- button "Log in" [disabled, ref=e3]', "button", "Log in"), "e3")

    def test_journey_targets_only_role_qualified_nodes(self) -> None:
        source = (HARNESS / "e2e.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r'_ref\((\w+), "[^"]*"\)',
                            "every _ref call must name a role and an accessible name")
        for call in ('_ref(snap, "textbox", "Email")', '_ref(snap, "textbox", "Password")',
                     '_ref(snap, "button", "Log in")',
                     '_ref(snap, "textbox", "Ask anything about the video library")',
                     '_ref(snap, "button", "Send message")',
                     '_ref(modal, "link", "Open on YouTube")'):
            self.assertIn(call, source, call)

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
EMAIL = "dark-factory-e2e@example.com"
# `agent-browser snapshot -i` of the real login page (agent-browser 0.35.0, Vite dev server,
# 2026-09-05). The first node is the React root: every listener is delegated to it, so it is
# "clickable", and its name is the page's whole text.
REAL_LOGIN_SNAPSHOT = (
    '- generic "DynaChatAsk Cole Medin\'s YouTube videos and Dynamous lessons anythingLog '
    'inEmailPasswordLog inNeed a" [ref=e1] clickable [onclick]\n'
    '  - heading "Log in" [level=1, ref=e2]\n'
    '  - textbox "Email" [required, ref=e4]\n'
    '  - textbox "Password" [required, ref=e5]\n'
    '  - button "Log in" [ref=e3]\n'
    '  - link "Sign up" [ref=e6]\n'
)
REAL_CHAT_SNAPSHOT = (
    '- generic "DynaChatAsk anything about the video libraryThis AI has access to '
    'transcripts" [ref=e1] clickable [onclick]\n'
    '  - button "New chat" [ref=e7]\n'
    '  - heading "Ask anything about the video library" [level=2, ref=e9]\n'
    '  - textbox "Ask anything about the video library…" [ref=e14]\n'
    '  - button "Send message" [disabled, ref=e15]\n'
)
LOGIN_FORM = REAL_LOGIN_SNAPSHOT


FIXTURE_VIDEO_ID = json.loads(
    (HARNESS / "harness.config.json").read_text(encoding="utf-8"))["browser"]["fixture_video_id"]
HEALTHY_STREAM = {
    "status": 200, "content_type": "text/event-stream", "first_byte_ms": 1200,
    "body": 'data: "The video"\n\ndata: " is about"\n\nevent: sources\ndata: [{"chunk_id":"c1"}]'
            '\n\ndata: [DONE]\n\n',
    "transport": "",
}


class _App:
    """Only the calls run_e2e makes before the browser: the API floor, the login probe and
    the catalog probe. No `app_log`: the standalone adapter without a captured log."""

    port = 8765

    def __init__(self, login_status: int, login_body: str, headers: dict[str, str]):
        self.login = (login_status, login_body, headers)
        self.posts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, dict[str, str] | None]] = []

    def get(self, path: str, headers: dict[str, str] | None = None):
        self.gets.append((path, headers))
        if path == "/api/health":
            return 200, '{"status":"ok"}', {}
        if path == "/api/version":
            return 200, "{}", {}
        if path == "/api/videos":
            return 200, json.dumps([
                {"id": "v1", "url": f"https://www.youtube.com/watch?v={FIXTURE_VIDEO_ID}"}]), {}
        return 401, "{}", {}

    def post(self, path: str, body: str, headers: dict[str, str] | None = None):
        self.posts.append((path, body))
        if path == "/api/auth/login":
            return self.login
        return 401, "{}", {}


def healthy_http_json(method: str, url: str, body, cookie: str):
    """The frontend-origin JSON calls the stream probe makes, answered as the app would."""
    if method == "POST" and url.endswith("/api/conversations"):
        return 201, '{"id":"conv-probe"}', {}
    if method == "DELETE":
        return 204, "", {}
    return 404, "{}", {}


class E2ELoginEvidenceTests(unittest.TestCase):
    """D-047: the second production E2E stalled on the login form with no reason in the
    log. The route had refused the synthetic account's `.invalid` address with 422, the
    interactive snapshot never shows alert text, and nothing captured the page. The
    harness now asks the route first, prints its answer, and dumps what the browser saw
    whenever the journey breaks."""

    def setUp(self) -> None:
        self._env = {k: os.environ.get(k) for k in ("ARTIFACTS_DIR", "DARK_FACTORY_E2E_BOOTSTRAP")}
        os.environ.pop("DARK_FACTORY_E2E_BOOTSTRAP", None)
        self._orig_browser = factory_e2e._browser
        self._orig_env = factory_e2e._load_validation_env
        self._orig_post = factory_e2e._post_json
        self._orig_http_json = factory_e2e._http_json
        self._orig_stream = factory_e2e._stream_request
        factory_e2e._load_validation_env = lambda: (EMAIL, PASSWORD)
        # The proxy probe posts through the frontend origin; by default it answers as a
        # healthy Vite proxy would. Individual tests replace it.
        self.proxy_posts: list[tuple[str, str]] = []

        def healthy_proxy(url: str, body: str):
            self.proxy_posts.append((url, body))
            return 200, '{"id":"u1"}', {"set-cookie": "session=tok; HttpOnly; Secure; Path=/"}

        factory_e2e._post_json = healthy_proxy
        # The stream probe (harness-side POST of the question) answers as a healthy route
        # would; tests/factory/test_e2e_stream_evidence.py exercises its own refusals.
        factory_e2e._http_json = healthy_http_json
        factory_e2e._stream_request = lambda url, body, cookie, timeout_s: dict(HEALTHY_STREAM)

    def tearDown(self) -> None:
        factory_e2e._browser = self._orig_browser
        factory_e2e._load_validation_env = self._orig_env
        factory_e2e._post_json = self._orig_post
        factory_e2e._http_json = self._orig_http_json
        factory_e2e._stream_request = self._orig_stream
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
            ("get", "html", "html"): f'<input value="{PASSWORD}"><div role="alert">nope</div>',
            ("console",): "[error] Failed to load resource: 422\n",
            ("errors",): "",
            ("network", "requests"): "POST /api/auth/login 422\n",
            ("cookies", "get"): json.dumps([{"name": "session", "value": "tok", "secure": True}]),
        }

        filled: dict[str, str] = {}

        def fake_browser(session, *args, timeout=30, check=True):
            calls.append(tuple(args))
            if args[0] == "snapshot" and "-i" in args:
                return LOGIN_FORM
            if args[0] == "fill":
                filled[args[1]] = args[2]
                return "Done"
            if args[:2] == ("get", "value"):
                return filled.get(args[2], "")
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
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["e2e-evidence"])
            artifacts = Path(tmp) / "e2e-evidence"
            names = sorted(p.name for p in artifacts.iterdir())
            page = (artifacts / "page.html").read_text(encoding="utf-8")
            cookies = (artifacts / "cookies.txt").read_text(encoding="utf-8")
            snapshot = (artifacts / "snapshot.txt").read_text(encoding="utf-8")
            reason = (artifacts / "failure.txt").read_text(encoding="utf-8")

        self.assertIsNone(steps)
        self.assertIn("E2E_LOGIN_PROBE status=200 session_cookie=true", output)
        self.assertIn("E2E_PROXY_PROBE url=http://localhost:5173/api/auth/login status=200 "
                      "session_cookie=true", output)
        self.assertIn("E2E_FIELD_CHECK email=true password=true", output)
        self.assertIn("E2E_EVIDENCE_DUMP dir=", output)
        self.assertIn("e2e-evidence", output.split("E2E_EVIDENCE_DUMP dir=", 1)[1].split()[0])
        self.assertIn("E2E_FAIL  click @e3 failed", output)
        # The fills landed on the inputs, not on the root container (D-049).
        self.assertEqual(filled, {"@e4": EMAIL, "@e5": PASSWORD})
        self.assertIn(("click", "@e3"), calls)
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

    def _healthy_backend(self) -> _App:
        return _App(200, '{"id":"u1"}', {"Set-Cookie": "session=tok; HttpOnly; Secure; Path=/"})

    def _no_browser(self, reason: str) -> None:
        def no_browser(session, *args, **kwargs):
            raise factory_e2e.E2EFailure(reason)
        factory_e2e._browser = no_browser

    def test_proxy_probe_refuses_before_any_browser_when_the_frontend_path_fails(self) -> None:
        """D-049 boundary: the backend accepts the account, but the page posts through the
        frontend origin. A proxy that answers anything but 200-with-cookie stops the run
        before the browser and names itself in the log."""
        factory_e2e._post_json = lambda url, body: (502, "Bad Gateway from vite proxy", {})
        self._no_browser("browser launched although the proxy path refused")
        steps, output = self._run(self._healthy_backend())
        self.assertIsNone(steps)
        self.assertIn("E2E_LOGIN_PROBE status=200 session_cookie=true", output)
        self.assertIn("E2E_PROXY_PROBE url=http://localhost:5173/api/auth/login status=502 "
                      "session_cookie=false body=Bad Gateway from vite proxy", output)
        self.assertIn("E2E_FAIL  frontend proxies the login to the backend", output)
        self.assertNotIn("browser launched", output)

    def test_proxy_probe_requires_the_cookie_to_survive_the_proxy(self) -> None:
        factory_e2e._post_json = lambda url, body: (200, '{"id":"u1"}', {})
        self._no_browser("browser launched without a proxied cookie")
        steps, output = self._run(self._healthy_backend())
        self.assertIsNone(steps)
        self.assertIn("E2E_PROXY_PROBE url=http://localhost:5173/api/auth/login status=200 "
                      "session_cookie=false", output)
        self.assertIn("E2E_FAIL  frontend proxies the login to the backend", output)

    def test_proxy_probe_reports_an_unreachable_frontend_as_status_zero(self) -> None:
        def unreachable(url, body):
            raise factory_e2e.E2EFailure(f"POST {url} could not reach the frontend: refused")
        factory_e2e._post_json = unreachable
        self._no_browser("browser launched against an unreachable frontend")
        steps, output = self._run(self._healthy_backend())
        self.assertIsNone(steps)
        self.assertIn("E2E_PROXY_PROBE url=http://localhost:5173/api/auth/login status=0 "
                      "session_cookie=false body=POST http://localhost:5173/api/auth/login "
                      "could not reach the frontend: refused", output)
        self.assertNotIn("browser launched", output)

    def test_proxy_probe_posts_the_credentials_to_the_frontend_origin(self) -> None:
        self._no_browser("stop here")
        self._run(self._healthy_backend())
        self.assertEqual([u for u, _ in self.proxy_posts], ["http://localhost:5173/api/auth/login"])
        self.assertEqual(json.loads(self.proxy_posts[0][1]), {"email": EMAIL, "password": PASSWORD})

    def test_field_check_refuses_before_the_click_when_a_fill_did_not_land(self) -> None:
        """D-049: `fill` on the root container reports Done. The harness reads every field
        back and refuses before submitting; the detail names lengths, never the password."""
        calls: list[tuple[str, ...]] = []

        def fake_browser(session, *args, timeout=30, check=True):
            calls.append(tuple(args))
            if args[0] == "snapshot" and "-i" in args:
                return LOGIN_FORM
            if args[0] == "fill":
                return "Done"
            if args[:2] == ("get", "value"):
                return EMAIL if args[2] == "@e4" else ""
            if args[0] == "click":
                raise AssertionError("clicked Log in with an empty password field")
            if args[0] == "screenshot":
                Path(args[1]).write_bytes(b"png")
            return ""

        factory_e2e._browser = fake_browser
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ARTIFACTS_DIR"] = tmp
            steps, output = self._run(self._healthy_backend())
        self.assertIsNone(steps)
        self.assertIn("E2E_FIELD_CHECK email=true password=false", output)
        self.assertIn("E2E_FAIL  form fields hold the credentials before submit: "
                      f"email@e4 holds {len(EMAIL)} chars, expected {len(EMAIL)}; "
                      f"password@e5 holds 0 chars, expected {len(PASSWORD)}", output)
        self.assertNotIn(PASSWORD, output)
        self.assertIn(("get", "value", "@e4"), calls)
        self.assertIn(("get", "value", "@e5"), calls)
        self.assertNotIn(("click", "@e3"), calls)
        self.assertIn("E2E_EVIDENCE_DUMP dir=", output)

    def test_probes_run_in_order_before_the_browser(self) -> None:
        source = (HARNESS / "e2e.py").read_text(encoding="utf-8")
        backend = source.index("_probe_login(app, email, password)")
        proxy = source.index("_probe_proxy_login(frontend_url, email, password)")
        browser = source.index('_browser(session, "open", frontend_url')
        self.assertLess(backend, proxy)
        self.assertLess(proxy, browser)
        self.assertIn('"E2E_PROXY_PROBE url=', source)
        fills = source.index('_browser(session, "fill", f"@{password_ref}", password)')
        check = source.index("filled, detail = _check_fields(")
        click = source.index('_browser(session, "click", f"@{login_ref}")')
        self.assertLess(fills, check)
        self.assertLess(check, click)

    def test_artifact_dir_is_the_evidence_subdirectory_of_the_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ARTIFACTS_DIR"] = tmp
            path = factory_e2e._artifact_dir("df-1-2")
            self.assertEqual(path, (Path(tmp) / "e2e-evidence").resolve())
            self.assertTrue(path.is_dir())
        os.environ["ARTIFACTS_DIR"] = ""
        fallback = factory_e2e._artifact_dir("df-1-2")
        self.assertEqual(fallback.name, "dark-factory-e2e-df-1-2")
        self.assertNotIn(ROOT, (fallback, *fallback.parents))


class FrontendLaunchTests(unittest.TestCase):
    """The Vite child must proxy `/api` to the backend the harness started (D-049 probe
    boundary). `vite.config.ts` falls back to port 8000 without `VITE_API_TARGET`."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("factory_serve", HARNESS / "serve.py")
        assert spec and spec.loader
        cls.serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.serve)

    def test_vite_child_targets_the_chosen_backend_port(self) -> None:
        argv, env = self.serve.frontend_launch(53699, 5177)
        self.assertEqual(env["VITE_API_TARGET"], "http://127.0.0.1:53699")
        self.assertEqual(argv[:4], ["bun", "run", "dev", "--"])
        self.assertIn("--strictPort", argv)
        self.assertEqual(argv[argv.index("--port") + 1], "5177")
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
        # The rest of the environment is inherited, not replaced.
        self.assertEqual({k: v for k, v in env.items() if k != "VITE_API_TARGET"},
                         {k: v for k, v in os.environ.items() if k != "VITE_API_TARGET"})

    def test_server_launches_the_frontend_through_that_helper(self) -> None:
        source = (HARNESS / "serve.py").read_text(encoding="utf-8")
        self.assertIn("frontend_argv, frontend_env = frontend_launch(args.port, frontend_port)", source)
        self.assertIn("frontend_argv, cwd=FRONTEND, env=frontend_env,", source)
        self.assertIn("api_target={frontend_env['VITE_API_TARGET']}", source)

    def test_vite_reads_the_variable_the_server_exports(self) -> None:
        vite_config = ROOT / "app" / "frontend" / "vite.config.ts"
        if not vite_config.is_file():
            self.skipTest("repo-shaped copy without the frontend (mutation runner)")
        self.assertIn("process.env.VITE_API_TARGET", vite_config.read_text(encoding="utf-8"))


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
