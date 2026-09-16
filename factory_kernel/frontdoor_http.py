"""Small owner-authenticated WSGI transport; no App private key or model runs in this process."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
from .decision_history import explain_history
from .frontdoor_control import stop_status
from .execution_fence import fence_status
from .execution_budget import ExecutionBudget
from .frontdoor_intent import IntentRefused, IntentStore, Principal
from .frontdoor_programme import prepare_programme
from .frontdoor_prepare import IntentPreparation, api_provider, protected_repository_context
from .frontdoor_synthesis import ProgrammePreparation
from .frontdoor_hosted import AgeCipher, HostedPreparationProvider
from .frontdoor_exploration import FrontDoorExploration, require_clear_stop
from .factory_feedback import FactoryFeedback
from .strategy_rules import register as register_strategy_rules
from .strategy_rejection import StrategyRejection
from .github_cli import GitHubClient
from .programme import ProgrammeRefused, parse_json
from .programme_runtime import ProgrammeQueue
from .programme_turnover import review_turnover
from .publication_currency import CurrencyProtocol, key_from_identity
from .publication_request import PublicationRequests
from .publication_source import observe_publication_source
from .publication_dispatch import PublicationDispatches
from .publication_strategy import StoredStrategyReviews
from . import publication_policy

ASSETS = Path(__file__).with_name("frontdoor_static")
MAX_BODY = 250000


class FrontDoorApplication:
    def __init__(self, *, store, project, token, origin, github, labels, app_login, preparer=None, synthesizer=None,
                 publication_key=None, publisher=None, explorer=None):
        parsed = urlsplit(origin)
        if (parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password
                or not parsed.hostname or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"})):
            raise ValueError("origin must be HTTPS or loopback HTTP without a path")
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
            raise ValueError("owner token must be 32 random bytes encoded as lowercase hex")
        self.store, self.project, self.github = store, project, github
        self.labels, self.app_login = labels, app_login
        self.preparer = preparer
        self.synthesizer = synthesizer
        self.explorer = explorer
        if publisher is not None and publication_key is None:
            raise ValueError("publication dispatch requires authenticated currency")
        self.publisher = publisher
        self.publications = (publisher.requests if publisher is not None else
                             PublicationRequests(store, app_login=app_login) if publication_key is not None else None)
        self.currency = (CurrencyProtocol(publication_key, repository=store.repository, project=project)
                         if publication_key is not None else None)
        self.origin, self.host = origin, parsed.netloc
        self.token_hash = hashlib.sha256(token.encode()).digest()
        self.principal = Principal(store.owner, "owner")
        self.execution_budget = ExecutionBudget(store)
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
        result = {"project": self.project, "repository": self.store.repository, "intent": state,
                  "observed_at": None, "preparation_available": self.preparer is not None,
                  "preparation": self.preparer.latest(self.project) if self.preparer else None,
                  "preparation_recovery": self.preparer.recovery_offer(self.project) if self.preparer else None,
                  "synthesis_available": self.synthesizer is not None,
                  "synthesis": self.synthesizer.latest(self.project) if self.synthesizer else None,
                  "publication_available": self.publisher is not None, "publication": None,
                  "strategy_choices": [], "exploration_available": self.explorer is not None}
        errors = (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError)
        try:
            result["execution_budget"] = self.execution_budget.snapshot(self.project, principal=self.principal)
        except errors:
            result["execution_budget"] = None
        # Stop must remain observable even when a programme or its receipts are damaged.
        try:
            result["stop"] = stop_status(self.github)
            result["stop_observed_at"] = datetime.now(timezone.utc).isoformat()
        except errors:
            result.update(stop=None, stop_observed_at=None)
        try:
            result["execution_fence"] = fence_status(self.github)
        except errors:
            result["execution_fence"] = None
        try:
            result["execution"] = ProgrammeQueue(self.github, "main").status(self.labels)
            result["execution_observed_at"] = datetime.now(timezone.utc).isoformat()
        except errors:
            result.update(execution=None, execution_observed_at=None)
        result["observation_available"] = result["stop"] is not None and result["execution"] is not None
        if result["observation_available"]:
            result["observed_at"] = result["execution_observed_at"]
        if self.publisher is not None:
            try:
                result["publication"] = self.publisher.latest(self.project)
            except errors:
                result["publication"] = {"workflow_observation": "unavailable"}
            if self.publications.strategy_reviews is not None:
                try:
                    result["strategy_choices"] = self.publications.strategy_reviews.choices(
                        self.project, principal=self.principal)
                except errors:
                    result["strategy_choices"] = []  # Historical choices never hide stop or product evidence.
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
                  "/exploration.js": ("exploration.js", "text/javascript; charset=utf-8"),
                  "/frontdoor.css": ("frontdoor.css", "text/css; charset=utf-8")}
        if method == "GET" and path in assets:
            name, mime = assets[path]
            return send("200 OK", (ASSETS / name).read_bytes(), mime)
        if method == "POST" and path == "/api/publication-currency" and self.currency is not None:
            try:
                # Read-only shared-key route: it cannot create owner requests. Authenticate
                # its bounded, nonce-bound envelope before any remote observation or disk read.
                result = self.currency.answer(self._body(environ), requests=self.publications,
                                              principal=self.principal,
                                              observe=lambda: observe_publication_source(self.github))
                return send("200 OK", result)
            except (IntentRefused, ProgrammeRefused, UnicodeError, ValueError):
                return send("409 Conflict", {"error": "publication currency refused"})
            except (RuntimeError, OSError, KeyError, TypeError, subprocess.SubprocessError):
                return send("503 Service Unavailable", {"error": "publication currency unavailable"})
        if not self._authenticated(environ):
            return send("401 Unauthorized", {"error": "owner authentication required"})
        if method == "POST" and environ.get("HTTP_ORIGIN") != self.origin:
            return send("403 Forbidden", {"error": "same-origin command required"})
        try:
            if method == "GET" and path == "/api/snapshot":
                return send("200 OK", self._snapshot())
            if method == "GET" and path == "/api/history":
                return send("200 OK", explain_history(self.store, self.project, principal=self.principal))
            if method == "POST" and path == "/api/execution-budget":
                result = self.execution_budget.approve(self.project, self._body(environ),
                    principal=self.principal, github=self.github, app_login=self.app_login)
                return send("200 OK", result)
            if path == "/api/exploration" and method == "GET":
                if self.explorer is None:
                    return send("503 Service Unavailable", {"error": "hosted exploration is not enabled"})
                return send("200 OK", self.explorer.snapshot(self.project, principal=self.principal))
            if path == "/api/exploration/import-feedback" and method == "POST":
                if self.explorer is None:
                    return send("503 Service Unavailable", {"error": "hosted exploration is not enabled"})
                result = FactoryFeedback(self.explorer.engine, self.github).import_outcome(
                    self.project, self._body(environ), principal=self.principal)
                return send("200 OK", result)
            if method == "POST" and path in {"/api/exploration/register-rules", "/api/exploration/assess-feedback"}:
                if self.explorer is None:
                    return send("503 Service Unavailable", {"error": "hosted exploration is not enabled"})
                command = self._body(environ)
                if path.endswith("register-rules"):
                    result = register_strategy_rules(self.explorer.engine, self.project, command, principal=self.principal)
                else:
                    result = StrategyRejection(self.explorer.engine, self.github).assess(self.project, command, principal=self.principal)
                return send("200 OK", result)
            if method == "POST" and path in {"/api/exploration/open", "/api/exploration/start",
                    "/api/exploration/recover", "/api/exploration/reopen", "/api/exploration/abandon"}:
                if self.explorer is None:
                    return send("503 Service Unavailable", {"error": "hosted exploration is not enabled"})
                operation = path.rsplit("/", 1)[-1]
                command = self._body(environ)
                if operation in {"reopen", "abandon"}:
                    result = self.explorer.command(self.project, operation, command, principal=self.principal)
                else:
                    result = getattr(self.explorer, operation)(self.project, command, principal=self.principal)
                return send("202 Accepted" if operation == "start" else "200 OK", result)
            if method == "POST" and path == "/api/commands":
                state = self.store.execute(self.project, self._body(environ), principal=self.principal)
                return send("200 OK", state)
            if method == "POST" and path in {"/api/prepare", "/api/prepare-recovery"}:
                if self.preparer is None:
                    return send("503 Service Unavailable", {"error": "specification preparation is not enabled"})
                prepare = self.preparer.recover if path == "/api/prepare-recovery" else self.preparer.prepare
                result = prepare(self.project, self._body(environ), principal=self.principal)
                return send("200 OK", result)
            if method == "POST" and path == "/api/programme-review":
                review = prepare_programme(self.store, self.project, self._body(environ),
                                           principal=self.principal, app_login=self.app_login)
                return send("200 OK", review)
            if method == "POST" and path == "/api/programme-publication":
                if self.publications is None:
                    return send("503 Service Unavailable", {"error": "publication requests are not enabled"})
                request = self._body(environ)
                result = self.publications.reserve(self.project, request, principal=self.principal,
                                                   observation=observe_publication_source(self.github))
                return send("200 OK", result)
            if method == "POST" and path == "/api/programme-replacement-review":
                if self.publications is None:
                    return send("503 Service Unavailable", {"error": "publication requests are not enabled"})
                result = review_turnover(self.publications, self.github, self.project,
                                         self._body(environ), principal=self.principal)
                return send("200 OK", result)
            if method == "POST" and path in {"/api/publication-preview", "/api/strategy-publication-preview", "/api/programme-publish"}:
                if self.publisher is None:
                    return send("503 Service Unavailable", {"error": "programme publication is not enabled"})
                operation = {"/api/publication-preview": self.publisher.preview,
                             "/api/strategy-publication-preview": self.publisher.preview_strategy,
                             "/api/programme-publish": self.publisher.publish}[path]
                result = operation(self.project, self._body(environ), principal=self.principal)
                return send("202 Accepted" if path == "/api/programme-publish" else "200 OK", result)
            if method == "POST" and path == "/api/programme-prepare":
                if self.synthesizer is None:
                    return send("503 Service Unavailable", {"error": "programme preparation is not enabled"})
                result = self.synthesizer.prepare(self.project, self._body(environ), principal=self.principal)
                return send("200 OK", result)
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
    preparation = parser.add_mutually_exclusive_group()
    preparation.add_argument("--enable-preparation", action="store_true")
    preparation.add_argument("--hosted-preparation-identity", type=Path,
                             help="private age identity for proposal calls through protected GitHub Actions")
    parser.add_argument("--enable-publication-requests", action="store_true",
                        help="enable private owner reservations and read-only publisher currency; no dispatch")
    parser.add_argument("--enable-programme-publication", action="store_true",
                        help="connect explicit owner consent to the protected programme publisher")
    parser.add_argument("--enable-strategy-publication", action="store_true",
                        help="allow publication of a freshly regenerated stored strategy recommendation")
    parser.add_argument("--enable-hosted-exploration", action="store_true",
                        help="enable owner-requested bounded adaptive jobs through protected workers")
    parser.add_argument("--exploration-source", action="append", default=[],
                        help="committed source path to include in exploration context (repeat, at most 40)")
    args = parser.parse_args()
    if args.enable_hosted_exploration and (not args.hosted_preparation_identity or not args.exploration_source):
        parser.error("hosted exploration requires the encrypted hosted identity and explicit source paths")
    if args.enable_publication_requests and not args.hosted_preparation_identity:
        parser.error("publication requests require the existing hosted preparation identity")
    if args.enable_strategy_publication and not args.enable_programme_publication:
        parser.error("strategy publication requires protected programme publication")
    if args.enable_programme_publication and (not args.enable_publication_requests
            or args.project != publication_policy.PROJECT or args.origin != publication_policy.ORIGIN):
        parser.error("programme publication requires currency and its protected project/origin")
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
    provider = None
    if args.hosted_preparation_identity:
        provider = HostedPreparationProvider(store, github, AgeCipher(args.hosted_preparation_identity),
                                              check_stop=lambda: require_clear_stop(github))
    elif args.enable_preparation:
        provider = api_provider(config.provider)
    context = lambda: protected_repository_context(github)
    preparer = IntentPreparation(store, provider, context) if provider else None
    synthesizer = ProgrammePreparation(store, provider, context,
                                      app_login=args.app_login) if provider else None
    publication_requests = PublicationRequests(store, app_login=args.app_login,
        strategy_reviews=StoredStrategyReviews(store, github, app_login=args.app_login)
        if args.enable_strategy_publication else None) if args.enable_programme_publication else None
    publisher = (PublicationDispatches(store, github, AgeCipher(args.hosted_preparation_identity), app_login=args.app_login,
                                      requests=publication_requests)
                 if args.enable_programme_publication else None)
    app = FrontDoorApplication(store=store, project=args.project, token=token, origin=args.origin,
                               github=github, labels=config.labels, app_login=args.app_login,
                               preparer=preparer, synthesizer=synthesizer, publisher=publisher,
                               explorer=(FrontDoorExploration.protected(store, provider, github,
                                         args.exploration_source, app_login=args.app_login)
                                         if args.enable_hosted_exploration else None),
                               publication_key=(key_from_identity(args.hosted_preparation_identity)
                                                if args.enable_publication_requests else None))
    # Single-host, bounded request timeout, loopback only. Hosting must enforce HTTPS and keep
    # state/token files outside all worker sandboxes. This is not a distributed service.
    with make_server("127.0.0.1", args.port, app, server_class=FrontDoorServer, handler_class=QuietHandler) as server:
        print(f"FRONT_DOOR_LISTENING loopback_port={args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
