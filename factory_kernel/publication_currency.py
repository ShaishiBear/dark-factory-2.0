"""Authenticated, fresh, read-only owner-request currency exchange.

The shared secret belongs only to the trusted host and protected workflow. It cannot
reserve owner requests, grant a GitHub capability, qualify product work or replace source
workflow provenance. No model receives the key or this exchange's response.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import re
import secrets
import time

from .canonical import canonical_bytes
from .frontdoor_hosted import AgeCipher
from .frontdoor_intent import IntentRefused, _shape

DOMAIN = b"dark-factory/publication-currency/v1/"
MAX_AGE_SECONDS = 60


def key_from_identity(path):
    """Domain-separate the existing high-entropy native age identity; never return its text."""
    cipher = AgeCipher(path)  # same regular-file, mode and native-key checks as encryption
    rows = [row for row in cipher.identity.read_text().splitlines() if row and not row.startswith("#")]
    return hmac.digest(rows[0].encode("ascii"), DOMAIN + b"key", "sha256")


def _hex(value, size):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{%d}" % size, value):
        raise IntentRefused("invalid currency identity")
    return value


class CurrencyProtocol:
    def __init__(self, key, *, repository, project, clock=None):
        if not isinstance(key, bytes) or len(key) != 32:
            raise IntentRefused("currency key must contain 32 bytes")
        self.key, self.repository, self.project = key, repository, project
        self.clock = clock or time.time

    def _seal(self, payload, purpose):
        raw = canonical_bytes(payload)
        if len(raw) > 10000:
            raise IntentRefused("currency envelope too large")
        return {"payload": payload, "mac": hmac.digest(self.key, DOMAIN + purpose + raw, "sha256").hex()}

    def _open(self, envelope, purpose):
        _shape(envelope, {"payload", "mac"})
        mac = _hex(envelope["mac"], 64)
        expected = self._seal(envelope["payload"], purpose)["mac"]
        if not hmac.compare_digest(mac, expected):
            raise IntentRefused("currency authentication refused")
        return envelope["payload"]

    def _challenge(self, challenge):
        _shape(challenge, {"repository", "project", "request_id", "request_sha256", "main_sha",
                           "phase", "nonce", "issued_at"})
        if (challenge["repository"] != self.repository or challenge["project"] != self.project
                or challenge["phase"] not in {"branch", "pull-request", "merge"}
                or type(challenge["issued_at"]) is not int
                or not -5 <= self.clock() - challenge["issued_at"] <= MAX_AGE_SECONDS):
            raise IntentRefused("currency challenge destination, phase or freshness refused")
        for key, size in (("request_id", 32), ("request_sha256", 64), ("main_sha", 40), ("nonce", 32)):
            _hex(challenge[key], size)
        return challenge

    def challenge(self, *, request_id, request_sha256, main_sha, phase):
        payload = self._challenge({"repository": self.repository, "project": self.project,
                                   "request_id": request_id, "request_sha256": request_sha256,
                                   "main_sha": main_sha, "phase": phase,
                                   "nonce": secrets.token_hex(16), "issued_at": int(self.clock())})
        return self._seal(payload, b"request/")

    def answer(self, envelope, *, requests, principal, observe):
        challenge = self._challenge(self._open(envelope, b"request/"))
        # Authentication precedes every remote read. This route cannot create a reservation.
        current = requests.current(self.project, challenge["request_id"], principal=principal,
                                   observation=observe())
        if (current["request_sha256"] != challenge["request_sha256"]
                or current["main_sha"] != challenge["main_sha"]):
            raise IntentRefused("currency challenge names a different request or source")
        self._challenge(challenge)  # remote reads must not silently outlive the challenge
        return self._seal({"challenge": challenge, "observed_at": int(self.clock()), "current": current}, b"response/")

    def verify(self, envelope, challenge_envelope):
        challenge = self._challenge(self._open(challenge_envelope, b"request/"))
        result = self._open(envelope, b"response/")
        _shape(result, {"challenge", "observed_at", "current"})
        if (result["challenge"] != challenge or type(result["observed_at"]) is not int
                or not challenge["issued_at"] - 5 <= result["observed_at"] <= self.clock() + 5
                or self.clock() - result["observed_at"] > MAX_AGE_SECONDS):
            raise IntentRefused("currency response is stale or belongs to another challenge")
        current = result["current"]
        _shape(current, {"request_id", "request_sha256", "repository", "project", "project_version",
                         "main_sha", "input_sha256", "programme_sha256", "expires_at", "decision"})
        if (any(current[key] != challenge[key] for key in
                ("request_id", "request_sha256", "repository", "project", "main_sha"))
                or current["decision"] != "current-owner-request"
                or type(current["project_version"]) is not int or current["project_version"] < 1):
            raise IntentRefused("currency response identity refused")
        for field in ("input_sha256", "programme_sha256"):
            _hex(current[field], 64)
        expires = datetime.fromisoformat(current["expires_at"])
        if expires.tzinfo is None or expires <= datetime.fromtimestamp(self.clock(), timezone.utc):
            raise IntentRefused("currency request expired before response consumption")
        return current
