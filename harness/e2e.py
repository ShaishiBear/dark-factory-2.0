#!/usr/bin/env python3
"""Canonical DynaChat E2E journey.

This is the repository-owned behavioral floor used by the full harness. It preserves the
live API/auth assertions and then drives the real frontend with agent-browser: login,
new conversation, streaming response, citation metadata, and the exact YouTube timestamp
link/modal for the locked validation fixture.

Screenshots are evidence only. Every pass/fail decision below is deterministic text/URL
state, not an AI interpretation of pixels.

Every boundary the browser will cross is asked from the harness side first, and each
answer is printed as a marker line: the backend login (`E2E_LOGIN_PROBE`), the frontend
proxy (`E2E_PROXY_PROBE`), the provisioned fixture (`E2E_BOOTSTRAP_SEEN`,
`E2E_VIDEOS_PROBE`) and the streaming route itself (`E2E_STREAM_PROBE`). A browser that
stalls cannot say why; the route can. On any failure the app process log is copied into the
evidence dump and its tail is printed (`E2E_APP_LOG_TAIL`), scrubbed of every secret.

The normal full harness owns process startup and calls ``run_e2e(app)`` in-process. The
standalone CLI exists only so the independent PR validator can run this SAME journey
against an app it already started. That prevents a second browser specification from
drifting in the workflow.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# The browser must open the frontend by the `localhost` name, never by 127.0.0.1.
# The application's session cookie is `Secure` (a MISSION security invariant, pinned by
# app/backend/tests/test_auth.py). Browsers store a Secure cookie only from a secure
# context; Chromium and Firefox grant that exemption to `localhost` on plain HTTP but not
# to the literal loopback address. Opened at 127.0.0.1, login returns 200, the cookie is
# discarded, /me answers 401 and the app bounces back to the login form -- exactly the
# failure the first production E2E recorded (D-046). The backend itself may still be
# addressed by 127.0.0.1; only the origin the browser sees matters.
BROWSER_ORIGIN_HOST = "localhost"
CONFIG = json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))
REF = re.compile(r"\[ref=(e\d+)\]")
CITATION = re.compile(r'button\s+"(?P<label>\d+:\d{2}\s+—\s+[^\"]+)".*\bref=(?P<ref>e\d+)\]')
# One line of an interactive snapshot: `- <role> "<accessible name>" [attr, ref=eN]`. The
# ref shares its bracket with other attributes (`[required, ref=e4]`), so it is matched as
# a word, not as `[ref=`.
NODE = re.compile(
    r'^\s*-\s*(?P<role>[a-z]+)\s+"(?P<name>(?:[^"\\]|\\.)*)"(?P<attrs>.*?)\bref=(?P<ref>e\d+)\]'
)
# Roles that only group other nodes. React delegates every event listener to its root
# element, so agent-browser lists that root first as `generic "<every visible string on the
# page>" clickable [onclick]`. Its name contains every label on the page; a substring match
# over raw snapshot lines therefore resolves any query to the root, and `fill` on that div
# reports success while the real inputs stay empty (D-049). Targets are resolved by parsed
# role and accessible name only, and a container role is never a target.
CONTAINER_ROLES = frozenset({
    "article", "banner", "complementary", "contentinfo", "dialog", "document", "form",
    "generic", "group", "list", "listitem", "main", "navigation", "none", "paragraph",
    "presentation", "region", "section",
})
# One text node of a full (non-interactive) snapshot: `- StaticText "<text>"`.
STATIC_TEXT = re.compile(r'^\s*-\s*StaticText\s+"(?P<text>(?:[^"\\]|\\.)*)"')
# The line `harness/bootstrap_e2e.py` prints once the locked fixture is ingested. It reaches
# the app log through `serve.py`, which runs the bootstrap before starting the backend.
BOOTSTRAP_MARKER = "E2E_BOOTSTRAP_OK"
# Environment values that may never reach a log: every variable named like a credential,
# the database DSN (it embeds a password) and the JWT signing secret.
SECRET_ENV_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD")
SECRET_ENV_NAMES = frozenset({"DATABASE_URL", "JWT_SECRET"})
# What the chat page renders when a send fails (ChatArea.tsx `InlineError`), so the recorder
# can tell an error state from an answer.
INLINE_ERROR_TEXTS = ("Failed to get a response", "daily message limit")
APP_LOG_TAIL_LINES = 60


class E2EFailure(RuntimeError):
    pass


class ExistingApp:
    """Small network adapter for validator-owned app processes.

    The full harness passes its richer appproc driver instead. Keeping this adapter here
    means the behavioral assertions still have exactly one implementation: ``run_e2e``.
    `app_log` names the file the owner of the process drains its output into, when it has
    one; without it the bootstrap marker cannot be read and the journey says so.
    """

    def __init__(self, port: int, app_log: Path | None = None):
        self.port = port
        self.app_log = app_log

    def _request(self, method: str, path: str, body: str | None = None,
                 headers: dict[str, str] | None = None):
        data = body.encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={
                **({"Content-Type": "application/json"} if data is not None else {}),
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                text = response.read().decode("utf-8", errors="replace")
                return response.status, text, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            return exc.code, text, dict(exc.headers.items())
        except (urllib.error.URLError, OSError) as exc:
            raise E2EFailure(f"{method} {path} could not reach backend: {exc}") from exc

    def get(self, path: str, headers: dict[str, str] | None = None):
        return self._request("GET", path, headers=headers)

    def post(self, path: str, body: str, headers: dict[str, str] | None = None):
        return self._request("POST", path, body, headers=headers)


def _nodes(snapshot: str) -> list[tuple[str, str, str]]:
    """(role, accessible name, ref) for every node line of an interactive snapshot."""
    found: list[tuple[str, str, str]] = []
    for line in snapshot.splitlines():
        match = NODE.match(line)
        if match:
            found.append((match.group("role"), match.group("name"), match.group("ref")))
    return found


def _ref(snapshot: str, role: str, name: str) -> str:
    """Resolve the ref of the node whose role is `role` and whose accessible name starts
    with `name`.

    The match is on the parsed role and name, never on the raw line: the root container's
    name carries every visible string, so a line-substring match would hand back the root
    for any query (D-049). A container role is refused as a target even when asked for.
    """
    if role in CONTAINER_ROLES:
        raise E2EFailure(f"refusing to target a container role: {role} {name!r}")
    containers: list[str] = []
    for node_role, node_name, ref in _nodes(snapshot):
        if node_role == role and node_name.startswith(name):
            return ref
        if node_role in CONTAINER_ROLES and name in node_name:
            containers.append(f"{node_role} [ref={ref}]")
    detail = f"; only containers carry that text: {', '.join(containers)}" if containers else ""
    raise E2EFailure(f"interactive element not found: {role} {name!r}{detail}")


def _citation(snapshot: str) -> tuple[str, str]:
    for line in snapshot.splitlines():
        match = CITATION.search(line)
        if match:
            return match.group("ref"), match.group("label")
    raise E2EFailure("no visible timestamped citation button")


def _timestamp_seconds(label: str) -> int:
    stamp = label.split("—", 1)[0].strip()
    mins, secs = stamp.split(":", 1)
    return int(mins) * 60 + int(secs)


def _scalar(output: str) -> str:
    value = output.strip()
    try:
        decoded = json.loads(value)
        if isinstance(decoded, str):
            return decoded
    except json.JSONDecodeError:
        pass
    return value.strip().strip('"').strip("'")


def _youtube_matches(external: str, embed: str, video_id: str, seconds: int) -> bool:
    ext = urlparse(external)
    emb = urlparse(embed)
    ext_q = parse_qs(ext.query)
    emb_q = parse_qs(emb.query)
    ext_video = ext_q.get("v", [""])[0]
    ext_time = ext_q.get("t", [""])[0]
    emb_video = emb.path.rstrip("/").split("/")[-1]
    emb_start = emb_q.get("start", [""])[0]
    return (ext_video == video_id and emb_video == video_id and
            ext_time == f"{seconds}s" and emb_start == str(seconds))


def _artifact_dir(session: str) -> Path:
    """Where screenshots and the failure dump go.

    Under the run's `ARTIFACTS_DIR` this is the `e2e-evidence/` subdirectory, which both the
    worker and the main-regression workflow upload; without one it is a temp directory that
    survives only on the host (D-049: a dump written to /tmp on a hosted runner is lost).
    """
    configured = os.environ.get("ARTIFACTS_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve() / "e2e-evidence"
        if ROOT not in (candidate, *candidate.parents):
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
    path = Path(tempfile.gettempdir()) / f"dark-factory-e2e-{session}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _browser(session: str, *args: str, timeout: int = 30, check: bool = True) -> str:
    executable = shutil.which("agent-browser")
    if executable is None:
        raise E2EFailure("agent-browser is not on PATH")
    fd, name = tempfile.mkstemp(prefix="df-agent-browser-", suffix=".log")
    os.close(fd)
    log = Path(name)
    try:
        with log.open("w", encoding="utf-8") as handle:
            # The resolved path, not the bare name: on Windows the CLI is a `.cmd` shim that
            # CreateProcess cannot find by name, so a maintainer's local run dies before the
            # first browser command (appproc.py resolves its commands the same way).
            proc = subprocess.run(
                [executable, "--session", session, *args],
                cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                text=True, timeout=timeout,
            )
        output = log.read_text(encoding="utf-8", errors="replace")
        if check and proc.returncode != 0:
            raise E2EFailure(
                f"agent-browser {' '.join(args)} failed rc={proc.returncode}: {output[-1200:]}")
        return output
    except subprocess.TimeoutExpired as exc:
        raise E2EFailure(f"agent-browser {' '.join(args)} timed out after {timeout}s") from exc
    finally:
        try:
            log.unlink(missing_ok=True)
        except OSError:
            # The daemon that `open` launches inherits the handle; Windows refuses to unlink
            # an open file. One small log per session is left for the OS to reclaim.
            pass


def _snapshot_until(session: str, predicate, timeout: int) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _browser(session, "snapshot", "-i", timeout=20)
        if predicate(last):
            return last
        time.sleep(0.35)
    raise E2EFailure(f"browser state did not appear in {timeout}s; last snapshot: {last[-1200:]}")


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _scrub_cookie_values(text: str) -> str:
    text = re.sub(r'("value"\s*:\s*)"[^"]*"', r'\1"***"', text)
    return re.sub(r"^(\s*[\w.-]+)=\S+", r"\1=***", text, flags=re.MULTILINE)


def _secret_values(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """Every environment value that must never appear in printed or uploaded evidence.

    Any variable whose name ends in `_KEY`, `_SECRET`, `_TOKEN` or `_PASSWORD`, plus
    `DATABASE_URL` and `JWT_SECRET`, and the password embedded in the DSN on its own (a
    log line quotes the DSN, or asyncpg quotes only the password). Values shorter than
    six characters are skipped: replacing every `true` in a log is not scrubbing. Longest
    first, so a value that contains another is masked whole.
    """
    env = os.environ if environ is None else environ
    found: set[str] = set()
    for name, value in env.items():
        upper = name.upper()
        secret_name = upper in SECRET_ENV_NAMES or upper.endswith(SECRET_ENV_SUFFIXES)
        if secret_name and value and len(value) >= 6:
            found.add(value)
    try:
        dsn_password = urlparse(env.get("DATABASE_URL", "")).password
    except ValueError:
        dsn_password = None
    if dsn_password and len(dsn_password) >= 6:
        found.add(dsn_password)
    return tuple(sorted(found, key=len, reverse=True))


def _scrub_log(text: str, secrets: tuple[str, ...]) -> str:
    """Scrub an app-process log: the secret values, every session cookie value, and any
    bearer token, while keeping the line structure the reader needs."""
    text = _scrub(text, secrets)
    text = re.sub(r"(?i)(\bsession=)[^;\s\"']+", r"\1***", text)
    return re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1***", text)


def _session_cookie_issued(headers: dict[str, str]) -> bool:
    return any(
        key.lower() == "set-cookie" and "session=" in str(value)
        for key, value in headers.items()
    )


def _session_cookie(headers: dict[str, str]) -> str:
    """The `session=<token>` pair from a Set-Cookie header, for replaying as `Cookie`."""
    for key, value in headers.items():
        if key.lower() == "set-cookie":
            match = re.search(r"\bsession=([^;\s]+)", str(value))
            if match:
                return f"session={match.group(1)}"
    return ""


def _post_json(url: str, body: str) -> tuple[int, str, dict[str, str]]:
    """POST a JSON body to an absolute URL; HTTP errors are answers, not exceptions."""
    return _http_json("POST", url, body, "")


def _http_json(method: str, url: str, body: str | None,
               cookie: str) -> tuple[int, str, dict[str, str]]:
    """One JSON request to an absolute URL with the session cookie; HTTP errors are
    answers, not exceptions."""
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(
        url, data=body.encode("utf-8") if body is not None else None,
        method=method, headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            text = response.read().decode("utf-8", errors="replace")
            return response.status, text, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), dict(exc.headers.items())
    except (urllib.error.URLError, OSError) as exc:
        raise E2EFailure(f"{method} {url} could not reach the frontend: {exc}") from exc


def _probe_login(app, email: str, password: str) -> tuple[int, bool, str, str]:
    """POST the validation credentials from the harness side before the browser does.

    A browser that stays on the login form cannot say why: the interactive snapshot never
    shows the alert text, and the refusal only surfaces as a predicate timeout. The route's
    own answer can. This prints the status, whether a session cookie was issued, and the
    scrubbed body, so a refusal names its cause in the log (D-047: the synthetic account's
    email failed the route's EmailStr validation with 422, and nothing said so). The
    issued cookie is returned so the later probes can speak as the signed-in account.
    """
    body = json.dumps({"email": email, "password": password})
    status, text, headers = app.post("/api/auth/login", body)
    cookie = _session_cookie_issued(headers)
    summary = _scrub(" ".join(text.split())[:400], (password,))
    print(
        f"E2E_LOGIN_PROBE status={status} session_cookie={str(cookie).lower()} body={summary}",
        flush=True,
    )
    return status, cookie, summary, _session_cookie(headers)


def _probe_proxy_login(frontend_url: str, email: str, password: str) -> tuple[int, bool, str]:
    """POST the same credentials through the frontend origin the browser will use.

    The page posts a relative `/api/auth/login`, which the Vite dev server proxies to
    `VITE_API_TARGET`. That is a second boundary the backend probe never crosses: a proxy
    pointed at the wrong port, or a cookie stripped on the way back, would leave the browser
    on the form with the backend probe green (D-049). Unreachable is reported as status 0.
    """
    url = f"{frontend_url}/api/auth/login"
    body = json.dumps({"email": email, "password": password})
    try:
        status, text, headers = _post_json(url, body)
        cookie = _session_cookie_issued(headers)
    except E2EFailure as exc:
        status, text, cookie = 0, str(exc), False
    summary = _scrub(" ".join(text.split())[:400], (password,))
    print(
        f"E2E_PROXY_PROBE url={url} status={status} session_cookie={str(cookie).lower()} "
        f"body={summary}",
        flush=True,
    )
    return status, cookie, summary


def _app_log_path(app) -> Path | None:
    path = getattr(app, "app_log", None)
    return Path(path) if path else None


def _app_log_text(app) -> str:
    path = _app_log_path(app)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _bootstrap_requested() -> bool:
    return os.environ.get("DARK_FACTORY_E2E_BOOTSTRAP", "").strip() == "1"


def _check_bootstrap(app, video_id: str, secrets: tuple[str, ...],
                     wait_s: float = 5.0) -> tuple[bool, str]:
    """When the run provisioned its own fixture, the app log must say the ingestion landed.

    `serve.py` runs `bootstrap_e2e.py` before the backend and its `E2E_BOOTSTRAP_OK
    fixture_video_id=... chunks=N` line reaches the app process log. Before this the line
    was printed into a pipe nobody read (run 33960088633), so a run whose fixture never
    landed could still reach the browser and fail there with no cause. Prints
    `E2E_BOOTSTRAP_SEEN <line>`, or `E2E_BOOTSTRAP_MISSING` when the marker is absent from
    the log, when the log cannot be read, or when it names a different video.
    """
    if not _bootstrap_requested():
        print("E2E_BOOTSTRAP_SKIPPED DARK_FACTORY_E2E_BOOTSTRAP is not 1; the fixture must "
              "already exist", flush=True)
        return True, "not requested"
    deadline = time.monotonic() + wait_s
    while True:
        for line in _app_log_text(app).splitlines():
            if line.strip().startswith(BOOTSTRAP_MARKER + " "):
                seen = _scrub(line.strip(), secrets)
                print(f"E2E_BOOTSTRAP_SEEN {seen}", flush=True)
                matches = f"fixture_video_id={video_id}" in seen
                return matches, seen if matches else f"bootstrap ingested another video: {seen}"
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    path = _app_log_path(app)
    detail = (f"{BOOTSTRAP_MARKER} never appeared in app_log={path or '-'}"
              + ("" if path else " (the app process has no captured log)"))
    print(f"E2E_BOOTSTRAP_MISSING app_log={path or '-'} marker={BOOTSTRAP_MARKER}", flush=True)
    return False, detail


def _probe_videos(app, cookie: str, video_id: str) -> tuple[bool, str]:
    """GET /api/videos as the signed-in account; the locked fixture must be listed.

    The bootstrap marker proves the ingestion ran; this proves the row the browser will be
    asked about is in the database the app is serving from. Prints
    `E2E_VIDEOS_PROBE count=N fixture_present=<bool> status=<n>`.
    """
    headers = {"Cookie": cookie} if cookie else None
    status, text, _ = app.get("/api/videos", headers=headers)
    count, present = -1, False
    try:
        videos = json.loads(text)
    except json.JSONDecodeError:
        videos = None
    if isinstance(videos, list):
        count = len(videos)
        present = any(
            isinstance(video, dict)
            and (video_id in str(video.get("url", ""))
                 or video.get("youtube_video_id") == video_id)
            for video in videos
        )
    print(f"E2E_VIDEOS_PROBE count={count} fixture_present={str(present).lower()} "
          f"status={status}", flush=True)
    return status == 200 and present, (
        f"status={status} count={count} fixture_present={present} "
        f"body={' '.join(text.split())[:200]}")


def _parse_sse(body: str) -> dict:
    """Count what a `text/event-stream` body carried, in the framing the frontend parses.

    Tokens are unnamed `data:` frames holding a JSON string (`data: "tok"`, footgun 3 in
    CLAUDE.md); `event: sources` precedes `data: [DONE]`; a mid-stream failure is an unnamed
    frame holding `{"error": ...}` (llm/openrouter.py). Comment lines (`: keepalive`) and
    `event: status` frames count as events, never as tokens.
    """
    events = tokens = 0
    sources = done = False
    error = "-"
    for frame in re.split(r"\n\n+", body):
        name = ""
        data: list[str] = []
        for line in frame.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].strip())
        if not data:
            continue
        events += 1
        payload = "\n".join(data)
        if name == "sources":
            sources = True
            continue
        if name:
            continue
        if payload == "[DONE]":
            done = True
            continue
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, str):
            if decoded:
                tokens += 1
        elif isinstance(decoded, dict) and "error" in decoded:
            error = str(decoded["error"])
    return {"events": events, "tokens": tokens, "sources": sources, "done": done,
            "error": error}


def _stream_request(url: str, body: str, cookie: str, timeout_s: int) -> dict:
    """POST and read the response body incrementally until `[DONE]`, EOF or the deadline.

    Plain `urllib` would buffer the whole body and could not tell first byte from last;
    `http.client` exposes the socket, so the per-read timeout is the remaining budget.
    Returns raw facts only: status, content type, milliseconds to the first body byte, the
    body read so far, and a transport error (`''` when the stream ended on its own).
    """
    parsed = urlparse(url)
    out = {"status": 0, "content_type": "-", "first_byte_ms": -1, "body": "", "transport": ""}
    started = time.monotonic()
    deadline = started + timeout_s
    pieces: list[str] = []
    # The whole budget is the socket timeout: the model may run several tool rounds
    # before its first token, and a shorter per-read timeout would report a slow answer as
    # a broken one. Headers arrive at once (StreamingResponse), so `getresponse` is quick.
    conn = http.client.HTTPConnection(parsed.hostname or "localhost", parsed.port,
                                      timeout=timeout_s)
    try:
        conn.request("POST", parsed.path, body=body.encode("utf-8"), headers={
            "Content-Type": "application/json", "Accept": "text/event-stream",
            **({"Cookie": cookie} if cookie else {}),
        })
        response = conn.getresponse()
        out["status"] = response.status
        out["content_type"] = (response.getheader("Content-Type") or "-").split(";")[0].strip()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                out["transport"] = f"deadline of {timeout_s}s elapsed before [DONE]"
                break
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            line = response.readline()
            if not line:
                break
            if out["first_byte_ms"] < 0:
                out["first_byte_ms"] = int((time.monotonic() - started) * 1000)
            pieces.append(line.decode("utf-8", errors="replace"))
            if line.strip() == b"data: [DONE]":
                break
    except (OSError, http.client.HTTPException) as exc:
        out["transport"] = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    out["body"] = "".join(pieces)
    return out


def _probe_stream(frontend_url: str, cookie: str, question: str, timeout_s: int,
                  secrets: tuple[str, ...]) -> tuple[bool, str]:
    """Ask the streaming route the journey's question from the harness side first.

    The browser's fetch of `POST /api/conversations/<id>/messages` ended with no status
    in the network log, no assistant text and no inline error (run 33960088633); the
    page cannot say whether the route refused, the stream broke before its first token,
    or the model answered without a citation. This creates a conversation through the
    frontend origin with the login cookie, posts the locked question, reads the SSE body
    as it arrives, prints `E2E_STREAM_PROBE status=<n> content_type=<ct> first_byte_ms=<n>
    events=<n> tokens=<n> sources=<bool> done=<bool> error=<payload or ->` and the first
    300 scrubbed characters of the body, then deletes the conversation so the browser
    journey still starts on an empty landing surface.

    The probe passes only when the route answered 200 and streamed at least one token
    without an error payload; an explicit `{"error": ...}` frame fails the probe with that
    error as the named cause, and a stream that closed with no token names the transport.

    This spends one of the synthetic account's 25 daily messages (MISSION §10 invariant
    #1); the validation database is disposable, so the counter starts at zero every run.
    """
    status, text, _ = _http_json("POST", f"{frontend_url}/api/conversations", "{}", cookie)
    conversation_id = ""
    try:
        decoded = json.loads(text)
        conversation_id = str(decoded.get("id") or "") if isinstance(decoded, dict) else ""
    except json.JSONDecodeError:
        pass
    if status not in (200, 201) or not conversation_id:
        reason = _scrub(" ".join(text.split())[:300], secrets)
        print(f"E2E_STREAM_PROBE status={status} content_type=- first_byte_ms=-1 events=0 "
              f"tokens=0 sources=false done=false error=conversation not created: {reason}",
              flush=True)
        return False, f"POST /api/conversations answered {status}: {reason}"

    raw = _stream_request(
        f"{frontend_url}/api/conversations/{conversation_id}/messages",
        json.dumps({"content": question}), cookie, timeout_s,
    )
    parsed = _parse_sse(raw["body"])
    cause = parsed["error"] if parsed["error"] != "-" else (raw["transport"] or "-")
    if cause != "-":
        cause = _scrub(" ".join(cause.split()), secrets)[:300]
    excerpt = _scrub(" ".join(raw["body"].split())[:300], secrets)
    print(
        f"E2E_STREAM_PROBE status={raw['status']} content_type={raw['content_type']} "
        f"first_byte_ms={raw['first_byte_ms']} events={parsed['events']} "
        f"tokens={parsed['tokens']} sources={str(parsed['sources']).lower()} "
        f"done={str(parsed['done']).lower()} error={cause}",
        flush=True,
    )
    print(f"E2E_STREAM_BODY {excerpt}", flush=True)

    delete_status, _, _ = _http_json(
        "DELETE", f"{frontend_url}/api/conversations/{conversation_id}", None, cookie)
    print(f"E2E_STREAM_CLEANUP conversation={conversation_id} status={delete_status}",
          flush=True)

    ok = raw["status"] == 200 and parsed["tokens"] >= 1 and parsed["error"] == "-"
    if parsed["error"] != "-":
        detail = f"the stream carried an error payload: {cause}"
    elif raw["status"] != 200:
        detail = f"status={raw['status']} body={excerpt}"
    else:
        detail = (f"no token arrived: events={parsed['events']} done={parsed['done']} "
                  f"first_byte_ms={raw['first_byte_ms']} transport={cause} body={excerpt}")
    if delete_status != 204:
        ok = False
        detail += f"; DELETE of the probe conversation answered {delete_status}"
    return ok, detail


def _static_text(snapshot: str) -> list[str]:
    """The text nodes of a full (non-interactive) snapshot."""
    return [m.group("text") for line in snapshot.splitlines()
            if (m := STATIC_TEXT.match(line))]


def _ui_state(snapshot: str, baseline: frozenset[str], question: str) -> dict[str, bool]:
    """Which of the chat page's states a full snapshot shows.

    `stop_button` and `send_button` are the two shapes of the input's button
    (ChatInput.tsx); `inline_error` is the failed-send block with its Retry button;
    `assistant_text` is any text node that was not on the page before the question was
    sent and is neither the question nor the error text (the streamed answer or a tool
    status label); `citation` is a timestamped citation button.
    """
    nodes = _nodes(snapshot)

    def button(name: str) -> bool:
        return any(role == "button" and text.startswith(name) for role, text, _ in nodes)

    inline_error = button("Retry") or any(text in snapshot for text in INLINE_ERROR_TEXTS)
    fresh = [
        text for text in _static_text(snapshot)
        if text not in baseline and question not in text
        and not any(marker in text for marker in INLINE_ERROR_TEXTS)
    ]
    return {
        "stop_button": button("Stop response"),
        "send_button": button("Send message"),
        "inline_error": inline_error,
        "assistant_text": bool(fresh),
        "citation": bool(CITATION.search(snapshot)),
    }


def _state_label(flags: dict[str, bool]) -> str:
    return "+".join(name.replace("_", "-") for name, on in flags.items() if on) or "none"


def _record_stream_window(session: str, artifacts: Path, predicate, timeout: int,
                          baseline: frozenset[str], question: str,
                          poll_s: float = 0.5) -> tuple[list[dict], str | None, int]:
    """Snapshot the page every `poll_s` until `predicate` holds or `timeout` elapses.

    Every distinct UI state is recorded with its timestamp to
    `<artifacts>/stream-states.jsonl` and summarised as `E2E_STREAM_UI states=[...]`, so
    a stream that never showed an answer leaves a timeline instead of one final snapshot.
    Returns the states, the snapshot that satisfied the predicate (None on timeout) and
    the number of polls taken.
    """
    started = time.monotonic()
    deadline = started + timeout
    states: list[dict] = []
    polls = 0
    matched: str | None = None
    path = artifacts / "stream-states.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        while True:
            snapshot = _browser(session, "snapshot", timeout=20)
            polls += 1
            flags = _ui_state(snapshot, baseline, question)
            label = _state_label(flags)
            if not states or states[-1]["state"] != label:
                record = {"t_ms": int((time.monotonic() - started) * 1000), "poll": polls,
                          "state": label, **flags}
                states.append(record)
                handle.write(json.dumps(record) + "\n")
                handle.flush()
            if predicate(snapshot):
                matched = snapshot
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_s)
    print(f"E2E_STREAM_UI states=[{','.join(s['state'] for s in states)}] polls={polls} "
          f"file={path}", flush=True)
    return states, matched, polls


def _stream_window_verdict(states: list[dict], matched: str | None,
                           polls: int) -> tuple[bool, str]:
    """The streaming state is evidence, not a hard requirement.

    "Stop response" is transient: an answer that lands between the click and the first
    poll never shows it. Pass when the button was observed at least once OR the response
    arrived within one poll; fail when neither happened, naming the states seen.
    """
    stop_seen = any(state.get("stop_button") for state in states)
    arrived_at_once = matched is not None and polls <= 1
    labels = ",".join(state["state"] for state in states)
    if stop_seen:
        return True, f"Stop response observed; states=[{labels}]"
    if arrived_at_once:
        return True, f"response arrived within one poll; states=[{labels}]"
    return False, (f"Stop response never observed and the response did not arrive within "
                   f"one poll; polls={polls} states=[{labels}]")


def _dump_app_log(app, artifacts: Path, secrets: tuple[str, ...]) -> None:
    """Copy the scrubbed app process log into the evidence dump and print its tail.

    The backend's own `OpenRouter streaming API error` / `Unexpected error during
    streaming` lines, uvicorn's tracebacks and Vite's output live only there.
    """
    path = _app_log_path(app)
    text = _scrub_log(_app_log_text(app), secrets)
    if path is not None:
        (artifacts / "app-process.log").write_text(text, encoding="utf-8")
    tail = text.splitlines()[-APP_LOG_TAIL_LINES:]
    print(f"E2E_APP_LOG_TAIL app_log={path or '-'} lines={len(tail)}", flush=True)
    for line in tail:
        print(f"  | {line}", flush=True)


def _check_fields(session: str, fields: dict[str, tuple[str, str]]) -> tuple[bool, str]:
    """Read every filled control back and compare it with what was typed.

    `fill` on a non-input reports success (D-049: the root container took both fills and the
    form was submitted empty, which the browser's `required` validation blocked silently).
    Prints `E2E_FIELD_CHECK <label>=<bool> ...`; the detail names lengths, never values.
    """
    verdicts: dict[str, bool] = {}
    details: list[str] = []
    for ref, (label, expected) in fields.items():
        value = _scalar(_browser(session, "get", "value", f"@{ref}"))
        verdicts[label] = value == expected
        details.append(f"{label}@{ref} holds {len(value)} chars, expected {len(expected)}")
    print(
        "E2E_FIELD_CHECK " + " ".join(f"{k}={str(v).lower()}" for k, v in verdicts.items()),
        flush=True,
    )
    return all(verdicts.values()), "; ".join(details)


FAILURE_CAPTURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("url.txt", ("get", "url")),
    ("snapshot.txt", ("snapshot",)),
    # `get html` needs a selector; without one the CLI answers with its usage text and the
    # dump held 73 bytes of "Missing arguments" instead of the page (run 33960088633).
    # `html` is the document element, so this is the whole page.
    ("page.html", ("get", "html", "html")),
    ("console.txt", ("console",)),
    ("errors.txt", ("errors",)),
    ("network.txt", ("network", "requests")),
    ("cookies.txt", ("cookies", "get")),
)


def _dump_failure(session: str, artifacts: Path, reason: str, secrets: tuple[str, ...]) -> None:
    """Capture what the browser saw when the journey broke (D-047).

    The full (non-interactive) snapshot is the one that shows alert text. Every capture is
    best effort and scrubbed of the validation password; the dump never masks the original
    failure, it only makes the next diagnosis a read instead of a guess.
    """
    written: list[str] = []
    for name, args in FAILURE_CAPTURES:
        try:
            output = _browser(session, *args, timeout=20, check=False)
        except E2EFailure as exc:
            output = f"capture failed: {exc}"
        if name == "cookies.txt":
            output = _scrub_cookie_values(output)
        (artifacts / name).write_text(_scrub(output, secrets), encoding="utf-8")
        written.append(name)
    try:
        _browser(session, "screenshot", str(artifacts / "failure.png"), timeout=20, check=False)
        written.append("failure.png")
    except E2EFailure:
        pass
    (artifacts / "failure.txt").write_text(_scrub(reason, secrets) + "\n", encoding="utf-8")
    written.append("failure.txt")
    print(f"E2E_EVIDENCE_DUMP dir={artifacts} files={','.join(written)}", flush=True)


def _load_validation_env() -> tuple[str, str]:
    from serve import load_env_file

    env_path = Path(os.environ.get("DARK_FACTORY_VALIDATION_ENV",
                                   "/opt/dark-factory/validation.env"))
    if env_path.is_file():
        load_env_file(env_path)
    email = os.environ.get("DARK_FACTORY_E2E_EMAIL", "").strip()
    password = os.environ.get("DARK_FACTORY_E2E_PASSWORD", "")
    if not email or not password:
        raise E2EFailure("validation env lacks DARK_FACTORY_E2E_EMAIL/PASSWORD")
    return email, password


def run_e2e(app, frontend_url: str | None = None) -> int | None:
    """Return the number of deterministic assertions, or None on any broken journey."""
    steps = 0
    secrets: tuple[str, ...] = _secret_values()

    def require(name: str, ok: bool, detail: str = "") -> None:
        nonlocal steps
        steps += 1
        if not ok:
            raise E2EFailure(f"{name}: {detail}")

    try:
        # Existing live-process floor.
        status, body, _ = app.get("/api/health")
        require("health returns 200", status == 200, f"got {status}")
        require("health body reports ok", "ok" in body.lower(), body[:200])
        status, _, _ = app.get("/api/version")
        require("version endpoint answers", status == 200, f"got {status}")
        status, _, _ = app.post("/api/conversations", "{}")
        require("anonymous cannot create conversation", status in (401, 403), f"got {status}")
        status, _, _ = app.get("/api/conversations")
        require("anonymous cannot list conversations", status in (401, 403), f"got {status}")

        browser_cfg = CONFIG.get("browser", {})
        video_id = str(browser_cfg.get("fixture_video_id", "")).strip()
        question = str(browser_cfg.get("question", "")).strip()
        response_timeout = int(browser_cfg.get("response_timeout_s", 90))
        require("locked browser fixture configured", bool(video_id and question))

        if frontend_url is None:
            from serve import frontend_port_file

            port_file = frontend_port_file(app.port)
            require("frontend rendezvous exists", port_file.is_file(), str(port_file))
            frontend_port = int(port_file.read_text(encoding="utf-8").strip())
            frontend_url = f"http://{BROWSER_ORIGIN_HOST}:{frontend_port}"
        else:
            parsed_frontend = urlparse(frontend_url)
            require(
                "frontend URL is an explicit local HTTP endpoint on the secure-context host",
                parsed_frontend.scheme == "http"
                and parsed_frontend.hostname == BROWSER_ORIGIN_HOST
                and parsed_frontend.port is not None,
                frontend_url,
            )

        email, password = _load_validation_env()
        secrets = (password, *secrets)

        # The route must accept the account before a browser is asked to. A refusal here
        # is the same refusal the browser would swallow, with its reason still attached.
        probe_status, probe_cookie, probe_body, cookie = _probe_login(app, email, password)
        require("backend accepts the validation account",
                probe_status == 200 and probe_cookie,
                f"status={probe_status} session_cookie={probe_cookie} body={probe_body}")

        # The page will post through the frontend origin, not to the backend port. That
        # path has its own ways to fail (proxy target, cookie pass-through); ask it too.
        proxy_status, proxy_cookie, proxy_body = _probe_proxy_login(frontend_url, email, password)
        require("frontend proxies the login to the backend",
                proxy_status == 200 and proxy_cookie,
                f"status={proxy_status} session_cookie={proxy_cookie} body={proxy_body}")

        # The fixture the question is about must be provably there: the bootstrap's own
        # marker in the app log when this run provisioned it, and the row in the catalog.
        bootstrapped, bootstrap_detail = _check_bootstrap(app, video_id, secrets)
        require("bootstrap ingested the locked fixture", bootstrapped, bootstrap_detail)
        videos_ok, videos_detail = _probe_videos(app, cookie, video_id)
        require("catalog lists the locked fixture", videos_ok, videos_detail)

        # The streaming route is asked the same question from the harness side, so a
        # stream that breaks before its first token names its cause here instead of as
        # a browser predicate timeout.
        stream_ok, stream_detail = _probe_stream(frontend_url, cookie, question,
                                                 response_timeout, secrets)
        require("streaming route answers the locked question", stream_ok, stream_detail)

        session = f"df-{os.getpid()}-{app.port}"
        artifacts = _artifact_dir(session)
        try:
            _run_browser_journey(session, artifacts, frontend_url, email, password,
                                 question, video_id, response_timeout, require, secrets)
        finally:
            try:
                _browser(session, "close", timeout=15, check=False)
            except E2EFailure:
                pass

        return steps
    except (E2EFailure, OSError, ValueError) as exc:
        print(f"  E2E_FAIL  {exc}", flush=True)
        # Whatever broke, the app process log is the one place the backend said why.
        try:
            _dump_app_log(app, _artifact_dir(f"df-{os.getpid()}-{app.port}"), secrets)
        except (OSError, ValueError) as dump_exc:
            print(f"E2E_APP_LOG_TAIL app_log=- lines=0 (dump failed: {dump_exc})", flush=True)
        return None


def _run_browser_journey(session: str, artifacts: Path, frontend_url: str, email: str,
                         password: str, question: str, video_id: str,
                         response_timeout: int, require,
                         secrets: tuple[str, ...] = ()) -> None:
    try:
        _browser_journey(session, artifacts, frontend_url, email, password,
                         question, video_id, response_timeout, require)
    except E2EFailure as exc:
        _dump_failure(session, artifacts, str(exc), (password, *secrets))
        raise


def _browser_journey(session: str, artifacts: Path, frontend_url: str, email: str,
                     password: str, question: str, video_id: str,
                     response_timeout: int, require) -> None:
    _browser(session, "open", frontend_url, timeout=30)
    snap = _snapshot_until(session, lambda s: "Email" in s and "Password" in s, 15)
    email_ref = _ref(snap, "textbox", "Email")
    password_ref = _ref(snap, "textbox", "Password")
    login_ref = _ref(snap, "button", "Log in")
    _browser(session, "fill", f"@{email_ref}", email)
    _browser(session, "fill", f"@{password_ref}", password)
    filled, detail = _check_fields(
        session, {email_ref: ("email", email), password_ref: ("password", password)})
    require("form fields hold the credentials before submit", filled, detail)
    _browser(session, "click", f"@{login_ref}")

    snap = _snapshot_until(
        session, lambda s: "Ask anything about the video library" in s, 20)
    url = _scalar(_browser(session, "get", "url"))
    require("login reaches new-conversation surface", urlparse(url).path == "/", url)
    _browser(session, "screenshot", str(artifacts / "authenticated.png"))

    input_ref = _ref(snap, "textbox", "Ask anything about the video library")
    send_ref = _ref(snap, "button", "Send message")
    # The text on the page before the question goes out; anything that appears after it
    # (other than the question itself) is the assistant region changing.
    baseline = frozenset(_static_text(_browser(session, "snapshot", timeout=20)))
    _browser(session, "fill", f"@{input_ref}", question)
    _browser(session, "click", f"@{send_ref}")

    # Record the whole stream window rather than waiting for one transient state: the
    # first run past login showed the question, the Send button, no answer and no error,
    # and a single final snapshot could not say what happened in between.
    states, snap, polls = _record_stream_window(
        session, artifacts,
        lambda s: "Send message" in s and bool(CITATION.search(s)),
        response_timeout, baseline, question,
    )
    streamed, stream_detail = _stream_window_verdict(states, snap, polls)
    require("streaming UI state observed", streamed, stream_detail)
    require("response with a timestamped citation arrived",
            snap is not None,
            f"no citation within {response_timeout}s; {stream_detail}")
    assert snap is not None
    url = _scalar(_browser(session, "get", "url"))
    require("message created a real conversation", urlparse(url).path.startswith("/c/"), url)

    citation_ref, citation_label = _citation(snap)
    seconds = _timestamp_seconds(citation_label)
    title_attr = _scalar(_browser(session, "get", "attr", f"@{citation_ref}", "title"))
    title_lines = title_attr.splitlines()
    require("citation includes title/timestamp metadata", len(title_lines) >= 2, title_attr)
    require("citation includes quoted transcript evidence",
            len(" ".join(title_lines[1:]).strip()) >= 8, title_attr)
    _browser(session, "screenshot", str(artifacts / "citation.png"))

    _browser(session, "click", f"@{citation_ref}")
    modal = _snapshot_until(
        session, lambda s: "Video citation" in s and "Open on YouTube" in s, 15)
    youtube_ref = _ref(modal, "link", "Open on YouTube")
    external = _scalar(_browser(session, "get", "attr", f"@{youtube_ref}", "href"))
    embed = _scalar(_browser(
        session, "eval",
        "document.querySelector('iframe[title=\"YouTube video player\"]')?.getAttribute('src') || ''",
    ))
    require("citation modal points to locked video at exact timestamp",
            _youtube_matches(external, embed, video_id, seconds),
            f"external={external!r} embed={embed!r} expected={video_id}@{seconds}")
    _browser(session, "screenshot", str(artifacts / "citation-modal.png"))
    print(f"E2E_EVIDENCE dir={artifacts} video_id={video_id} timestamp={seconds}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run canonical E2E against an existing app")
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--frontend-port", type=int, required=True)
    parser.add_argument("--app-log", type=Path, default=None,
                        help="file the app owner drains the process output into; read for "
                             "the bootstrap marker and copied into the evidence dump")
    args = parser.parse_args()

    for name, value in (("backend", args.backend_port), ("frontend", args.frontend_port)):
        if not 1 <= value <= 65535:
            print(f"E2E_ATTACH_REFUSED invalid {name} port={value}", flush=True)
            return 2

    app = ExistingApp(args.backend_port, app_log=args.app_log)
    frontend_url = f"http://{BROWSER_ORIGIN_HOST}:{args.frontend_port}"
    print(
        f"E2E_ATTACH backend={args.backend_port} frontend={args.frontend_port} authority=canonical-harness",
        flush=True,
    )
    steps = run_e2e(app, frontend_url=frontend_url)
    if steps is None:
        print("GATE_FAILED: e2e", flush=True)
        return 1
    print(f"E2E_PASSED steps={steps}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
