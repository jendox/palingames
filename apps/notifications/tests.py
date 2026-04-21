from unittest.mock import patch

from django.test import TestCase

from apps.notifications.models import NotificationOutbox
from apps.notifications.services import (
    create_notification_outbox,
    enqueue_notification_outbox,
    process_notification_outbox,
)
from apps.notifications.types import GUEST_ORDER_DOWNLOAD


class NotificationOutboxLoggingTests(TestCase):
    @patch("apps.notifications.services.log_event")
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_enqueue_notification_outbox_logs_enqueued_event(self, delay_mock, log_event_mock):
        outbox = create_notification_outbox(
            notification_type=GUEST_ORDER_DOWNLOAD,
            recipient="guest@example.com",
            payload=[],
        )

        enqueue_notification_outbox(outbox)

        self.assertEqual(log_event_mock.call_args.args[2], "notification.outbox.enqueued")
        self.assertEqual(log_event_mock.call_args.kwargs["outbox_id"], outbox.id)

    @patch("apps.notifications.services.log_event")
    @patch("apps.notifications.services.send_notification")
    def test_process_notification_outbox_logs_processing_started_event(self, send_notification_mock, log_event_mock):
        outbox = create_notification_outbox(
            notification_type=GUEST_ORDER_DOWNLOAD,
            recipient="guest@example.com",
            payload=[],
        )

        process_notification_outbox(outbox_id=outbox.id)

        events = [call.args[2] for call in log_event_mock.call_args_list]
        self.assertIn("notification.outbox.processing.started", events)
        self.assertIn("notification.outbox.sent", events)
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        send_notification_mock.assert_called_once()
