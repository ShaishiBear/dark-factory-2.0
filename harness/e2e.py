#!/usr/bin/env python3
"""Canonical DynaChat E2E journey.

This is the repository-owned behavioral floor used by the full harness. It preserves the
live API/auth assertions and then drives the real frontend with agent-browser: login,
new conversation, streaming response, citation metadata, and the exact YouTube timestamp
link/modal for the locked validation fixture.

Screenshots are evidence only. Every pass/fail decision below is deterministic text/URL
state, not an AI interpretation of pixels.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))
REF = re.compile(r"\[ref=(e\d+)\]")
CITATION = re.compile(r'button\s+"(?P<label>\d+:\d{2}\s+—\s+[^\"]+)".*\[ref=(?P<ref>e\d+)\]')


class E2EFailure(RuntimeError):
    pass


def _ref(snapshot: str, contains: str) -> str:
    for line in snapshot.splitlines():
        if contains in line:
            match = REF.search(line)
            if match:
                return match.group(1)
    raise E2EFailure(f"interactive element not found: {contains!r}")


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
    configured = os.environ.get("ARTIFACTS_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if ROOT not in (candidate, *candidate.parents):
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
    path = Path(tempfile.gettempdir()) / f"dark-factory-e2e-{session}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _browser(session: str, *args: str, timeout: int = 30, check: bool = True) -> str:
    if shutil.which("agent-browser") is None:
        raise E2EFailure("agent-browser is not on PATH")
    fd, name = tempfile.mkstemp(prefix="df-agent-browser-", suffix=".log")
    os.close(fd)
    log = Path(name)
    try:
        with log.open("w", encoding="utf-8") as handle:
            proc = subprocess.run(
                ["agent-browser", "--session", session, *args],
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
        log.unlink(missing_ok=True)


def _snapshot_until(session: str, predicate, timeout: int) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _browser(session, "snapshot", "-i", timeout=20)
        if predicate(last):
            return last
        time.sleep(0.35)
    raise E2EFailure(f"browser state did not appear in {timeout}s; last snapshot: {last[-1200:]}")


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


def run_e2e(app) -> int | None:
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

        from serve import frontend_port_file

        port_file = frontend_port_file(app.port)
        require("frontend rendezvous exists", port_file.is_file(), str(port_file))
        frontend_port = int(port_file.read_text(encoding="utf-8").strip())
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        email, password = _load_validation_env()

        session = f"df-{os.getpid()}-{app.port}"
        artifacts = _artifact_dir(session)
        try:
            _browser(session, "open", frontend_url, timeout=30)
            snap = _snapshot_until(session, lambda s: "Email" in s and "Password" in s, 15)
            email_ref = _ref(snap, "Email")
            password_ref = _ref(snap, "Password")
            login_ref = _ref(snap, 'button "Log in"')
            _browser(session, "fill", f"@{email_ref}", email)
            _browser(session, "fill", f"@{password_ref}", password)
            _browser(session, "click", f"@{login_ref}")

            snap = _snapshot_until(
                session, lambda s: "Ask anything about the video library" in s, 20)
            url = _scalar(_browser(session, "get", "url"))
            require("login reaches new-conversation surface", urlparse(url).path == "/", url)
            _browser(session, "screenshot", str(artifacts / "authenticated.png"))

            input_ref = _ref(snap, "Ask anything about the video library")
            send_ref = _ref(snap, "Send message")
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
            youtube_ref = _ref(modal, "Open on YouTube")
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
        finally:
            try:
                _browser(session, "close", timeout=15, check=False)
            except E2EFailure:
                pass

        return steps
    except (E2EFailure, OSError, ValueError) as exc:
        print(f"  E2E_FAIL  {exc}", flush=True)
        return None
