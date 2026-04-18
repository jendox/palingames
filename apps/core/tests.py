import json
import logging
from contextlib import contextmanager
from unittest.mock import patch

from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

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
