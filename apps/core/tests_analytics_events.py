from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpRequest
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.analytics_events import (
    SESSION_KEY_PENDING_ANALYTICS_EVENTS,
    build_account_download_analytics_payload,
    build_product_file_download_analytics_payload,
    consume_login_analytics_suppression,
    consume_pending_analytics_events,
    extract_file_extension,
    map_auth_method,
    map_social_provider,
    queue_client_analytics_event,
    suppress_login_analytics_event,
)
from apps.orders.models import Order
from apps.products.models import Product, ProductFile


class AnalyticsEventsHelpersTests(SimpleTestCase):
    def test_extract_file_extension(self):
        self.assertEqual(extract_file_extension("game.zip"), "zip")
        self.assertEqual(extract_file_extension("GAME.PDF"), "pdf")
        self.assertEqual(extract_file_extension("noext"), "")

    def test_map_auth_method(self):
        self.assertEqual(map_auth_method({"method": "socialaccount", "provider": "google"}), "google")
        self.assertEqual(map_auth_method({"method": "socialaccount", "provider": "yandex"}), "yandex")
        self.assertEqual(map_auth_method({"method": "password"}), "email")

    def test_map_social_provider(self):
        self.assertEqual(map_social_provider("google"), "google")
        self.assertEqual(map_social_provider("unknown"), "social")


class AnalyticsEventsSessionTests(TestCase):
    def setUp(self):
        self.request = HttpRequest()
        self.request.session = self.client.session

    def test_queue_and_consume_pending_events(self):
        queue_client_analytics_event(self.request, event="sign_up", payload={"method": "email"})
        queue_client_analytics_event(self.request, event="login", payload={"method": "google"})

        events = consume_pending_analytics_events(self.request)

        self.assertEqual(
            events,
            [
                {"event": "sign_up", "method": "email"},
                {"event": "login", "method": "google"},
            ],
        )
        self.assertEqual(consume_pending_analytics_events(self.request), [])

    def test_queue_client_analytics_event_supports_plain_dict_session(self):
        request = SimpleNamespace(session={})
        queue_client_analytics_event(request, event="login", payload={"method": "email"})
        self.assertEqual(
            request.session[SESSION_KEY_PENDING_ANALYTICS_EVENTS],
            [{"event": "login", "method": "email"}],
        )

    def test_suppress_login_flag_is_one_time(self):
        suppress_login_analytics_event(self.request)
        self.assertTrue(consume_login_analytics_suppression(self.request))
        self.assertFalse(consume_login_analytics_suppression(self.request))

    def test_build_download_payloads_do_not_include_pii(self):
        account_payload = build_account_download_analytics_payload(
            item_id="12",
            item_name="Игра",
            item_category="Категория",
            file_extension="zip",
        )
        self.assertEqual(account_payload["event"], "file_download_account")
        self.assertEqual(account_payload["item_id"], "12")
        self.assertNotIn("email", account_payload)

        class ProductStub:
            id = 12
            title = "Игра"
            slug = "igra"
            categories = SimpleNamespace(first=lambda: SimpleNamespace(title="Категория"))
            subtypes = SimpleNamespace(first=lambda: None)

        class FileStub:
            original_filename = "igra.zip"

        product_payload = build_product_file_download_analytics_payload(
            product=ProductStub(),
            product_file=FileStub(),
        )
        self.assertEqual(product_payload["file_extension"], "zip")
        self.assertEqual(product_payload["download_type"], "account")


class FileDownloadAnalyticsMpTests(TestCase):
    @override_settings(
        ANALYTICS_ENABLED=True,
        GA4_MEASUREMENT_ID="G-TEST123",
        GA4_API_SECRET="ga4-secret",
    )
    @patch("apps.core.analytics.httpx.post")
    def test_send_ga4_file_download_guest_event_posts_expected_payload(self, httpx_post_mock):
        from apps.core.analytics import send_ga4_file_download_guest_event

        order = Order.objects.create(
            email="guest@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
            analytics_storage_consent=True,
        )
        product = Product.objects.create(title="Guest product", slug="guest-product", price="10.00")
        product_file = ProductFile.objects.create(
            product=product,
            file_key="guest-product/archive.zip",
            original_filename="archive.zip",
            is_active=True,
        )

        guest_access = SimpleNamespace(id=1, order=order, product=product)

        response_mock = httpx_post_mock.return_value
        response_mock.raise_for_status.return_value = None

        send_ga4_file_download_guest_event(
            guest_access=guest_access,
            product_file=product_file,
            source="test",
        )

        payload = httpx_post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["events"][0]["name"], "file_download_guest")
        self.assertEqual(payload["events"][0]["params"]["item_id"], str(product.id))
        self.assertEqual(payload["events"][0]["params"]["file_extension"], "zip")
        self.assertNotIn("email", payload["events"][0]["params"])
