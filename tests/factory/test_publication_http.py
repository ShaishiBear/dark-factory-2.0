"""Owner writes and publisher reads use different credentials and capabilities."""
import unittest
from unittest.mock import patch

from factory_kernel.frontdoor_http import FrontDoorApplication
from tests.factory import test_frontdoor_http as http_tests
from tests.factory import test_publication_currency as currency_tests


class PublicationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.http = http_tests.FrontDoorHTTPTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)
        self.currency = currency_tests.PublicationCurrencyTests()
        self.currency.setUp()
        self.addCleanup(self.currency.doCleanups)
        store = self.currency.fixture.fixture.store
        self.http.app = FrontDoorApplication(store=store, project="citations", token=http_tests.TOKEN,
                                            origin=http_tests.ORIGIN, github=self.http.github,
                                            labels={}, app_login="factory[bot]", publication_key=b"x" * 32)
        self.http.app.publications = self.currency.fixture.service
        self.http.app.currency = self.currency.protocol

    def test_publisher_can_only_read_currency_with_authenticated_envelope(self):
        with patch("factory_kernel.frontdoor_http.observe_publication_source", side_effect=lambda _github: self.currency.observer()):
            result = self.http.call("/api/publication-currency", body=self.currency.request, HTTP_AUTHORIZATION="")
        self.assertEqual(result["status"], "200 OK", result)
        current = self.currency.protocol.verify(result["json"], self.currency.request)
        self.assertEqual(current["project_version"], 3)
        self.assertNotIn("wording", str(result["json"]))
        self.http.github.run.assert_not_called()
        self.http.github.run_as_app.assert_not_called()

    def test_bearer_alone_cannot_masquerade_as_publisher(self):
        with patch("factory_kernel.frontdoor_http.observe_publication_source", side_effect=lambda _github: self.currency.observer()):
            result = self.http.call("/api/publication-currency", body={"request_id": "a" * 32})
        self.assertEqual(result["status"], "409 Conflict")
        self.currency.observer.assert_not_called()

    def test_publisher_envelope_cannot_write_owner_reservations(self):
        with patch("factory_kernel.frontdoor_http.observe_publication_source", side_effect=lambda _github: self.currency.observer()):
            result = self.http.call("/api/programme-publication", body=self.currency.request, HTTP_AUTHORIZATION="")
        self.assertEqual(result["status"], "401 Unauthorized")
        self.currency.observer.assert_not_called()

    def test_owner_reservation_is_bound_to_configured_project_and_never_dispatches(self):
        with patch("factory_kernel.frontdoor_http.observe_publication_source", side_effect=lambda _github: self.currency.observer()):
            result = self.http.call("/api/programme-publication", body=self.currency.fixture.command)
        self.assertEqual(result["status"], "200 OK", result)
        self.assertEqual(result["json"], self.currency.record)
        self.http.github.run.assert_not_called()
        self.http.github.run_as_app.assert_not_called()

    def test_origin_query_and_disabled_service_cannot_bypass_either_boundary(self):
        self.assertEqual(self.http.call("/api/publication-currency", body=self.currency.request,
                                       HTTP_ORIGIN="https://elsewhere.example")["status"], "403 Forbidden")
        self.assertEqual(self.http.call("/api/publication-currency", body=self.currency.request,
                                       QUERY_STRING="project=other")["status"], "400 Bad Request")
        self.http.app.publications = None
        self.http.app.currency = None
        self.assertEqual(self.http.call("/api/programme-publication", body=self.currency.fixture.command)["status"],
                         "503 Service Unavailable")
        self.assertEqual(self.http.call("/api/publication-currency", body=self.currency.request,
                                       HTTP_AUTHORIZATION="")["status"], "401 Unauthorized")


if __name__ == "__main__":
    unittest.main()

