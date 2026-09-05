#!/usr/bin/env python3
"""Canonical DynaChat E2E journey.

This is the repository-owned behavioral floor used by the full harness. It preserves the
live API/auth assertions and then drives the real frontend with agent-browser: login,
new conversation, streaming response, citation metadata, and the exact YouTube timestamp
link/modal for the locked validation fixture.

Screenshots are evidence only. Every pass/fail decision below is deterministic text/URL
state, not an AI interpretation of pixels.

The normal full harness owns process startup and calls ``run_e2e(app)`` in-process. The
standalone CLI exists only so the independent PR validator can run this SAME journey
against an app it already started. That prevents a second browser specification from
drifting in the workflow.
"""
from __future__ import annotations

import argparse
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


class E2EFailure(RuntimeError):
    pass


class ExistingApp:
    """Small network adapter for validator-owned app processes.

    The full harness passes its richer appproc driver instead. Keeping this adapter here
    means the behavioral assertions still have exactly one implementation: ``run_e2e``.
    """

    def __init__(self, port: int):
        self.port = port

    def _request(self, method: str, path: str, body: str | None = None):
        data = body.encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
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

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, body: str):
        return self._request("POST", path, body)


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


def _session_cookie_issued(headers: dict[str, str]) -> bool:
    return any(
        key.lower() == "set-cookie" and "session=" in str(value)
        for key, value in headers.items()
    )


def _post_json(url: str, body: str) -> tuple[int, str, dict[str, str]]:
    """POST a JSON body to an absolute URL; HTTP errors are answers, not exceptions."""
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            text = response.read().decode("utf-8", errors="replace")
            return response.status, text, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), dict(exc.headers.items())
    except (urllib.error.URLError, OSError) as exc:
        raise E2EFailure(f"POST {url} could not reach the frontend: {exc}") from exc


def _probe_login(app, email: str, password: str) -> tuple[int, bool, str]:
    """POST the validation credentials from the harness side before the browser does.

    A browser that stays on the login form cannot say why: the interactive snapshot never
    shows the alert text, and the refusal only surfaces as a predicate timeout. The route's
    own answer can. This prints the status, whether a session cookie was issued, and the
    scrubbed body, so a refusal names its cause in the log (D-047: the synthetic account's
    email failed the route's EmailStr validation with 422, and nothing said so).
    """
    body = json.dumps({"email": email, "password": password})
    status, text, headers = app.post("/api/auth/login", body)
    cookie = _session_cookie_issued(headers)
    summary = _scrub(" ".join(text.split())[:400], (password,))
    print(
        f"E2E_LOGIN_PROBE status={status} session_cookie={str(cookie).lower()} body={summary}",
        flush=True,
    )
    return status, cookie, summary


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
    ("page.html", ("get", "html")),
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

        # The route must accept the account before a browser is asked to. A refusal here
        # is the same refusal the browser would swallow, with its reason still attached.
        probe_status, probe_cookie, probe_body = _probe_login(app, email, password)
        require("backend accepts the validation account",
                probe_status == 200 and probe_cookie,
                f"status={probe_status} session_cookie={probe_cookie} body={probe_body}")

        # The page will post through the frontend origin, not to the backend port. That
        # path has its own ways to fail (proxy target, cookie pass-through); ask it too.
        proxy_status, proxy_cookie, proxy_body = _probe_proxy_login(frontend_url, email, password)
        require("frontend proxies the login to the backend",
                proxy_status == 200 and proxy_cookie,
                f"status={proxy_status} session_cookie={proxy_cookie} body={proxy_body}")

        session = f"df-{os.getpid()}-{app.port}"
        artifacts = _artifact_dir(session)
        try:
            _run_browser_journey(session, artifacts, frontend_url, email, password,
                                 question, video_id, response_timeout, require)
        finally:
            try:
                _browser(session, "close", timeout=15, check=False)
            except E2EFailure:
                pass

        return steps
    except (E2EFailure, OSError, ValueError) as exc:
        print(f"  E2E_FAIL  {exc}", flush=True)
        return None


def _run_browser_journey(session: str, artifacts: Path, frontend_url: str, email: str,
                         password: str, question: str, video_id: str,
                         response_timeout: int, require) -> None:
    try:
        _browser_journey(session, artifacts, frontend_url, email, password,
                         question, video_id, response_timeout, require)
    except E2EFailure as exc:
        _dump_failure(session, artifacts, str(exc), (password,))
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
    _browser(session, "fill", f"@{input_ref}", question)
    _browser(session, "click", f"@{send_ref}")

    _snapshot_until(session, lambda s: "Stop response" in s, 10)
    require("streaming UI state observed", True)

    snap = _snapshot_until(
        session,
        lambda s: "Send message" in s and bool(CITATION.search(s)),
        response_timeout,
    )
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
    args = parser.parse_args()

    for name, value in (("backend", args.backend_port), ("frontend", args.frontend_port)):
        if not 1 <= value <= 65535:
            print(f"E2E_ATTACH_REFUSED invalid {name} port={value}", flush=True)
            return 2

    app = ExistingApp(args.backend_port)
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
