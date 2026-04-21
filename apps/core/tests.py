import json
import logging
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import caches
from django.http import HttpRequest
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.core.analytics import send_ga4_purchase_event_for_order
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


class AnalyticsTemplateTests(TestCase):
    @override_settings(ANALYTICS_ENABLED=True, GTM_ID="GTM-TEST123")
    def test_analytics_context_processor_exposes_gtm_settings(self):
        context = analytics(HttpRequest())

        self.assertEqual(
            context,
            {
                "analytics_enabled": True,
                "gtm_id": "GTM-TEST123",
            },
        )

    @override_settings(ANALYTICS_ENABLED=True, GTM_ID="GTM-TEST123")
    def test_home_renders_gtm_and_analytics_script_when_enabled(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "googletagmanager.com/gtm.js?id=GTM-TEST123")
        self.assertContains(response, "googletagmanager.com/ns.html?id=GTM-TEST123")
        self.assertContains(response, '/static/js/analytics.js')
        self.assertContains(response, 'data-user-type="guest"')

    @override_settings(ANALYTICS_ENABLED=False, GTM_ID="GTM-TEST123")
    def test_home_does_not_render_gtm_when_disabled(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "googletagmanager.com/gtm.js?id=GTM-TEST123")
        self.assertNotContains(response, '/static/js/analytics.js')


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
