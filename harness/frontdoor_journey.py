#!/usr/bin/env python3
"""A browser journey over the real Front Door for the decision graph (SPECIFICATION 10, WP10B).

Serves `FrontDoorApplication` on a loopback port over a disposable intent store that already
holds one approved scope, drives a real browser with agent-browser (the same tool the canonical
E2E journey uses): unlock with the owner token, wait for the decision graph's list alternative
to show the recorded decision, read the Prove view's blocker line, then stop the service, start
a fresh one over the same directory, reload and read the same rows again. The journey passes
only when the graph after the restart is the graph before it, reconstructed from the store
alone, and no green state was shown for an unproven claim.

This is a local maintainer fixture, not a rung of the canonical ladder: it needs agent-browser
on PATH and a browser, makes no paid call, touches no remote system, and prints one
`FRONTDOOR_JOURNEY_PASSED` or `FRONTDOOR_JOURNEY_FAILED` line. Nothing here is proof of anything
but the page's behaviour in that browser.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import Mock
from wsgiref.simple_server import WSGIServer, make_server

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.frontdoor_http import FrontDoorApplication  # noqa: E402
from factory_kernel.frontdoor_intent import IntentStore  # noqa: E402
from tests.factory.test_frontdoor_intent import OWNER, REPO  # noqa: E402  (the fixture owner the store is configured for)


class JourneyFailure(RuntimeError):
    pass


class _Quiet(WSGIServer):
    def handle_error(self, request, client_address):  # pragma: no cover - browser disconnects
        pass


def _serve(store: IntentStore, token: str, port: int) -> tuple[WSGIServer, threading.Thread]:
    github = Mock()
    github.programme_issues.return_value = []
    app = FrontDoorApplication(store=store, project="citations", token=token, origin=f"http://127.0.0.1:{port}",
                               github=github, labels={}, app_login="factory[bot]")
    server = make_server("127.0.0.1", port, app, server_class=_Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _decide(store: IntentStore) -> None:
    """record-intent, propose-spec, approve-spec through the store's own command path."""
    from tests.factory.test_project_graph_http import ProjectGraphHTTPTests

    fixture = ProjectGraphHTTPTests()
    fixture.store = store
    fixture.github = Mock(); fixture.github.programme_issues.return_value = []
    fixture.app = FrontDoorApplication(store=store, project="citations", token="c" * 64, origin="https://factory.example",
                                       github=fixture.github, labels={}, app_login="factory[bot]")
    fixture.decide()


def _browser(session: str, *args: str, timeout: int = 40) -> str:
    """One agent-browser command. Output goes to a log file, never a pipe: the daemon that
    `open` launches inherits the handles and a captured pipe would wait on it forever
    (the canonical E2E harness does the same)."""
    executable = shutil.which("agent-browser")
    if executable is None:
        raise JourneyFailure("agent-browser is not on PATH")
    fd, name = tempfile.mkstemp(prefix="df-frontdoor-journey-", suffix=".log")
    os.close(fd)
    log = Path(name)
    try:
        with log.open("w", encoding="utf-8") as handle:
            proc = subprocess.run([executable, "--session", session, *args], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                  text=True, timeout=timeout)
        output = log.read_text(encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise JourneyFailure(f"agent-browser {' '.join(args)} failed rc={proc.returncode}: {output[-1200:]}")
        return output
    finally:
        try:
            log.unlink(missing_ok=True)
        except OSError:
            pass  # the daemon may still hold the handle on Windows; the OS reclaims it


def _wait_for(session: str, predicate, timeout: int) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _browser(session, "snapshot", "-i")
        if predicate(last):
            return last
        time.sleep(0.5)
    raise JourneyFailure(f"page state did not appear in {timeout}s; last snapshot tail: {last[-1500:]}")


def _unlock(session: str, snapshot: str, token_ref: str, token: str) -> None:
    """Type the token into the field and press the unlock button, by their refs (`@ref`, as the
    canonical harness addresses elements)."""
    button = next((m.group(1) for m in re.finditer(r'button "Unlock workspace"[^\n]*?\bref=([^\]\s,]+)', snapshot)), None)
    if button is None:
        raise JourneyFailure("no unlock button found: " + snapshot[-800:])
    _browser(session, "fill", f"@{token_ref}", token)
    _browser(session, "click", f"@{button}")


def _graph_rows(snapshot: str) -> list[str]:
    """The rows of the graph's list alternative as the browser reports them (cells joined)."""
    rows = []
    for line in snapshot.splitlines():
        if re.search(r'\b(owner-decision|specification|proposal|requirement)\b', line):
            rows.append(line.strip())
    return rows


def run(port: int, keep_session: bool = False) -> dict:
    token = secrets.token_hex(32)
    session = f"frontdoor-journey-{secrets.token_hex(4)}"
    with tempfile.TemporaryDirectory(prefix="frontdoor-journey-") as tmp:
        directory = Path(tmp) / "store"
        store = IntentStore(directory, repository=REPO, owner=OWNER.identity)
        _decide(store)
        server, thread = _serve(store, token, port)
        try:
            url = f"http://127.0.0.1:{port}/"
            _browser(session, "open", url)
            snap = _wait_for(session, lambda s: "Access token" in s, 20)
            ref = next((m.group(1) for m in re.finditer(r'textbox "Access token"[^\n]*?\bref=([^\]\s,]+)', snap)), None)
            if ref is None:
                raise JourneyFailure("no access token field found: " + snap[-800:])
            _unlock(session, snap, ref, token)
            before = _wait_for(session, lambda s: "owner-decision" in s, 30)
            rows_before = _graph_rows(before)
            if not any("approve-spec" in row for row in rows_before):
                raise JourneyFailure("the recorded decision did not appear in the graph list: " + before[-1500:])
            if any("current" in row and "requirement" in row for row in rows_before):
                raise JourneyFailure("a requirement was shown current without proof")
        finally:
            server.shutdown()
            thread.join(timeout=5)
        # Restart: a fresh service over the same directory, nothing carried in memory.
        restarted = IntentStore(directory, repository=REPO, owner=OWNER.identity)
        server, thread = _serve(restarted, token, port)
        try:
            _browser(session, "open", url)
            snap = _wait_for(session, lambda s: "Access token" in s, 20)
            ref = next((m.group(1) for m in re.finditer(r'textbox "Access token"[^\n]*?\bref=([^\]\s,]+)', snap)), None)
            if ref is None:
                raise JourneyFailure("no access token field found after restart: " + snap[-800:])
            _unlock(session, snap, ref, token)
            after = _wait_for(session, lambda s: "owner-decision" in s, 30)
            rows_after = _graph_rows(after)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            if not keep_session:
                try:
                    _browser(session, "close", timeout=15)
                except JourneyFailure:
                    pass
    if rows_before != rows_after:
        raise JourneyFailure(f"graph rows differ after restart: {rows_before} != {rows_after}")
    return {"rows": rows_before, "restarted_equal": True, "port": port}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keep-session", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.port, keep_session=args.keep_session)
    except (JourneyFailure, subprocess.TimeoutExpired, OSError) as exc:
        print(f"FRONTDOOR_JOURNEY_FAILED {type(exc).__name__}: {exc}")
        return 1
    print("FRONTDOOR_JOURNEY_PASSED " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
