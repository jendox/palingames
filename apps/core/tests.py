import json
import logging
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import caches
from django.http import HttpRequest
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.core.alerts import (
    ThresholdIncidentSpec,
    record_threshold_incident,
    resolve_threshold_incident,
    send_incident_alert,
    send_incident_recovery,
)
from apps.core.analytics import send_ga4_purchase_event_for_order
from apps.core.consent import SESSION_KEY_ANALYTICS_STORAGE, SESSION_KEY_CONSENT_POLICY_VERSION
from apps.core.context_processors import analytics
from apps.core.logging import (
    JsonFormatter,
    LoggingContextFilter,
    build_task_logging_headers,
    clear_logging_context,
    set_logging_context,
)
from apps.core.rate_limits import RateLimitScope, check_rate_limit
from apps.core.sentry import configure_sentry_scope, init_sentry
from apps.core.tasks import clear_expired_sessions_task
from apps.orders.models import Order, OrderItem
from apps.payments.models import Invoice
from apps.products.models import Product


class StructuredLoggingTests(TestCase):
    def tearDown(self):
        clear_logging_context()

    def test_json_formatter_redacts_sensitive_fields_and_includes_request_id(self):
        set_logging_context(request_id="req-123", email="user@example.com")
        logger = logging.getLogger("tests.logging")
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            0,
            "order.creation.success",
            args=(),
            exc_info=None,
            extra={
                "event": "order.creation.success",
                "event_context": {
                    "order_id": 42,
                    "token": "secret-token",
                },
            },
        )
        LoggingContextFilter().filter(record)

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["request_id"], "req-123")
        self.assertEqual(payload["event"], "order.creation.success")
        self.assertEqual(payload["context"]["order_id"], 42)
        self.assertEqual(payload["context"]["email"], "***")
        self.assertEqual(payload["context"]["token"], "***")
        self.assertNotIn("message", payload)

    def test_json_formatter_keeps_message_for_non_event_logs(self):
        logger = logging.getLogger("tests.logging")
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            0,
            "Connected to redis://localhost:6379/0",
            args=(),
            exc_info=None,
            extra=None,
        )
        LoggingContextFilter().filter(record)

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["message"], "Connected to redis://localhost:6379/0")
        self.assertNotIn("event", payload)

    def test_build_task_logging_headers_uses_request_id_from_context(self):
        set_logging_context(request_id="req-456")

        self.assertEqual(build_task_logging_headers(), {"request_id": "req-456"})

    def test_request_middleware_sets_request_id_header(self):
        response = self.client.get(reverse("home"), HTTP_X_REQUEST_ID="req-from-client")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Request-ID"], "req-from-client")


class SentryIntegrationTests(TestCase):
    def tearDown(self):
        clear_logging_context()

    def test_init_sentry_returns_false_when_dsn_is_not_configured(self):
        initialized = init_sentry(
            dsn="",
            environment="test",
            release=None,
            traces_sample_rate=0.0,
        )

        self.assertFalse(initialized)

    @patch("apps.core.sentry._sentry_available", return_value=True)
    @patch("apps.core.sentry.sentry_sdk")
    def test_init_sentry_initializes_sdk_when_available(self, sentry_sdk_mock, _sentry_available_mock):
        initialized = init_sentry(
            dsn="https://example@sentry.io/1",
            environment="production",
            release="test-release",
            traces_sample_rate=0.0,
        )

        self.assertTrue(initialized)
        sentry_sdk_mock.init.assert_called_once()

    @patch("apps.core.sentry.sentry_sdk")
    def test_configure_sentry_scope_uses_standard_tags_and_context(self, sentry_sdk_mock):
        tags = {}
        contexts = {}

        class FakeScope:
            def set_tag(self, key, value):
                tags[key] = value

            def set_context(self, key, value):
                contexts[key] = value

        @contextmanager
        def fake_configure_scope():
            yield FakeScope()

        sentry_sdk_mock.configure_scope = fake_configure_scope
        set_logging_context(request_id="req-123", path="/checkout/")

        configure_sentry_scope(task_id="task-1", order_id=42, email="user@example.com")

        self.assertEqual(tags["request_id"], "req-123")
        self.assertEqual(tags["task_id"], "task-1")
        self.assertEqual(contexts["logging_context"]["order_id"], 42)
        self.assertEqual(contexts["logging_context"]["email"], "***")


class CoreTasksTests(TestCase):
    @patch("apps.core.tasks.call_command")
    def test_clear_expired_sessions_task_calls_django_clearsessions(self, call_command_mock):
        clear_expired_sessions_task.apply(args=())

        call_command_mock.assert_called_once_with("clearsessions")


class IncidentAlertTests(TestCase):
    def tearDown(self):
        caches["default"].clear()

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_INCIDENTS_THREAD_ID=0,
    )
    @patch("apps.core.alerts.send_telegram_message")
    def test_send_incident_alert_returns_false_when_incidents_route_is_not_configured(self, send_mock):
        sent = send_incident_alert(
            key="payments.webhook.failures",
            title="Repeated payment webhook failures",
            severity="critical",
            details={"failures": 5},
        )

        self.assertFalse(sent)
        send_mock.assert_not_called()

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_INCIDENTS_THREAD_ID=9,
    )
    @patch("apps.core.alerts.send_telegram_message")
    def test_send_incident_alert_sends_message_when_configured(self, send_mock):
        sent = send_incident_alert(
            key="payments.webhook.failures",
            title="Repeated payment webhook failures",
            severity="critical",
            details={"failures": 5},
        )

        self.assertTrue(sent)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["destination"].value, "incidents")
        text = send_mock.call_args.kwargs["text"]
        self.assertIn("[CRITICAL] Repeated payment webhook failures", text)
        self.assertIn("key: payments.webhook.failures", text)
        self.assertIn("failures: 5", text)

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_INCIDENTS_THREAD_ID=9,
    )
    @patch("apps.core.alerts.send_telegram_message")
    def test_send_incident_alert_deduplicates_by_key_when_details_change(self, send_mock):
        first = send_incident_alert(
            key="payments.webhook.failures",
            title="Repeated payment webhook failures",
            severity="critical",
            details={"failures": 5},
        )
        second = send_incident_alert(
            key="payments.webhook.failures",
            title="Repeated payment webhook failures",
            severity="critical",
            details={"failures": 6},
        )

        self.assertTrue(first)
        self.assertFalse(second)
        send_mock.assert_called_once()

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_INCIDENTS_THREAD_ID=9,
    )
    @patch("apps.core.alerts.send_telegram_message")
    def test_send_incident_alert_uses_explicit_fingerprint_for_deduplication(self, send_mock):
        first = send_incident_alert(
            key="payments.webhook.failures",
            title="Repeated payment webhook failures",
            severity="critical",
            fingerprint="payments.webhook.failures:express_pay:invoice_not_found",
            details={"provider": "EXPRESS_PAY", "reason": "invoice_not_found"},
        )
        second = send_incident_alert(
            key="payments.webhook.failures",
            title="Repeated payment webhook failures",
            severity="critical",
            fingerprint="payments.webhook.failures:express_pay:processing_error",
            details={"provider": "EXPRESS_PAY", "reason": "processing_error"},
        )

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(send_mock.call_count, 2)

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_INCIDENTS_THREAD_ID=9,
    )
    @patch("apps.core.alerts.send_telegram_message")
    def test_send_incident_alert_renders_warning_severity(self, send_mock):
        sent = send_incident_alert(
            key="storage.s3.unavailable",
            title="Storage is unavailable",
            severity="warning",
            details={"bucket": "products"},
        )

        self.assertTrue(sent)
        text = send_mock.call_args.kwargs["text"]
        self.assertIn("[WARNING] Storage is unavailable", text)

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_INCIDENTS_THREAD_ID=9,
    )
    @patch("apps.core.alerts.send_telegram_message")
    def test_send_incident_recovery_sends_resolved_message_when_configured(self, send_mock):
        sent = send_incident_recovery(
            key="storage.s3.unavailable",
            title="Storage recovered",
            fingerprint="storage.s3.unavailable:generate_presigned_download_url",
            details={"operation": "generate_presigned_download_url"},
        )

        self.assertTrue(sent)
        text = send_mock.call_args.kwargs["text"]
        self.assertIn("[RESOLVED] Storage recovered", text)
        self.assertIn("key: storage.s3.unavailable", text)
        self.assertIn("operation: generate_presigned_download_url", text)

    @patch("apps.core.alerts.send_incident_alert")
    def test_record_threshold_incident_below_threshold_does_not_send_alert(self, send_incident_alert_mock):
        sent = record_threshold_incident(
            counter_key="incident:payments:webhook",
            threshold=3,
            window_seconds=600,
            incident=ThresholdIncidentSpec(
                key="payments.webhook.failures",
                title="Repeated payment webhook failures",
                severity="critical",
                fingerprint="payments.webhook.failures:EXPRESS_PAY:invoice_not_found",
                details={"provider": "EXPRESS_PAY", "reason": "invoice_not_found"},
            ),
        )

        self.assertFalse(sent)
        send_incident_alert_mock.assert_not_called()

    @patch("apps.core.alerts.send_incident_alert")
    def test_record_threshold_incident_sends_alert_when_threshold_is_reached(self, send_incident_alert_mock):
        send_incident_alert_mock.return_value = True

        self.assertFalse(
            record_threshold_incident(
                counter_key="incident:payments:webhook",
                threshold=3,
                window_seconds=600,
                incident=ThresholdIncidentSpec(
                    key="payments.webhook.failures",
                    title="Repeated payment webhook failures",
                    severity="critical",
                    fingerprint="payments.webhook.failures:EXPRESS_PAY:invoice_not_found",
                    details={"provider": "EXPRESS_PAY", "reason": "invoice_not_found"},
                ),
            ),
        )
        self.assertFalse(
            record_threshold_incident(
                counter_key="incident:payments:webhook",
                threshold=3,
                window_seconds=600,
                incident=ThresholdIncidentSpec(
                    key="payments.webhook.failures",
                    title="Repeated payment webhook failures",
                    severity="critical",
                    fingerprint="payments.webhook.failures:EXPRESS_PAY:invoice_not_found",
                    details={"provider": "EXPRESS_PAY", "reason": "invoice_not_found"},
                ),
            ),
        )
        self.assertTrue(
            record_threshold_incident(
                counter_key="incident:payments:webhook",
                threshold=3,
                window_seconds=600,
                incident=ThresholdIncidentSpec(
                    key="payments.webhook.failures",
                    title="Repeated payment webhook failures",
                    severity="critical",
                    fingerprint="payments.webhook.failures:EXPRESS_PAY:invoice_not_found",
                    details={"provider": "EXPRESS_PAY", "reason": "invoice_not_found"},
                ),
            ),
        )

        send_incident_alert_mock.assert_called_once_with(
            key="payments.webhook.failures",
            title="Repeated payment webhook failures",
            severity="critical",
            fingerprint="payments.webhook.failures:EXPRESS_PAY:invoice_not_found",
            details={
                "provider": "EXPRESS_PAY",
                "reason": "invoice_not_found",
                "failures": 3,
                "window_minutes": 10,
            },
        )

    @patch("apps.core.alerts.send_incident_alert")
    @patch("apps.core.alerts.send_incident_recovery")
    def test_record_threshold_incident_marks_incident_active_when_sent(
        self,
        send_incident_recovery_mock,
        send_incident_alert_mock,
    ):
        send_incident_alert_mock.return_value = True
        send_incident_recovery_mock.return_value = True
        incident = ThresholdIncidentSpec(
            key="payments.webhook.failures",
            title="Repeated payment webhook failures",
            recovery_title="Payment webhook failures recovered",
            severity="critical",
            fingerprint="payments.webhook.failures:EXPRESS_PAY:invoice_not_found",
            details={"provider": "EXPRESS_PAY", "reason": "invoice_not_found"},
        )

        self.assertTrue(
            record_threshold_incident(
                counter_key="incident:payments:webhook",
                threshold=1,
                window_seconds=600,
                incident=incident,
            ),
        )
        self.assertTrue(resolve_threshold_incident(incident=incident))
        send_incident_recovery_mock.assert_called_once()

    @patch("apps.core.alerts.send_incident_recovery")
    def test_resolve_threshold_incident_returns_false_when_incident_is_not_active(self, send_incident_recovery_mock):
        sent = resolve_threshold_incident(
            incident=ThresholdIncidentSpec(
                key="storage.s3.unavailable",
                title="Storage is unavailable",
                recovery_title="Storage recovered",
                severity="critical",
                fingerprint="storage.s3.unavailable:generate_presigned_download_url",
                details={"operation": "generate_presigned_download_url"},
            ),
        )

        self.assertFalse(sent)
        send_incident_recovery_mock.assert_not_called()

    @patch("apps.core.alerts.send_incident_alert")
    @patch("apps.core.alerts.send_incident_recovery")
    def test_resolve_threshold_incident_sends_recovery_and_clears_active_state(
        self,
        send_incident_recovery_mock,
        send_incident_alert_mock,
    ):
        send_incident_alert_mock.return_value = True
        send_incident_recovery_mock.return_value = True
        incident = ThresholdIncidentSpec(
            key="storage.s3.unavailable",
            title="Storage is unavailable",
            recovery_title="Storage recovered",
            severity="critical",
            fingerprint="storage.s3.unavailable:generate_presigned_download_url",
            details={"operation": "generate_presigned_download_url"},
        )

        record_threshold_incident(
            counter_key="storage-unavailable:generate_presigned_download_url",
            threshold=1,
            window_seconds=600,
            incident=incident,
        )

        first = resolve_threshold_incident(incident=incident, details={"bucket": "products"})
        second = resolve_threshold_incident(incident=incident, details={"bucket": "products"})

        self.assertTrue(first)
        self.assertFalse(second)
        send_incident_recovery_mock.assert_called_once_with(
            key="storage.s3.unavailable",
            title="Storage recovered",
            fingerprint="storage.s3.unavailable:generate_presigned_download_url",
            details={
                "operation": "generate_presigned_download_url",
                "bucket": "products",
            },
        )

    def test_resolve_threshold_incident_requires_recovery_title(self):
        with self.assertRaisesMessage(ValueError, "incident recovery_title must be configured"):
            resolve_threshold_incident(
                incident=ThresholdIncidentSpec(
                    key="storage.s3.unavailable",
                    title="Storage is unavailable",
                    severity="critical",
                    fingerprint="storage.s3.unavailable:generate_presigned_download_url",
                ),
            )

    def test_record_threshold_incident_rejects_non_positive_threshold(self):
        with self.assertRaisesMessage(ValueError, "threshold must be greater than zero"):
            record_threshold_incident(
                counter_key="incident:payments:webhook",
                threshold=0,
                window_seconds=600,
                incident=ThresholdIncidentSpec(
                    key="payments.webhook.failures",
                    title="Repeated payment webhook failures",
                ),
            )

    def test_record_threshold_incident_rejects_non_positive_window(self):
        with self.assertRaisesMessage(ValueError, "window_seconds must be greater than zero"):
            record_threshold_incident(
                counter_key="incident:payments:webhook",
                threshold=3,
                window_seconds=0,
                incident=ThresholdIncidentSpec(
                    key="payments.webhook.failures",
                    title="Repeated payment webhook failures",
                ),
            )


class AnalyticsTemplateTests(TestCase):
    @override_settings(
        ANALYTICS_ENABLED=True,
        GTM_ID="GTM-TEST123",
        COOKIE_CONSENT_POLICY_VERSION=1,
        COOKIE_CONSENT_MAX_AGE_SECONDS=15552000,
        YANDEX_METRIKA_ID="YANDEX-TEST-123",
    )
    def test_analytics_context_processor_exposes_gtm_settings(self):
        context = analytics(HttpRequest())

        self.assertEqual(
            context,
            {
                "analytics_enabled": True,
                "gtm_id": "GTM-TEST123",
                "cookie_consent_policy_version": 1,
                "cookie_consent_max_age_seconds": 15552000,
                "cookie_consent_ui_enabled": True,
                "yandex_metrika_id": "YANDEX-TEST-123",
            },
        )

    @override_settings(
        ANALYTICS_ENABLED=False,
        GTM_ID="GTM-TEST123",
        COOKIE_CONSENT_POLICY_VERSION=2,
        COOKIE_CONSENT_MAX_AGE_SECONDS=3600,
        YANDEX_METRIKA_ID="",
    )
    def test_analytics_context_processor_disables_ui_when_analytics_off(self):
        context = analytics(HttpRequest())

        self.assertEqual(
            context,
            {
                "analytics_enabled": False,
                "gtm_id": "GTM-TEST123",
                "cookie_consent_policy_version": 2,
                "cookie_consent_max_age_seconds": 3600,
                "cookie_consent_ui_enabled": False,
                "yandex_metrika_id": "",
            },
        )

    @override_settings(ANALYTICS_ENABLED=True, GTM_ID="GTM-TEST123")
    def test_home_renders_gtm_and_analytics_script_when_enabled(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "googletagmanager.com/gtm.js?id=")
        self.assertContains(response, "GTM-TEST123")
        self.assertContains(response, "googletagmanager.com/ns.html?id=GTM-TEST123")
        self.assertContains(response, '/static/js/analytics.js')
        self.assertContains(response, 'data-user-type="guest"')

    @override_settings(ANALYTICS_ENABLED=False, GTM_ID="GTM-TEST123")
    def test_home_does_not_render_gtm_when_disabled(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "googletagmanager.com/gtm.js?id=GTM-TEST123")
        self.assertNotContains(response, '/static/js/analytics.js')


@override_settings(COOKIE_CONSENT_POLICY_VERSION=7)
class CookieConsentApiTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client(enforce_csrf_checks=True)

    def _csrf_token(self) -> str:
        self.client.get(reverse("home"))
        return self.client.cookies["csrftoken"].value

    def test_get_returns_policy_version_and_consent_state(self):
        response = self.client.get(reverse("cookie-consent-api"))

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["policy_version"], 7)
        self.assertIsNone(payload["analytics_storage_consent"])

    def test_post_persists_analytics_choice_in_session(self):
        token = self._csrf_token()
        response = self.client.post(
            reverse("cookie-consent-api"),
            data=json.dumps({"analytics_storage": True, "policy_version": 7}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content.decode()), {"ok": True})
        session = self.client.session
        self.assertIs(session[SESSION_KEY_ANALYTICS_STORAGE], True)
        self.assertEqual(session[SESSION_KEY_CONSENT_POLICY_VERSION], 7)

    def test_post_false_clears_analytics_consent(self):
        token = self._csrf_token()
        session = self.client.session
        session[SESSION_KEY_ANALYTICS_STORAGE] = True
        session.save()

        response = self.client.post(
            reverse("cookie-consent-api"),
            data=json.dumps({"analytics_storage": False, "policy_version": 7}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIs(self.client.session[SESSION_KEY_ANALYTICS_STORAGE], False)

    def test_post_rejects_policy_version_mismatch(self):
        token = self._csrf_token()
        response = self.client.post(
            reverse("cookie-consent-api"),
            data=json.dumps({"analytics_storage": True, "policy_version": 1}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.content.decode()),
            {"error": "policy_version_mismatch"},
        )

    def test_post_rejects_invalid_json(self):
        token = self._csrf_token()
        response = self.client.post(
            reverse("cookie-consent-api"),
            data="{not json",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content.decode()), {"error": "invalid_json"})

    def test_post_rejects_non_bool_analytics_storage(self):
        token = self._csrf_token()
        response = self.client.post(
            reverse("cookie-consent-api"),
            data=json.dumps({"analytics_storage": "yes", "policy_version": 7}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.content.decode()),
            {"error": "invalid_analytics_storage"},
        )

    def test_post_rejects_without_csrf_header(self):
        self.client.get(reverse("home"))
        response = self.client.post(
            reverse("cookie-consent-api"),
            data=json.dumps({"analytics_storage": True, "policy_version": 7}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)


@override_settings(SITE_BASE_URL="https://example.com")
class SeoTemplateTests(TestCase):
    def test_home_renders_basic_seo_meta_tags(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>PaliGames — развивающие игры для детей</title>", html=True)
        self.assertContains(response, '<link rel="canonical" href="https://example.com/" />', html=True)
        self.assertContains(
            response,
            'property="og:title" content="PaliGames — развивающие игры для детей"',
            html=False,
        )

    def test_robots_txt_references_sitemap(self):
        response = self.client.get(reverse("robots-txt"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sitemap: https://example.com/sitemap.xml")

    def test_sitemap_lists_static_pages(self):
        response = self.client.get(reverse("sitemap-xml"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<loc>https://example.com/</loc>", html=False)
        self.assertContains(response, "<loc>https://example.com/catalog/</loc>", html=False)
        self.assertContains(response, "<loc>https://example.com/privacy/</loc>", html=False)
        self.assertContains(response, "<loc>https://example.com/cookies/</loc>", html=False)


class PurchaseAnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(
            title="Analytics product",
            slug="analytics-product",
            price=Decimal("25.00"),
        )
        cls.order = Order.objects.create(
            email="analytics@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("22.50"),
            items_count=1,
            promo_code_snapshot="WELCOME10",
            currency=933,
            analytics_storage_consent=True,
        )
        Invoice.objects.create(
            order=cls.order,
            provider_invoice_no="99990001",
            status=Invoice.InvoiceStatus.PAID,
            invoice_url="https://example.com/pay/99990001",
            amount=Decimal("22.50"),
            currency=933,
        )
        OrderItem.objects.create(
            order=cls.order,
            product=cls.product,
            title_snapshot=cls.product.title,
            category_snapshot="Игры",
            unit_price_amount=Decimal("25.00"),
            quantity=1,
            line_total_amount=Decimal("25.00"),
            discount_amount=Decimal("2.50"),
            discounted_line_total_amount=Decimal("22.50"),
            product_slug_snapshot=cls.product.slug,
            product_image_snapshot="https://example.com/product.png",
        )

    @override_settings(
        ANALYTICS_ENABLED=True,
        GA4_MEASUREMENT_ID="G-TEST123",
        GA4_API_SECRET="ga4-secret",
    )
    @patch("apps.core.analytics.httpx.post")
    def test_send_ga4_purchase_event_for_order_posts_expected_payload(self, httpx_post_mock):
        response_mock = httpx_post_mock.return_value
        response_mock.raise_for_status.return_value = None

        send_ga4_purchase_event_for_order(order_id=self.order.id, source="notification")

        httpx_post_mock.assert_called_once()
        self.assertEqual(
            httpx_post_mock.call_args.kwargs["params"],
            {
                "measurement_id": "G-TEST123",
                "api_secret": "ga4-secret",
            },
        )
        payload = httpx_post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["events"][0]["name"], "purchase")
        self.assertEqual(payload["events"][0]["params"]["transaction_id"], str(self.order.public_id))
        self.assertEqual(payload["events"][0]["params"]["value"], 22.5)
        self.assertEqual(payload["events"][0]["params"]["coupon"], "WELCOME10")
        self.assertEqual(payload["events"][0]["params"]["items"][0]["item_name"], self.product.title)

    @override_settings(
        ANALYTICS_ENABLED=False,
        GA4_MEASUREMENT_ID="",
        GA4_API_SECRET="",
    )
    @patch("apps.core.analytics.httpx.post")
    def test_send_ga4_purchase_event_for_order_skips_when_ga4_disabled(self, httpx_post_mock):
        send_ga4_purchase_event_for_order(order_id=self.order.id, source="notification")

        httpx_post_mock.assert_not_called()

    @override_settings(
        ANALYTICS_ENABLED=True,
        GA4_MEASUREMENT_ID="G-TEST123",
        GA4_API_SECRET="ga4-secret",
    )
    @patch("apps.core.analytics.httpx.post")
    def test_send_ga4_purchase_event_skips_without_storage_consent(self, httpx_post_mock):
        declined = Order.objects.create(
            email="declined@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
            items_count=0,
            currency=933,
            analytics_storage_consent=False,
        )

        send_ga4_purchase_event_for_order(order_id=declined.id, source="notification")

        httpx_post_mock.assert_not_called()


class HealthViewsTests(TestCase):
    def test_health_live_returns_ok(self):
        response = self.client.get(reverse("health-live"))

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"status": "ok"})

    @patch("apps.core.views.build_readiness_report")
    def test_health_ready_returns_ok_when_all_dependencies_are_available(self, build_readiness_report_mock):
        build_readiness_report_mock.return_value = {
            "database": {"status": "ok"},
            "redis": {"status": "ok"},
            "s3": {"status": "ok"},
        }

        response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "status": "ok",
                "checks": {
                    "database": {"status": "ok"},
                    "redis": {"status": "ok"},
                    "s3": {"status": "ok"},
                },
            },
        )

    @patch("apps.core.views.build_readiness_report")
    def test_health_ready_returns_503_when_any_dependency_is_unavailable(self, build_readiness_report_mock):
        build_readiness_report_mock.return_value = {
            "database": {"status": "ok"},
            "redis": {"status": "failed", "error": "ConnectionError"},
            "s3": {"status": "ok"},
        }

        response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertJSONEqual(
            response.content,
            {
                "status": "degraded",
                "checks": {
                    "database": {"status": "ok"},
                    "redis": {"status": "failed", "error": "ConnectionError"},
                    "s3": {"status": "ok"},
                },
            },
        )

    @patch("apps.core.views.metrics_response")
    def test_metrics_endpoint_returns_prometheus_payload(self, metrics_response_mock):
        metrics_response_mock.return_value = (b"test_metric 1\n", "text/plain; version=0.0.4; charset=utf-8")

        response = self.client.get(reverse("metrics"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; version=0.0.4; charset=utf-8")
        self.assertEqual(response.content, b"test_metric 1\n")


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-default",
        },
        "rate_limit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-rate-limit",
        },
    },
)
class RateLimitTests(SimpleTestCase):
    def test_check_rate_limit_blocks_after_limit(self):
        first = check_rate_limit(
            scope=RateLimitScope.CHECKOUT_CREATE,
            identifier="user@example.com",
            limit=2,
            window_seconds=60,
        )
        second = check_rate_limit(
            scope=RateLimitScope.CHECKOUT_CREATE,
            identifier="user@example.com",
            limit=2,
            window_seconds=60,
        )
        third = check_rate_limit(
            scope=RateLimitScope.CHECKOUT_CREATE,
            identifier="user@example.com",
            limit=2,
            window_seconds=60,
        )

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertFalse(third.allowed)
        self.assertEqual(third.count, 3)
        self.assertEqual(third.limit, 2)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "auth-rate-limit-test-default",
        },
        "rate_limit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "auth-rate-limit-test-rate-limit",
        },
    },
    AUTH_LOGIN_EMAIL_RATE_LIMIT=1,
    AUTH_LOGIN_EMAIL_RATE_LIMIT_WINDOW_SECONDS=600,
    AUTH_LOGIN_IP_RATE_LIMIT=100,
    AUTH_LOGIN_IP_RATE_LIMIT_WINDOW_SECONDS=600,
    AUTH_SIGNUP_EMAIL_RATE_LIMIT=1,
    AUTH_SIGNUP_EMAIL_RATE_LIMIT_WINDOW_SECONDS=3600,
    AUTH_SIGNUP_IP_RATE_LIMIT=100,
    AUTH_SIGNUP_IP_RATE_LIMIT_WINDOW_SECONDS=3600,
    AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT=1,
    AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT_WINDOW_SECONDS=3600,
    AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT=100,
    AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT_WINDOW_SECONDS=3600,
    AUTH_PASSWORD_RESET_CONFIRM_KEY_RATE_LIMIT=1,
    AUTH_PASSWORD_RESET_CONFIRM_KEY_RATE_LIMIT_WINDOW_SECONDS=600,
    AUTH_PASSWORD_RESET_CONFIRM_IP_RATE_LIMIT=100,
    AUTH_PASSWORD_RESET_CONFIRM_IP_RATE_LIMIT_WINDOW_SECONDS=600,
)
class AuthRateLimitMiddlewareTests(TestCase):
    def setUp(self):
        caches["rate_limit"].clear()

    def test_auth_login_enforces_email_rate_limit(self):
        first = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": "user@example.com", "password": "wrong"}),
            content_type="application/json",
        )
        second = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": "user@example.com", "password": "wrong"}),
            content_type="application/json",
        )

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "600")

    @patch("apps.core.rate_limit_middleware.log_event")
    def test_auth_login_rate_limit_is_logged(self, log_event_mock):
        self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": "user@example.com", "password": "wrong"}),
            content_type="application/json",
        )

        self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": "user@example.com", "password": "wrong"}),
            content_type="application/json",
        )

        log_event_mock.assert_called_once()
        self.assertEqual(log_event_mock.call_args.args[2], "auth.rate_limit.triggered")
        self.assertEqual(log_event_mock.call_args.kwargs["scope"], RateLimitScope.AUTH_LOGIN)
        self.assertEqual(log_event_mock.call_args.kwargs["identifier_type"], "email")

    @patch("apps.core.rate_limit_middleware.inc_auth_rate_limit_triggered")
    def test_auth_login_rate_limit_increments_metric(self, inc_auth_rate_limit_triggered_mock):
        self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": "user@example.com", "password": "wrong"}),
            content_type="application/json",
        )

        self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": "user@example.com", "password": "wrong"}),
            content_type="application/json",
        )

        inc_auth_rate_limit_triggered_mock.assert_called_once_with(
            scope=RateLimitScope.AUTH_LOGIN,
            identifier_type="email",
        )

    def test_auth_signup_enforces_email_rate_limit(self):
        first = self.client.post(
            "/_allauth/browser/v1/auth/signup",
            data=json.dumps(
                {
                    "email": "new@example.com",
                    "password1": "test-password-123",
                    "password2": "test-password-123",
                },
            ),
            content_type="application/json",
        )
        second = self.client.post(
            "/_allauth/browser/v1/auth/signup",
            data=json.dumps(
                {
                    "email": "new@example.com",
                    "password1": "test-password-123",
                    "password2": "test-password-123",
                },
            ),
            content_type="application/json",
        )

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "3600")
        self.assertJSONEqual(
            second.content,
            {
                "errors": [
                    {
                        "message": "Слишком много попыток регистрации. Попробуйте позже.",
                        "code": "rate_limited",
                    },
                ],
            },
        )

    @override_settings(
        AUTH_SIGNUP_EMAIL_RATE_LIMIT=100,
        AUTH_SIGNUP_EMAIL_RATE_LIMIT_WINDOW_SECONDS=3600,
        AUTH_SIGNUP_IP_RATE_LIMIT=1,
        AUTH_SIGNUP_IP_RATE_LIMIT_WINDOW_SECONDS=3600,
    )
    def test_auth_signup_enforces_ip_rate_limit(self):
        first = self.client.post(
            "/_allauth/browser/v1/auth/signup",
            data=json.dumps(
                {
                    "email": "first-new@example.com",
                    "password1": "test-password-123",
                    "password2": "test-password-123",
                },
            ),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.40",
        )
        second = self.client.post(
            "/_allauth/browser/v1/auth/signup",
            data=json.dumps(
                {
                    "email": "second-new@example.com",
                    "password1": "test-password-123",
                    "password2": "test-password-123",
                },
            ),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.40",
        )

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "3600")
        self.assertJSONEqual(
            second.content,
            {
                "errors": [
                    {
                        "message": "Слишком много попыток регистрации. Попробуйте позже.",
                        "code": "rate_limited",
                    },
                ],
            },
        )

    def test_auth_password_reset_request_enforces_email_rate_limit(self):
        first = self.client.post(
            "/_allauth/browser/v1/auth/password/request",
            data=json.dumps({"email": "reset@example.com"}),
            content_type="application/json",
        )
        second = self.client.post(
            "/_allauth/browser/v1/auth/password/request",
            data=json.dumps({"email": "reset@example.com"}),
            content_type="application/json",
        )

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "3600")
        self.assertJSONEqual(
            second.content,
            {
                "errors": [
                    {
                        "message": "Слишком много запросов сброса пароля. Попробуйте позже.",
                        "code": "rate_limited",
                    },
                ],
            },
        )

    @override_settings(
        AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT=100,
        AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT_WINDOW_SECONDS=3600,
        AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT=1,
        AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT_WINDOW_SECONDS=3600,
    )
    def test_auth_password_reset_request_enforces_ip_rate_limit(self):
        first = self.client.post(
            "/_allauth/browser/v1/auth/password/request",
            data=json.dumps({"email": "first-reset@example.com"}),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.50",
        )
        second = self.client.post(
            "/_allauth/browser/v1/auth/password/request",
            data=json.dumps({"email": "second-reset@example.com"}),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.50",
        )

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "3600")
        self.assertJSONEqual(
            second.content,
            {
                "errors": [
                    {
                        "message": "Слишком много запросов сброса пароля. Попробуйте позже.",
                        "code": "rate_limited",
                    },
                ],
            },
        )

    def test_auth_password_reset_confirm_enforces_key_rate_limit(self):
        first = self.client.post(
            "/_allauth/browser/v1/auth/password/reset",
            data=json.dumps({"key": "test-reset-key", "password": "test-password-123"}),
            content_type="application/json",
        )
        second = self.client.post(
            "/_allauth/browser/v1/auth/password/reset",
            data=json.dumps({"key": "test-reset-key", "password": "test-password-123"}),
            content_type="application/json",
        )

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "600")
        self.assertJSONEqual(
            second.content,
            {
                "errors": [
                    {
                        "message": "Слишком много попыток сброса пароля. Попробуйте позже.",
                        "code": "rate_limited",
                    },
                ],
            },
        )

    @override_settings(
        AUTH_PASSWORD_RESET_CONFIRM_KEY_RATE_LIMIT=100,
        AUTH_PASSWORD_RESET_CONFIRM_KEY_RATE_LIMIT_WINDOW_SECONDS=600,
        AUTH_PASSWORD_RESET_CONFIRM_IP_RATE_LIMIT=1,
        AUTH_PASSWORD_RESET_CONFIRM_IP_RATE_LIMIT_WINDOW_SECONDS=600,
    )
    def test_auth_password_reset_confirm_enforces_ip_rate_limit(self):
        first = self.client.post(
            "/_allauth/browser/v1/auth/password/reset",
            data=json.dumps({"key": "first-test-reset-key", "password": "test-password-123"}),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.60",
        )
        second = self.client.post(
            "/_allauth/browser/v1/auth/password/reset",
            data=json.dumps({"key": "second-test-reset-key", "password": "test-password-123"}),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.60",
        )

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "600")
        self.assertJSONEqual(
            second.content,
            {
                "errors": [
                    {
                        "message": "Слишком много попыток сброса пароля. Попробуйте позже.",
                        "code": "rate_limited",
                    },
                ],
            },
        )

    @override_settings(
        AUTH_LOGIN_EMAIL_RATE_LIMIT=100,
        AUTH_LOGIN_EMAIL_RATE_LIMIT_WINDOW_SECONDS=600,
        AUTH_LOGIN_IP_RATE_LIMIT=1,
        AUTH_LOGIN_IP_RATE_LIMIT_WINDOW_SECONDS=600,
    )
    def test_auth_login_enforces_ip_rate_limit(self):
        first = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": "first@example.com", "password": "wrong"}),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.30",
        )
        second = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": "second@example.com", "password": "wrong"}),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.30",
        )

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "600")
