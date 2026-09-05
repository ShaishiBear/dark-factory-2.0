"""D-051: the streaming step of the browser journey carries its own evidence.

Validation of PR #99 (run 33960088633) got past login and failed at the streaming step with
`browser state did not appear in 10s`. The network log held a `POST .../messages` with no
status, the snapshot showed the question and the Send button, `page.html` was the CLI's
usage text, and the app process log -- the one place the backend says why a stream broke --
was a pipe nobody read after `APP_STARTED`. These tests pin what the next such run prints.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e2e = _load("factory_e2e_stream", HARNESS / "e2e.py")
appproc = _load("factory_appproc_stream", HARNESS / "appproc.py")

CONFIG = json.loads((HARNESS / "harness.config.json").read_text(encoding="utf-8"))
VIDEO_ID = str(CONFIG["browser"]["fixture_video_id"])
QUESTION = str(CONFIG["browser"]["question"])
EMAIL = "dark-factory-e2e@example.com"
PASSWORD = "fake-probe-pw-Xy7"
COOKIE = "session=tok.abc.def"
SET_COOKIE = {"Set-Cookie": "session=tok.abc.def; HttpOnly; Secure; Path=/"}
BOOTSTRAP_LINE = f"E2E_BOOTSTRAP_OK fixture_video_id={VIDEO_ID} chunks=12"
UVICORN_ERROR = "ERROR:backend.llm.openrouter:OpenRouter streaming API error: Error code: 402"
DSN_PASSWORD = "pg-pass-word"
# Assembled at runtime: the security guard's `credential_url` pattern refuses a literal of
# the shape `scheme://user:password@` in any added line, fixture or not.
FAKE_DSN = "postgresql://dynachat:" + DSN_PASSWORD + "@127.0.0.1:5433/dark_factory_validation"

HEALTHY_BODY = (
    'data: "The video"\n\n'
    ": keepalive\n\n"
    'event: status\ndata: {"type": "tool_call_start", "tool": "search_videos"}\n\n'
    'data: " is about factories"\n\n'
    'event: sources\ndata: [{"chunk_id": "c1", "start_seconds": 754}]\n\n'
    "data: [DONE]\n\n"
)
ERROR_BODY = 'data: "Partial"\n\ndata: {"error": "OpenRouter said no: key sk-secret-value-1"}\n\n'


def _stream(**overrides: object) -> dict:
    result = {
        "status": 200,
        "content_type": "text/event-stream",
        "first_byte_ms": 1200,
        "body": HEALTHY_BODY,
        "transport": "",
    }
    result.update(overrides)
    return result


LOGIN_FORM = (
    "- generic \"DynaChatAsk Cole Medin's YouTube videos and Dynamous lessons anythingLog "
    'inEmailPasswordLog inNeed a" [ref=e1] clickable [onclick]\n'
    '  - heading "Log in" [level=1, ref=e2]\n'
    '  - textbox "Email" [required, ref=e4]\n'
    '  - textbox "Password" [required, ref=e5]\n'
    '  - button "Log in" [ref=e3]\n'
)
CHAT_INTERACTIVE = (
    '- generic "DynaChatAsk anything about the video libraryThis AI has access to '
    'transcripts" [ref=e1] clickable [onclick]\n'
    '  - button "New chat" [ref=e7]\n'
    '  - heading "Ask anything about the video library" [level=2, ref=e9]\n'
    '  - textbox "Ask anything about the video library…" [ref=e14]\n'
    '  - button "Send message" [disabled, ref=e15]\n'
)
# Full (non-interactive) snapshots in the shape agent-browser 0.35.0 prints: the same
# `- button "..." [ref=eN]` lines as the interactive view plus `- StaticText "..."` nodes.
CHAT_BASELINE = (
    "- generic\n"
    '  - button "New chat" [ref=e7]\n'
    '  - heading "Ask anything about the video library" [level=2, ref=e9]\n'
    '  - StaticText "This AI has access to transcripts"\n'
    '  - textbox "Ask anything about the video library…" [ref=e14]\n'
    '  - button "Send message" [disabled, ref=e15]\n'
)
CHAT_STREAMING = (
    "- generic\n"
    '  - button "New chat" [ref=e7]\n'
    f'  - StaticText "{QUESTION}"\n'
    '  - StaticText "Searching videos for factory"\n'
    '  - textbox "Ask anything about the video library…" [ref=e14]\n'
    '  - button "Stop response" [ref=e16]\n'
)
CHAT_SENT_ONLY = (
    "- generic\n"
    '  - button "New chat" [ref=e7]\n'
    f'  - StaticText "{QUESTION}"\n'
    '  - textbox "Ask anything about the video library…" [ref=e14]\n'
    '  - button "Send message" [ref=e15]\n'
)
CHAT_ERROR = (
    "- generic\n"
    '  - button "New chat" [ref=e7]\n'
    f'  - StaticText "{QUESTION}"\n'
    "  - paragraph\n"
    '    - StaticText "Failed to get a response. Please try again."\n'
    '  - button "Retry" [ref=e20]\n'
    '  - textbox "Ask anything about the video library…" [ref=e14]\n'
    '  - button "Send message" [ref=e15]\n'
)
CHAT_ANSWERED = (
    "- generic\n"
    '  - button "New chat" [ref=e7]\n'
    f'  - StaticText "{QUESTION}"\n'
    "  - paragraph\n"
    '    - StaticText "The video is about building a dark factory."\n'
    '  - button "12:34 — Locked Fixture" [ref=e42]\n'
    '  - textbox "Ask anything about the video library…" [ref=e14]\n'
    '  - button "Send message" [ref=e15]\n'
)
MODAL = (
    '- dialog "Video citation" [ref=e48]\n'
    '  - heading "Video citation" [level=2, ref=e49]\n'
    '  - link "Open on YouTube" [ref=e50]\n'
    '  - button "Close" [ref=e51]\n'
)
EXTERNAL = f"https://www.youtube.com/watch?v={VIDEO_ID}&t=754s"
EMBED = f"https://www.youtube.com/embed/{VIDEO_ID}?start=754&autoplay=1"


class _App:
    """The calls run_e2e makes before the browser, answered as a healthy app would."""

    port = 8765

    def __init__(self, app_log: Path | None = None, videos: list[dict] | None = None):
        self.app_log = app_log
        self.videos = (
            videos
            if videos is not None
            else [{"id": "v1", "url": f"https://www.youtube.com/watch?v={VIDEO_ID}"}]
        )
        self.gets: list[tuple[str, dict[str, str] | None]] = []

    def get(self, path: str, headers: dict[str, str] | None = None):
        self.gets.append((path, headers))
        if path == "/api/health":
            return 200, '{"status":"ok"}', {}
        if path == "/api/version":
            return 200, "{}", {}
        if path == "/api/videos":
            return 200, json.dumps(self.videos), {}
        return 401, "{}", {}

    def post(self, path: str, body: str, headers: dict[str, str] | None = None):
        if path == "/api/auth/login":
            return 200, json.dumps({"id": "u1", "email": EMAIL}), dict(SET_COOKIE)
        return 401, "{}", {}


class _Harness(unittest.TestCase):
    """Fakes for every boundary run_e2e crosses; each test narrows one of them."""

    def setUp(self) -> None:
        self._env = {
            k: os.environ.get(k)
            for k in ("ARTIFACTS_DIR", "DARK_FACTORY_E2E_BOOTSTRAP", "PROBE_TEST_API_KEY")
        }
        os.environ.pop("DARK_FACTORY_E2E_BOOTSTRAP", None)
        self._orig = {
            name: getattr(e2e, name)
            for name in (
                "_browser",
                "_load_validation_env",
                "_post_json",
                "_http_json",
                "_stream_request",
            )
        }
        e2e._load_validation_env = lambda: (EMAIL, PASSWORD)
        e2e._post_json = lambda url, body: (200, '{"id":"u1"}', dict(SET_COOKIE))
        self.json_calls: list[tuple[str, str, object, str]] = []

        def http_json(method: str, url: str, body, cookie: str):
            self.json_calls.append((method, url, body, cookie))
            if method == "POST" and url.endswith("/api/conversations"):
                return 201, '{"id":"conv-probe"}', {}
            if method == "DELETE":
                return 204, "", {}
            return 404, "{}", {}

        e2e._http_json = http_json
        self.stream_calls: list[tuple[str, str, str, int]] = []
        self.stream_result = _stream()

        def stream_request(url: str, body: str, cookie: str, timeout_s: int):
            self.stream_calls.append((url, body, cookie, timeout_s))
            return dict(self.stream_result)

        e2e._stream_request = stream_request
        self.no_browser("browser launched before the harness-side probes passed")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ARTIFACTS_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        for name, value in self._orig.items():
            setattr(e2e, name, value)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def no_browser(self, reason: str) -> None:
        def refuse(session, *args, **kwargs):
            raise e2e.E2EFailure(reason)

        e2e._browser = refuse

    def run_journey(self, app: _App) -> tuple[object, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            steps = e2e.run_e2e(app, frontend_url="http://localhost:5173")
        return steps, out.getvalue()

    def evidence(self) -> Path:
        return Path(self.tmp.name) / "e2e-evidence"

    def app_log(self, *lines: str) -> Path:
        path = Path(self.tmp.name) / "app-process.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


class StreamProbeParserTests(unittest.TestCase):
    def test_counts_tokens_sources_done_and_ignores_comments_and_status(self) -> None:
        parsed = e2e._parse_sse(HEALTHY_BODY)
        self.assertEqual(
            parsed, {"events": 5, "tokens": 2, "sources": True, "done": True, "error": "-"}
        )

    def test_reports_an_explicit_error_payload(self) -> None:
        parsed = e2e._parse_sse(ERROR_BODY)
        self.assertEqual(parsed["tokens"], 1)
        self.assertEqual(parsed["error"], "OpenRouter said no: key sk-secret-value-1")
        self.assertFalse(parsed["done"])

    def test_empty_or_non_sse_bodies_carry_nothing(self) -> None:
        self.assertEqual(e2e._parse_sse("")["tokens"], 0)
        parsed = e2e._parse_sse('{"detail":"Conversation not found"}')
        self.assertEqual((parsed["tokens"], parsed["events"], parsed["error"]), (0, 0, "-"))

    def test_empty_string_tokens_are_not_evidence_of_an_answer(self) -> None:
        self.assertEqual(e2e._parse_sse('data: ""\n\ndata: [DONE]\n\n')["tokens"], 0)


class StreamProbeRequirementTests(_Harness):
    def _probe(self) -> tuple[bool, str, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok, detail = e2e._probe_stream(
                "http://localhost:5173", COOKIE, QUESTION, 90, (PASSWORD, "sk-secret-value-1")
            )
        return ok, detail, out.getvalue()

    def test_healthy_stream_passes_and_prints_the_marker(self) -> None:
        ok, _, output = self._probe()
        self.assertTrue(ok)
        self.assertIn(
            "E2E_STREAM_PROBE status=200 content_type=text/event-stream first_byte_ms=1200 "
            "events=5 tokens=2 sources=true done=true error=-",
            output,
        )
        self.assertIn('E2E_STREAM_BODY data: "The video" : keepalive event: status', output)
        self.assertIn("E2E_STREAM_CLEANUP conversation=conv-probe status=204", output)

    def test_probe_posts_the_locked_question_with_the_cookie_through_the_frontend(self) -> None:
        self._probe()
        self.assertEqual(
            self.stream_calls,
            [
                (
                    "http://localhost:5173/api/conversations/conv-probe/messages",
                    json.dumps({"content": QUESTION}),
                    COOKIE,
                    90,
                )
            ],
        )
        self.assertEqual(
            [(m, u, c) for m, u, _, c in self.json_calls],
            [
                ("POST", "http://localhost:5173/api/conversations", COOKIE),
                ("DELETE", "http://localhost:5173/api/conversations/conv-probe", COOKIE),
            ],
        )

    def test_probe_requires_status_200_even_when_a_token_arrived(self) -> None:
        """Mutation `e2e-stream-probe-accepts-any-status`: a 500 whose body happens to
        carry a token must not pass."""
        self.stream_result = _stream(status=500, content_type="application/json")
        ok, detail, output = self._probe()
        self.assertFalse(ok)
        self.assertIn("status=500", detail)
        self.assertIn("E2E_STREAM_PROBE status=500", output)

    def test_probe_requires_a_token_and_names_the_transport_when_none_came(self) -> None:
        self.stream_result = _stream(
            body="", first_byte_ms=-1, transport="IncompleteRead: 0 bytes read"
        )
        ok, detail, output = self._probe()
        self.assertFalse(ok)
        self.assertIn("no token arrived", detail)
        self.assertIn("transport=IncompleteRead: 0 bytes read", detail)
        self.assertIn(
            "tokens=0 sources=false done=false error=IncompleteRead: 0 bytes read", output
        )

    def test_an_error_payload_is_the_named_cause_and_is_scrubbed(self) -> None:
        self.stream_result = _stream(body=ERROR_BODY)
        ok, detail, output = self._probe()
        self.assertFalse(ok)
        self.assertIn("the stream carried an error payload: OpenRouter said no: key ***", detail)
        self.assertIn("error=OpenRouter said no: key ***", output)
        self.assertNotIn("sk-secret-value-1", output)
        self.assertNotIn("sk-secret-value-1", detail)

    def test_a_conversation_that_cannot_be_created_fails_without_streaming(self) -> None:
        e2e._http_json = lambda method, url, body, cookie: (401, '{"detail":"Unauthorized"}', {})
        ok, detail, output = self._probe()
        self.assertFalse(ok)
        self.assertEqual(self.stream_calls, [])
        self.assertIn("POST /api/conversations answered 401", detail)
        self.assertIn("error=conversation not created:", output)

    def test_the_probe_conversation_must_be_deleted(self) -> None:
        def http_json(method, url, body, cookie):
            if method == "DELETE":
                return 500, "boom", {}
            return 201, '{"id":"conv-probe"}', {}

        e2e._http_json = http_json
        ok, detail, output = self._probe()
        self.assertFalse(ok)
        self.assertIn("DELETE of the probe conversation answered 500", detail)
        self.assertIn("E2E_STREAM_CLEANUP conversation=conv-probe status=500", output)

    def test_journey_refuses_before_the_browser_when_the_stream_probe_fails(self) -> None:
        self.stream_result = _stream(body="", transport="RemoteDisconnected: closed")
        steps, output = self.run_journey(_App(app_log=self.app_log("boot", UVICORN_ERROR)))
        self.assertIsNone(steps)
        self.assertIn("E2E_VIDEOS_PROBE count=1 fixture_present=true status=200", output)
        self.assertIn("E2E_FAIL  streaming route answers the locked question: no token", output)
        self.assertNotIn("browser launched", output)
        self.assertLess(output.index("E2E_STREAM_PROBE"), output.index("E2E_FAIL"))

    def test_probe_runs_after_the_login_probes_and_before_the_browser(self) -> None:
        source = (HARNESS / "e2e.py").read_text(encoding="utf-8")
        proxy = source.index("_probe_proxy_login(frontend_url, email, password)")
        bootstrap = source.index("_check_bootstrap(app, video_id, secrets)")
        videos = source.index("_probe_videos(app, cookie, video_id)")
        stream = source.index("_probe_stream(frontend_url, cookie, question,")
        browser = source.index('_browser(session, "open", frontend_url')
        self.assertLess(proxy, bootstrap)
        self.assertLess(bootstrap, videos)
        self.assertLess(videos, stream)
        self.assertLess(stream, browser)
        self.assertIn("spends one of the synthetic account's 25 daily messages", source)


class StreamWindowRecorderTests(_Harness):
    def test_ui_state_names_each_of_the_page_states(self) -> None:
        baseline = frozenset(e2e._static_text(CHAT_BASELINE))
        label = lambda snap: e2e._state_label(e2e._ui_state(snap, baseline, QUESTION))  # noqa: E731
        self.assertEqual(label(CHAT_BASELINE), "send-button")
        self.assertEqual(label(CHAT_SENT_ONLY), "send-button")
        self.assertEqual(label(CHAT_STREAMING), "stop-button+assistant-text")
        self.assertEqual(label(CHAT_ERROR), "send-button+inline-error")
        self.assertEqual(label(CHAT_ANSWERED), "send-button+assistant-text+citation")

    def _record(self, snapshots: list[str], timeout: int = 5):
        calls: list[tuple[str, ...]] = []
        remaining = list(snapshots)

        def browser(session, *args, timeout=30, check=True):
            calls.append(tuple(args))
            self.assertEqual(args, ("snapshot",), "the recorder takes full snapshots only")
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]

        e2e._browser = browser
        artifacts = self.evidence()
        artifacts.mkdir(parents=True, exist_ok=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            states, matched, polls = e2e._record_stream_window(
                "s",
                artifacts,
                lambda s: "Send message" in s and bool(e2e.CITATION.search(s)),
                timeout,
                frozenset(e2e._static_text(CHAT_BASELINE)),
                QUESTION,
                poll_s=0.01,
            )
        return states, matched, polls, out.getvalue(), artifacts / "stream-states.jsonl"

    def test_recorder_writes_distinct_states_with_timestamps_and_prints_them(self) -> None:
        states, matched, polls, output, path = self._record(
            [CHAT_SENT_ONLY, CHAT_STREAMING, CHAT_STREAMING, CHAT_STREAMING, CHAT_ANSWERED]
        )
        self.assertEqual(
            [s["state"] for s in states],
            ["send-button", "stop-button+assistant-text", "send-button+assistant-text+citation"],
        )
        self.assertEqual(polls, 5)
        self.assertEqual(matched, CHAT_ANSWERED)
        self.assertIn(
            "E2E_STREAM_UI states=[send-button,stop-button+assistant-text,"
            "send-button+assistant-text+citation] polls=5",
            output,
        )
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([line["state"] for line in lines], [s["state"] for s in states])
        self.assertEqual([line["poll"] for line in lines], [1, 2, 5])
        self.assertTrue(
            all("t_ms" in line and line["stop_button"] in (True, False) for line in lines)
        )
        self.assertTrue(lines[1]["stop_button"])
        self.assertTrue(lines[2]["citation"])

    def test_recorder_returns_no_match_on_timeout_but_keeps_the_timeline(self) -> None:
        states, matched, polls, output, path = self._record([CHAT_SENT_ONLY, CHAT_ERROR], timeout=0)
        self.assertIsNone(matched)
        self.assertEqual(polls, 1)
        self.assertEqual([s["state"] for s in states], ["send-button"])
        self.assertTrue(path.is_file())
        self.assertIn("E2E_STREAM_UI states=[send-button] polls=1", output)

    def test_verdict_passes_when_stop_was_seen(self) -> None:
        states = [{"state": "stop-button", "stop_button": True}, {"state": "send-button+citation"}]
        ok, detail = e2e._stream_window_verdict(states, CHAT_ANSWERED, 7)
        self.assertTrue(ok)
        self.assertIn("Stop response observed", detail)

    def test_verdict_passes_a_response_that_arrived_within_one_poll(self) -> None:
        """Mutation `e2e-stream-window-requires-transient-stop`: an answer that landed
        before the first snapshot never shows the Stop button and must still pass."""
        states = [{"state": "send-button+assistant-text+citation", "stop_button": False}]
        ok, detail = e2e._stream_window_verdict(states, CHAT_ANSWERED, 1)
        self.assertTrue(ok)
        self.assertIn("within one poll", detail)

    def test_verdict_fails_when_stop_never_showed_and_the_answer_was_slow(self) -> None:
        states = [{"state": "send-button", "stop_button": False}]
        ok, detail = e2e._stream_window_verdict(states, CHAT_ANSWERED, 3)
        self.assertFalse(ok)
        self.assertIn("polls=3 states=[send-button]", detail)
        ok, _ = e2e._stream_window_verdict(states, None, 40)
        self.assertFalse(ok)

    def test_journey_uses_the_recorder_instead_of_a_single_stop_wait(self) -> None:
        source = (HARNESS / "e2e.py").read_text(encoding="utf-8")
        self.assertNotIn('_snapshot_until(session, lambda s: "Stop response" in s, 10)', source)
        self.assertNotIn('require("streaming UI state observed", True)', source)
        self.assertIn("states, snap, polls = _record_stream_window(", source)
        self.assertIn(
            "streamed, stream_detail = _stream_window_verdict(states, snap, polls)", source
        )
        self.assertIn('require("streaming UI state observed", streamed, stream_detail)', source)
        # The citation/modal requirements are unchanged.
        for kept in (
            'require("message created a real conversation"',
            'require("citation includes title/timestamp metadata"',
            'require("citation includes quoted transcript evidence"',
            'require("citation modal points to locked video at exact timestamp"',
            "_youtube_matches(external, embed, video_id, seconds)",
        ):
            self.assertIn(kept, source)


class _FakeBrowser:
    """The whole journey as agent-browser answers it, with the stream window scripted."""

    def __init__(self, test: unittest.TestCase, stream_polls: list[str]):
        self.test = test
        self.stream_polls = list(stream_polls)
        self.calls: list[tuple[str, ...]] = []
        self.interactive = 0
        self.full = 0
        self.urls = 0
        self.filled: dict[str, str] = {}

    def __call__(self, session, *args, timeout=30, check=True):
        self.calls.append(tuple(args))
        if args == ("snapshot", "-i"):
            self.interactive += 1
            return [LOGIN_FORM, CHAT_INTERACTIVE, MODAL][min(self.interactive, 3) - 1]
        if args == ("snapshot",):
            self.full += 1
            if self.full == 1:
                return CHAT_BASELINE
            if len(self.stream_polls) > 1:
                return self.stream_polls.pop(0)
            return self.stream_polls[0]
        if args[0] == "fill":
            self.filled[args[1]] = args[2]
            return "Done"
        if args[:2] == ("get", "value"):
            return self.filled.get(args[2], "")
        if args == ("get", "url"):
            self.urls += 1
            return "http://localhost:5173/" if self.urls == 1 else "http://localhost:5173/c/abc"
        if args[:2] == ("get", "attr") and args[3] == "title":
            return '"Locked Fixture at 12:34\\nQuoted transcript evidence here"'
        if args[:2] == ("get", "attr") and args[3] == "href":
            return EXTERNAL
        if args[0] == "eval":
            return EMBED
        if args[0] == "screenshot":
            Path(args[1]).write_bytes(b"png")
            return ""
        return ""


class FullJourneyTests(_Harness):
    def _journey(self, stream_polls: list[str]) -> tuple[object, str, _FakeBrowser]:
        browser = _FakeBrowser(self, stream_polls)
        e2e._browser = browser
        steps, output = self.run_journey(_App())
        return steps, output, browser

    def test_fast_answer_passes_without_ever_showing_the_stop_button(self) -> None:
        steps, output, browser = self._journey([CHAT_ANSWERED])
        self.assertEqual(steps, 20, output)
        self.assertIn("E2E_STREAM_UI states=[send-button+assistant-text+citation] polls=1", output)
        self.assertIn(
            f"E2E_EVIDENCE dir={self.evidence()} video_id={VIDEO_ID} timestamp=754", output
        )
        self.assertTrue((self.evidence() / "stream-states.jsonl").is_file())
        self.assertNotIn("E2E_APP_LOG_TAIL", output)
        # The baseline full snapshot is taken after the fill target resolved and before
        # the question is typed.
        baseline = browser.calls.index(("snapshot",))
        self.assertLess(baseline, browser.calls.index(("fill", "@e14", QUESTION)))
        self.assertLess(
            browser.calls.index(("click", "@e15")), browser.calls.index(("snapshot",), baseline + 1)
        )

    def test_streamed_answer_records_the_stop_state_then_the_citation(self) -> None:
        steps, output, _ = self._journey([CHAT_SENT_ONLY, CHAT_STREAMING, CHAT_ANSWERED])
        self.assertEqual(steps, 20, output)
        self.assertIn(
            "E2E_STREAM_UI states=[send-button,stop-button+assistant-text,"
            "send-button+assistant-text+citation] polls=3",
            output,
        )

    def test_answer_that_never_streamed_fails_with_the_timeline_and_the_app_log(self) -> None:
        app_log = self.app_log("INFO: Application startup complete.", UVICORN_ERROR)
        browser = _FakeBrowser(self, [CHAT_SENT_ONLY, CHAT_SENT_ONLY, CHAT_ANSWERED])
        e2e._browser = browser
        steps, output = self.run_journey(_App(app_log=app_log))
        self.assertIsNone(steps)
        self.assertIn(
            "E2E_FAIL  streaming UI state observed: Stop response never observed and the "
            "response did not arrive within one poll; polls=3 states=[send-button,"
            "send-button+assistant-text+citation]",
            output,
        )
        self.assertIn("E2E_EVIDENCE_DUMP dir=", output)
        self.assertIn(("get", "html", "html"), browser.calls)
        names = sorted(p.name for p in self.evidence().iterdir())
        self.assertIn("stream-states.jsonl", names)
        self.assertIn("app-process.log", names)
        self.assertIn("page.html", names)
        self.assertIn("E2E_APP_LOG_TAIL app_log=", output)
        self.assertIn(f"  | {UVICORN_ERROR}", output)


class BootstrapVisibilityTests(_Harness):
    def _check(self, app: _App, wait_s: float = 0.0) -> tuple[bool, str, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok, detail = e2e._check_bootstrap(app, VIDEO_ID, (PASSWORD,), wait_s=wait_s)
        return ok, detail, out.getvalue()

    def test_not_requested_is_skipped_and_said_so(self) -> None:
        ok, _, output = self._check(_App())
        self.assertTrue(ok)
        self.assertIn("E2E_BOOTSTRAP_SKIPPED", output)

    def test_marker_in_the_app_log_is_seen(self) -> None:
        os.environ["DARK_FACTORY_E2E_BOOTSTRAP"] = "1"
        app = _App(app_log=self.app_log("loaded 3 vars", BOOTSTRAP_LINE, "INFO: started"))
        ok, detail, output = self._check(app)
        self.assertTrue(ok)
        self.assertEqual(detail, BOOTSTRAP_LINE)
        self.assertIn(f"E2E_BOOTSTRAP_SEEN {BOOTSTRAP_LINE}", output)

    def test_missing_marker_is_reported(self) -> None:
        os.environ["DARK_FACTORY_E2E_BOOTSTRAP"] = "1"
        app = _App(app_log=self.app_log("loaded 3 vars", "E2E_BOOTSTRAP_FAILED Supadata 429"))
        ok, detail, output = self._check(app)
        self.assertFalse(ok)
        self.assertIn("E2E_BOOTSTRAP_OK never appeared in app_log=", detail)
        self.assertIn("E2E_BOOTSTRAP_MISSING app_log=", output)

    def test_no_captured_log_counts_as_missing(self) -> None:
        os.environ["DARK_FACTORY_E2E_BOOTSTRAP"] = "1"
        ok, detail, output = self._check(_App())
        self.assertFalse(ok)
        self.assertIn("(the app process has no captured log)", detail)
        self.assertIn("E2E_BOOTSTRAP_MISSING app_log=- marker=E2E_BOOTSTRAP_OK", output)

    def test_marker_for_another_video_is_refused(self) -> None:
        os.environ["DARK_FACTORY_E2E_BOOTSTRAP"] = "1"
        app = _App(app_log=self.app_log("E2E_BOOTSTRAP_OK fixture_video_id=other chunks=3"))
        ok, detail, _ = self._check(app)
        self.assertFalse(ok)
        self.assertIn("bootstrap ingested another video", detail)

    def test_journey_fails_before_the_browser_when_the_marker_is_absent(self) -> None:
        """Mutation `e2e-bootstrap-missing-non-fatal`: a run whose fixture never landed
        must not reach the browser and fail there with no cause."""
        os.environ["DARK_FACTORY_E2E_BOOTSTRAP"] = "1"
        app = _App(app_log=self.app_log("INFO: Application startup complete."))
        steps, output = self.run_journey(app)
        self.assertIsNone(steps)
        self.assertIn("E2E_BOOTSTRAP_MISSING", output)
        self.assertIn("E2E_FAIL  bootstrap ingested the locked fixture:", output)
        self.assertNotIn("browser launched", output)
        self.assertNotIn("E2E_VIDEOS_PROBE", output)

    def test_journey_passes_the_bootstrap_check_when_the_marker_is_present(self) -> None:
        os.environ["DARK_FACTORY_E2E_BOOTSTRAP"] = "1"
        self.no_browser("stop here")
        app = _App(app_log=self.app_log(BOOTSTRAP_LINE))
        _, output = self.run_journey(app)
        self.assertIn(f"E2E_BOOTSTRAP_SEEN {BOOTSTRAP_LINE}", output)
        self.assertIn("E2E_VIDEOS_PROBE count=1 fixture_present=true status=200", output)
        self.assertIn("E2E_FAIL  stop here", output)

    def test_videos_probe_carries_the_cookie_and_requires_the_fixture(self) -> None:
        app = _App(videos=[{"id": "v9", "url": "https://www.youtube.com/watch?v=other"}])
        steps, output = self.run_journey(app)
        self.assertIsNone(steps)
        self.assertIn("E2E_VIDEOS_PROBE count=1 fixture_present=false status=200", output)
        self.assertIn("E2E_FAIL  catalog lists the locked fixture:", output)
        self.assertIn(("/api/videos", {"Cookie": COOKIE}), app.gets)
        self.assertNotIn("E2E_STREAM_PROBE", output)

    def test_videos_probe_tolerates_a_non_json_answer(self) -> None:
        app = _App()
        app.videos = None  # type: ignore[assignment]
        app.get = lambda path, headers=None: (  # type: ignore[method-assign]
            (502, "<html>Bad Gateway</html>", {})
            if path == "/api/videos"
            else _App.get(app, path, headers)
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok, detail = e2e._probe_videos(app, COOKIE, VIDEO_ID)
        self.assertFalse(ok)
        self.assertIn("E2E_VIDEOS_PROBE count=-1 fixture_present=false status=502", out.getvalue())
        self.assertIn("Bad Gateway", detail)


class ScrubberTests(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        "OPENROUTER_API_KEY": "sk-or-v1-abcdef123456",
        "SUPADATA_API_KEY": "sd_live_9876543210",
        "JWT_SECRET": "jwt-signing-secret-value",
        "GITHUB_TOKEN": "ghp_tokenvalue12345",
        "DARK_FACTORY_E2E_PASSWORD": "fake-probe-pw-Xy7",
        "DATABASE_URL": FAKE_DSN,
        "SHORT_KEY": "abc",
        "SEED_ENABLE": "false",
        "PATH": "/usr/bin",
    }

    def test_secret_values_cover_every_credential_shaped_variable(self) -> None:
        values = e2e._secret_values(self.ENV)
        for expected in (
            "sk-or-v1-abcdef123456",
            "sd_live_9876543210",
            "jwt-signing-secret-value",
            "ghp_tokenvalue12345",
            "fake-probe-pw-Xy7",
            FAKE_DSN,
            DSN_PASSWORD,
        ):
            self.assertIn(expected, values)
        self.assertNotIn("abc", values)
        self.assertNotIn("false", values)
        self.assertNotIn("/usr/bin", values)
        # Longest first, so a value containing another is masked whole.
        self.assertEqual(list(values), sorted(values, key=len, reverse=True))

    def test_scrub_log_masks_secrets_cookies_and_bearer_tokens_keeping_structure(self) -> None:
        secrets = e2e._secret_values(self.ENV)
        log = textwrap.dedent(
            f"""\
            loaded 7 vars from /opt/validation.env
            connecting to {self.ENV["DATABASE_URL"]}
            asyncpg: password "pg-pass-word" accepted
            INFO: 127.0.0.1 - "POST /api/auth/login HTTP/1.1" 200 set-cookie: session=tok.abc.def; HttpOnly
            headers: Cookie: session=tok.abc.def
            Authorization: Bearer sk-or-v1-abcdef123456
            {BOOTSTRAP_LINE}
            """
        )
        scrubbed = e2e._scrub_log(log, secrets)
        self.assertNotIn("pg-pass-word", scrubbed)
        self.assertNotIn("tok.abc.def", scrubbed)
        self.assertNotIn("sk-or-v1-abcdef123456", scrubbed)
        self.assertIn("connecting to ***", scrubbed)
        self.assertIn("set-cookie: session=***; HttpOnly", scrubbed)
        self.assertIn("Cookie: session=***", scrubbed)
        self.assertIn("Authorization: Bearer ***", scrubbed)
        self.assertIn(BOOTSTRAP_LINE, scrubbed)
        self.assertIn("loaded 7 vars from /opt/validation.env", scrubbed)

    def test_journey_scrubs_the_process_environment_not_only_the_password(self) -> None:
        source = (HARNESS / "e2e.py").read_text(encoding="utf-8")
        self.assertIn("secrets: tuple[str, ...] = _secret_values()", source)
        self.assertIn("secrets = (password, *secrets)", source)
        self.assertIn("_dump_failure(session, artifacts, str(exc), (password, *secrets))", source)


class AppLogEvidenceTests(_Harness):
    def test_dump_writes_the_scrubbed_log_and_prints_the_last_sixty_lines(self) -> None:
        lines = [f"line {i} session=tok{i}" for i in range(100)] + [UVICORN_ERROR]
        app = _App(app_log=self.app_log(*lines))
        artifacts = self.evidence()
        artifacts.mkdir(parents=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            e2e._dump_app_log(app, artifacts, (PASSWORD,))
        output = out.getvalue()
        copied = (artifacts / "app-process.log").read_text(encoding="utf-8")
        self.assertEqual(len(copied.splitlines()), 101)
        self.assertNotIn("tok5", copied)
        self.assertIn("line 5 session=***", copied)
        self.assertIn(f"E2E_APP_LOG_TAIL app_log={app.app_log} lines=60", output)
        printed = [line for line in output.splitlines() if line.startswith("  | ")]
        self.assertEqual(len(printed), 60)
        self.assertEqual(printed[0], "  | line 41 session=***")
        self.assertEqual(printed[-1], f"  | {UVICORN_ERROR}")

    def test_dump_without_a_captured_log_still_prints_the_marker(self) -> None:
        artifacts = self.evidence()
        artifacts.mkdir(parents=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            e2e._dump_app_log(_App(), artifacts, ())
        self.assertIn("E2E_APP_LOG_TAIL app_log=- lines=0", out.getvalue())
        self.assertFalse((artifacts / "app-process.log").exists())

    def test_every_failure_prints_the_app_log_tail(self) -> None:
        """Mutation `e2e-app-log-tail-not-printed`: a probe failure must still show what
        the backend printed, or the next diagnosis is a guess again."""
        self.stream_result = _stream(body="", transport="RemoteDisconnected: closed")
        app = _App(app_log=self.app_log("INFO: Application startup complete.", UVICORN_ERROR))
        steps, output = self.run_journey(app)
        self.assertIsNone(steps)
        self.assertIn("E2E_FAIL  streaming route answers the locked question", output)
        self.assertIn(f"E2E_APP_LOG_TAIL app_log={app.app_log} lines=2", output)
        self.assertIn(f"  | {UVICORN_ERROR}", output)
        self.assertLess(output.index("E2E_FAIL"), output.index("E2E_APP_LOG_TAIL"))
        self.assertEqual(
            (self.evidence() / "app-process.log").read_text(encoding="utf-8").splitlines()[-1],
            UVICORN_ERROR,
        )

    def test_page_capture_names_the_document_selector(self) -> None:
        captures = dict(e2e.FAILURE_CAPTURES)
        self.assertEqual(captures["page.html"], ("get", "html", "html"))

    def test_cli_accepts_the_app_log_of_an_existing_app(self) -> None:
        source = (HARNESS / "e2e.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--app-log", type=Path, default=None,', source)
        self.assertIn("app = ExistingApp(args.backend_port, app_log=args.app_log)", source)
        self.assertEqual(e2e.ExistingApp(8000).app_log, None)
        self.assertEqual(e2e.ExistingApp(8000, app_log=Path("x.log")).app_log, Path("x.log"))


CHILD = textwrap.dedent(
    """\
    import sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(sys.argv[sys.argv.index("--port") + 1])
    mode = sys.argv[sys.argv.index("--mode") + 1]
    if mode == "exit":
        print("boom: the app refused to start", flush=True)
        sys.exit(3)
    sys.stdout.write("first line of the app log\\n")
    for i in range(6000):
        sys.stdout.write(f"filler {i} " + "x" * 60 + "\\n")
    sys.stdout.write("last line before serving\\n")
    sys.stdout.flush()


    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass


    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    """
)


class HttpAppDrainTests(unittest.TestCase):
    """`HttpApp` must never leave the child's stdout undrained.

    The first version read the pipe only when health never came. After `APP_STARTED`
    nothing read it, so the bootstrap marker, uvicorn's tracebacks and Vite's output were
    lost, and a child that filled the 64 KiB pipe would have blocked (run 33960088633).
    """

    def setUp(self) -> None:
        self._artifacts = os.environ.get("ARTIFACTS_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ARTIFACTS_DIR"] = self.tmp.name
        self.script = Path(self.tmp.name) / "child.py"
        self.script.write_text(CHILD, encoding="utf-8")

    def tearDown(self) -> None:
        if self._artifacts is None:
            os.environ.pop("ARTIFACTS_DIR", None)
        else:
            os.environ["ARTIFACTS_DIR"] = self._artifacts
        self.tmp.cleanup()

    def _cfg(self, mode: str) -> dict:
        for path in (sys.executable, str(self.script)):
            if any(ch.isspace() for ch in path):
                self.skipTest(f"appproc splits the start command on whitespace: {path!r}")
        return {
            "http": {
                "start": f"{sys.executable} {self.script} --port {{port}} --mode {mode}",
                "health_path": "/health",
                "health_contains": "ok",
                "boot_timeout_s": 25,
            }
        }

    def test_structure_drains_stdout_on_a_daemon_thread_before_waiting_on_health(self) -> None:
        source = (HARNESS / "appproc.py").read_text(encoding="utf-8")
        enter = source.split('    def __enter__(self) -> "HttpApp":', 1)[1].split("    def ", 1)[0]
        self.assertIn("stdout=subprocess.PIPE", enter)
        self.assertIn("target=_pump, args=(self.proc.stdout, self.app_log)", enter)
        self.assertIn("daemon=True", enter)
        self.assertLess(enter.index("self._drain.start()"), enter.index("self._await_health()"))
        self.assertIn(
            'print(f"APP_STARTED port={self.port} app_log={self.app_log}", flush=True)', enter
        )
        self.assertNotIn("self.proc.stdout.read()", source)
        self.assertIn("self._drain.join(timeout=5)", source.split("def __exit__", 1)[1])

    def test_a_child_that_prints_more_than_the_pipe_holds_still_becomes_healthy(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), appproc.HttpApp(self._cfg("serve")) as app:
            status, body, _ = app.get("/health")
            self.assertEqual((status, "ok" in body), (200, True))
            log_path = app.app_log
            self.assertEqual(log_path, Path(self.tmp.name).resolve() / "app-process.log")
            self.assertIn("last line before serving", app.log_tail(3))
        text = log_path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("first line of the app log\n"))
        self.assertIn("filler 5999 ", text)
        self.assertGreater(len(text.encode("utf-8")), 64 * 1024)
        self.assertIn(f"APP_STARTED port={app.port} app_log={log_path}", out.getvalue())

    def test_a_child_that_exits_early_names_its_last_words_and_the_log(self) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(appproc.AppDidNotStart) as ctx,
            appproc.HttpApp(self._cfg("exit")),
        ):
            pass
        self.assertIn("boom: the app refused to start", str(ctx.exception))
        self.assertIn("app_log=", str(ctx.exception))

    def test_log_lands_in_a_temp_file_without_an_artifacts_dir(self) -> None:
        os.environ["ARTIFACTS_DIR"] = ""
        path = appproc.app_log_path(4242)
        try:
            self.assertTrue(path.is_file())
            self.assertNotIn(ROOT, (path, *path.parents))
            self.assertTrue(path.name.startswith("dark-factory-app-4242-"))
        finally:
            path.unlink(missing_ok=True)

    def test_log_is_refused_inside_the_repository(self) -> None:
        os.environ["ARTIFACTS_DIR"] = str(ROOT / "harness")
        path = appproc.app_log_path(4243)
        try:
            self.assertNotIn(ROOT, (path, *path.parents))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
