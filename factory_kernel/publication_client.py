"""Protected publisher's fixed-origin read-only currency client; no owner bearer export."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .canonical import canonical_bytes
from .frontdoor_intent import IntentRefused
from .programme import parse_json
from .publication_currency import CurrencyProtocol, key_from_identity
from . import publication_policy as policy


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, _request, _file, _code, _message, _headers, _new_url):
        raise IntentRefused("publication currency origin redirected")


def current_currency(manifest, phase, *, secret=None, opener=None):
    # Only trusted code calls this. The candidate PR never chooses a hostname, credential
    # path, project or owner, and head-based CI never receives this secret.
    identity = secret if secret is not None else os.environ.get("FRONTDOOR_AGE_IDENTITY", "")
    if not isinstance(identity, str) or not identity or len(identity) > 1024:
        raise IntentRefused("protected publication currency identity is unavailable")
    with tempfile.TemporaryDirectory(prefix="factory-publication-currency-") as directory:
        path = Path(directory) / "identity.age"
        path.touch(mode=0o600)
        path.write_text(identity, encoding="utf-8")
        protocol = CurrencyProtocol(key_from_identity(path), repository=policy.REPOSITORY, project=policy.PROJECT)
    challenge = protocol.challenge(request_id=manifest["request_id"], request_sha256=manifest["request_sha256"],
                                   main_sha=manifest["source_sha"], phase=phase)
    request = Request(policy.ORIGIN + "/api/publication-currency", data=canonical_bytes(challenge),
                      headers={"Content-Type": "application/json", "Origin": policy.ORIGIN}, method="POST")
    client = opener if opener is not None else build_opener(NoRedirect())
    with client.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != request.full_url:
            raise IntentRefused("publication currency response origin or status refused")
        raw = response.read(10001)
    if len(raw) > 10000:
        raise IntentRefused("publication currency response exceeds its bound")
    current = protocol.verify(parse_json(raw.decode("utf-8")), challenge)
    if (current["input_sha256"] != manifest["input_sha256"]
            or current["programme_sha256"] != manifest["programme_sha256"]):
        raise IntentRefused("current owner payload differs from the validated publication")
    return current
