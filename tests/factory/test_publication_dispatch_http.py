"""Owner consent routes preserve authentication, independent stop and disabled defaults."""
import unittest
from unittest.mock import patch

from factory_kernel.frontdoor_http import FrontDoorApplication
from factory_kernel import publication_policy as policy
from tests.factory import test_frontdoor_http as http_fixtures
from tests.factory import test_publication_dispatch as dispatch_fixtures


class PublicationDispatchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = dispatch_fixtures.PublicationDispatchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.http = http_fixtures.FrontDoorHTTPTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)
        self.fixture.github.programme_issues.return_value = []
        self.http.app = FrontDoorApplication(store=self.fixture.store, project=policy.PROJECT,
            token=http_fixtures.TOKEN, origin=http_fixtures.ORIGIN, github=self.fixture.github,
            labels={}, app_login=policy.APP_LOGIN, publication_key=b"x" * 32, publisher=self.fixture.service)

    def test_preview_and_publish_require_owner_bearer_and_same_origin(self):
        for path, body in (("/api/publication-preview", self.fixture.review),
                           ("/api/programme-replacement-review", self.fixture.review),
                           ("/api/programme-publish", self.fixture.command)):
            self.assertEqual(self.http.call(path, body=body, HTTP_AUTHORIZATION="")["status"], "401 Unauthorized")
            self.assertEqual(self.http.call(path, body=body, HTTP_ORIGIN="https://elsewhere.invalid")["status"], "403 Forbidden")
        self.fixture.github.run.assert_not_called()

    def test_real_owner_preview_then_exact_consent_dispatches_once(self):
        preview = self.http.call("/api/publication-preview", body=self.fixture.review)
        self.assertEqual(preview["status"], "200 OK", preview)
        self.assertEqual(preview["json"]["destination"], self.fixture.command["destination"])
        self.fixture.github.run.assert_not_called()
        response = self.http.call("/api/programme-publish", body=self.fixture.command)
        self.assertEqual(response["status"], "202 Accepted", response)
        self.assertEqual(response["json"]["state"], "dispatch-submitted")
        self.assertEqual(self.http.call("/api/programme-publish", body=self.fixture.command)["json"], response["json"])
        self.fixture.github.run.assert_called_once()
        self.fixture.github.run_as_app.assert_not_called()

    def test_publication_observation_failure_cannot_hide_stop_or_product_progress(self):
        self.fixture.github.programme_issues.return_value = [
            {"number": 17, "state": "open", "labels": [{"name": "factory:stop"}]}]
        with patch.object(self.fixture.service, "latest", side_effect=OSError("private storage failed")), \
                patch("factory_kernel.frontdoor_http.ProgrammeQueue") as queue:
            queue.return_value.status.return_value = {"items": [], "status": "incomplete"}
            result = self.http.call("/api/snapshot")
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["json"]["stop"], {"state": "stopped", "issues": [17]})
        self.assertEqual(result["json"]["execution"]["status"], "incomplete")
        self.assertEqual(result["json"]["publication"]["workflow_observation"], "unavailable")
        self.assertNotIn(b"private storage", result["body"])

    def test_disabled_dispatch_and_missing_currency_cannot_activate(self):
        self.http.app.publisher = None
        self.assertEqual(self.http.call("/api/programme-publish", body=self.fixture.command)["status"], "503 Service Unavailable")
        with self.assertRaisesRegex(ValueError, "authenticated currency"):
            FrontDoorApplication(store=self.fixture.store, project=policy.PROJECT,
                token=http_fixtures.TOKEN, origin=http_fixtures.ORIGIN, github=self.fixture.github,
                labels={}, app_login=policy.APP_LOGIN, publisher=self.fixture.service)
        self.fixture.github.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
