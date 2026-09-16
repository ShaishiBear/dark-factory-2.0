"""The protected currency client uses one fixed HTTPS origin and authenticates its response."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from factory_kernel.frontdoor_intent import IntentRefused
from factory_kernel.publication_client import NoRedirect, current_currency
from factory_kernel.publication_currency import CurrencyProtocol, key_from_identity
from factory_kernel import publication_policy as policy
from tests.factory.test_publication_admission import publication_facts


class Response(BytesIO):
    status = 200
    url = policy.ORIGIN + "/api/publication-currency"
    def geturl(self):
        return self.url


class PublicationClientTests(unittest.TestCase):
    def setUp(self):
        # Synthetic, nonfunctional fixture; no real identity or network is used.
        self.secret = "AGE-SECRET-KEY-1" + "A" * 58 + "\n"
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = directory / "identity"
        path.touch(mode=0o600)
        path.write_text(self.secret)
        self.protocol = CurrencyProtocol(key_from_identity(path), repository=policy.REPOSITORY, project=policy.PROJECT)
        self.manifest = publication_facts()["manifest"]
        self.change = lambda value: value
        self.response_status, self.response_url = 200, Response.url
        self.opener = Mock()
        def answer(request, timeout):
            self.assertEqual(timeout, 30)
            self.assertEqual(request.full_url, Response.url)
            self.assertEqual(request.method, "POST")
            self.assertNotIn(self.secret.strip().encode(), request.data)
            envelope = json.loads(request.data)
            challenge = self.protocol._challenge(self.protocol._open(envelope, b"request/"))
            current = {key: challenge[key] for key in ("request_id", "request_sha256", "repository", "project", "main_sha")}
            current.update(project_version=1, input_sha256=self.manifest["input_sha256"],
                           programme_sha256=self.manifest["programme_sha256"], decision="current-owner-request",
                           expires_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat())
            envelope = self.protocol._seal({"challenge": challenge, "observed_at": int(time.time()),
                                           "current": self.change(current)}, b"response/")
            response = Response(json.dumps(envelope).encode())
            response.status, response.url = self.response_status, self.response_url
            return response
        self.opener.open.side_effect = answer

    def call(self):
        return current_currency(self.manifest, "merge", secret=self.secret, opener=self.opener)

    def test_fresh_fixed_origin_exchange_returns_exact_payload_identities(self):
        self.assertEqual(self.call()["input_sha256"], self.manifest["input_sha256"])
        self.opener.open.assert_called_once()

    def test_even_authenticated_other_payload_is_refused(self):
        for field in ("input_sha256", "programme_sha256"):
            self.change = lambda value: {**value, field: "f" * 64}
            with self.subTest(field=field), self.assertRaisesRegex(IntentRefused, "payload differs"):
                self.call()

    def test_redirect_wrong_status_and_oversize_are_refused(self):
        with self.assertRaises(IntentRefused):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid")
        for status, url in ((302, Response.url), (200, "https://elsewhere.invalid")):
            self.response_status, self.response_url = status, url
            with self.subTest(status=status), self.assertRaises(IntentRefused):
                self.call()
        self.opener.open.side_effect = lambda *_args, **_kwargs: Response(b"x" * 10001)
        with self.assertRaisesRegex(IntentRefused, "bound"):
            self.call()

    def test_missing_or_plugin_identity_is_refused_before_network(self):
        for secret in ("", None, "AGE-PLUGIN-SECRET-KEY-1fixture", "x" * 1025):
            with self.subTest(secret=secret), self.assertRaises(IntentRefused):
                current_currency(self.manifest, "merge", secret=secret or "", opener=self.opener)
        self.opener.open.assert_not_called()

    def test_altered_unsigned_response_is_not_current(self):
        self.opener.open.side_effect = lambda *_args, **_kwargs: Response(b'{"payload":{},"mac":"' + b"0" * 64 + b'"}')
        with self.assertRaisesRegex(IntentRefused, "authentication"):
            self.call()


if __name__ == "__main__":
    unittest.main()
