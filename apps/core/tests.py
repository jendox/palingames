import json
import logging

from django.test import TestCase
from django.urls import reverse

from apps.core.logging import (
    JsonFormatter,
    LoggingContextFilter,
    build_task_logging_headers,
    clear_logging_context,
    set_logging_context,
)


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

    def test_build_task_logging_headers_uses_request_id_from_context(self):
        set_logging_context(request_id="req-456")

        self.assertEqual(build_task_logging_headers(), {"request_id": "req-456"})

    def test_request_middleware_sets_request_id_header(self):
        response = self.client.get(reverse("home"), HTTP_X_REQUEST_ID="req-from-client")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Request-ID"], "req-from-client")
