"""Small owner-authenticated WSGI transport; no App private key or model runs in this process."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from socketserver import ThreadingMixIn
import subprocess
import threading
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .config import load_config
from .frontdoor_control import stop_status
from .frontdoor_intent import IntentRefused, IntentStore, Principal
from .frontdoor_programme import prepare_programme
from .github_cli import GitHubClient
from .programme import ProgrammeRefused, parse_json
from .programme_runtime import ProgrammeQueue

ASSETS = Path(__file__).with_name("frontdoor_static")
MAX_BODY = 250000


class FrontDoorApplication:
    def __init__(self, *, store, project, token, origin, github, labels, app_login):
        parsed = urlsplit(origin)
        if (parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password
                or not parsed.hostname or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"})):
            raise ValueError("origin must be HTTPS or loopback HTTP without a path")
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
            raise ValueError("owner token must be 32 random bytes encoded as lowercase hex")
        self.store, self.project, self.github = store, project, github
        self.labels, self.app_login = labels, app_login
        self.origin, self.host = origin, parsed.netloc
        self.token_hash = hashlib.sha256(token.encode()).digest()
        self.principal = Principal(store.owner, "owner")
        store.snapshot(project, principal=self.principal)  # validate configured project before serving

    def _authenticated(self, environ):
        authorization = environ.get("HTTP_AUTHORIZATION", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 100:
            return False
        supplied = hashlib.sha256(authorization[7:].encode()).digest()
        return hmac.compare_digest(supplied, self.token_hash)

    def _body(self, environ):
        if environ.get("CONTENT_TYPE") != "application/json" or environ.get("HTTP_TRANSFER_ENCODING"):
            raise IntentRefused("a length-bounded application/json body is required")
        length = environ.get("CONTENT_LENGTH", "")
        if not length.isdecimal() or not 0 < int(length) <= MAX_BODY:
            raise IntentRefused("invalid request body length")
        raw = environ["wsgi.input"].read(int(length))
        if len(raw) != int(length):
            raise IntentRefused("incomplete request body")
        return parse_json(raw.decode("utf-8"))

    def _snapshot(self):
        state = self.store.snapshot(self.project, principal=self.principal)
        result = {"project": self.project, "repository": self.store.repository, "intent": state}
        try:
            result["execution"] = ProgrammeQueue(self.github, "main").status(self.labels)
            result["stop"] = stop_status(self.github)
            result["observation_available"] = True
        except (RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            # Read failure must not render a clear stop or complete programme. Intake remains
            # available; no runtime effect consumes this observation as authority.
            result.update(observation_available=False, execution=None, stop=None)
        return result

    def __call__(self, environ, start_response):
        headers = [("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                   ("Referrer-Policy", "no-referrer"),
                   ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")]

        def send(status, body, content_type="application/json; charset=utf-8"):
            raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            start_response(status, headers + [("Content-Type", content_type), ("Content-Length", str(len(raw)))])
            return [raw]

        if environ.get("HTTP_HOST") != self.host or environ.get("HTTP_ORIGIN", self.origin) != self.origin:
            return send("403 Forbidden", {"error": "request origin refused"})
        method, path = environ.get("REQUEST_METHOD"), environ.get("PATH_INFO", "")
        if environ.get("QUERY_STRING"):
            return send("400 Bad Request", {"error": "query parameters are not supported"})
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/frontdoor.js": ("frontdoor.js", "text/javascript; charset=utf-8"),
                  "/frontdoor.css": ("frontdoor.css", "text/css; charset=utf-8")}
        if method == "GET" and path in assets:
            name, mime = assets[path]
            return send("200 OK", (ASSETS / name).read_bytes(), mime)
        if not self._authenticated(environ):
            return send("401 Unauthorized", {"error": "owner authentication required"})
        if method == "POST" and environ.get("HTTP_ORIGIN") != self.origin:
            return send("403 Forbidden", {"error": "same-origin command required"})
        try:
            if method == "GET" and path == "/api/snapshot":
                return send("200 OK", self._snapshot())
            if method == "POST" and path == "/api/commands":
                state = self.store.execute(self.project, self._body(environ), principal=self.principal)
                return send("200 OK", state)
            if method == "POST" and path == "/api/programme-review":
                review = prepare_programme(self.store, self.project, self._body(environ),
                                           principal=self.principal, app_login=self.app_login)
                return send("200 OK", review)
            if method == "POST" and path == "/api/stop":
                request = self._body(environ)
                if not isinstance(request, dict) or set(request) != {"request_id", "reason"}:
                    raise IntentRefused("stop request has unknown or missing fields")
                if (not isinstance(request["request_id"], str)
                        or not re.fullmatch(r"[a-f0-9]{32}", request["request_id"])
                        or not isinstance(request["reason"], str) or not request["reason"].strip()
                        or len(request["reason"]) > 2000 or "<!--" in request["reason"]):
                    raise IntentRefused("invalid stop request")
                # Fixed workflow/ref, explicit owner command. No App key on this host. The
                # workflow verifies platform owner identity and mints only the Issues effect.
                self.github.run(["workflow", "run", "dark-factory-owner-stop.yml", "-R", self.store.repository,
                                 "--ref", "main", "-f", f"request_id={request['request_id']}",
                                 "-f", f"reason={request['reason']}"])
                return send("202 Accepted", {"state": "stop-requested", "request_id": request["request_id"]})
            return send("404 Not Found", {"error": "unknown endpoint"})
        except (IntentRefused, ProgrammeRefused, UnicodeError, ValueError) as exc:
            return send("409 Conflict", {"error": str(exc)})
        except (RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError):
            return send("503 Service Unavailable", {"error": "request unavailable or outcome uncertain; reload before retrying"})


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_args):
        pass  # Never log credential-bearing requests or private intent.

    def handle(self):
        self.request.settimeout(15)
        super().handle()


class FrontDoorServer(ThreadingMixIn, WSGIServer):
    """Bound connections while allowing stop alongside a slow GitHub observation."""
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def verify_host_identity(github, owner):
    """A host dispatching the owner workflow must actually authenticate as that owner."""
    identity = github.json(["api", "user"])
    if (not isinstance(identity, dict) or identity.get("login") != owner
            or identity.get("type") != "User" or github.repository.split("/", 1)[0] != owner):
        raise ValueError("GitHub credential must resolve to the configured repository owner")


def main():
    parser = argparse.ArgumentParser(description="Loopback Front Door; use an HTTPS reverse proxy for remote access")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--app-login", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.token_file.is_symlink() or args.token_file.stat().st_size > 100:
        raise ValueError("owner token must be a small private regular file")
    token = args.token_file.read_text(encoding="utf-8").strip()
    if os.name != "nt" and args.token_file.stat().st_mode & 0o077:
        raise ValueError("owner token file must be private to its service account")
    config = load_config(Path.cwd() / ".factory/kernel.json")
    github = GitHubClient(config.repository, cwd=Path.cwd())
    verify_host_identity(github, args.owner)
    if args.state_dir.is_symlink():
        raise ValueError("intent state directory cannot be a symlink")
    store = IntentStore(args.state_dir, repository=config.repository, owner=args.owner)
    if os.name != "nt" and args.state_dir.stat().st_mode & 0o077:
        raise ValueError("intent state directory must be private to its service account")
    app = FrontDoorApplication(store=store, project=args.project, token=token, origin=args.origin,
                               github=github, labels=config.labels, app_login=args.app_login)
    # Single-host, bounded request timeout, loopback only. Hosting must enforce HTTPS and keep
    # state/token files outside all worker sandboxes. This is not a distributed service.
    with make_server("127.0.0.1", args.port, app, server_class=FrontDoorServer, handler_class=QuietHandler) as server:
        print(f"FRONT_DOOR_LISTENING loopback_port={args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
