from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.custom_games.models import CustomGameRequest
from apps.emails.models import EmailLog
from apps.notifications.destinations import TelegramDestination
from apps.notifications.formatters import format_custom_game_request_paid_admin_telegram
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import (
    create_notification_outbox,
    encrypt_outbox_payload,
    enqueue_notification_outbox,
    process_notification_outbox,
)
from apps.notifications.telegram import (
    TelegramConfigurationError,
    get_telegram_destination_skip_reason,
    get_telegram_route,
    publish_telegram_outbound,
)
from apps.notifications.telegram_delivery import (
    _read_feedback_stream,
    confirm_telegram_outbox_delivery,
    fail_telegram_outbox_delivery,
    process_telegram_outbound_feedback,
    reap_stuck_telegram_outbox_deliveries,
)
from apps.notifications.types import NotificationType
from apps.payments.models import Invoice


class NotificationOutboxLoggingTests(TestCase):
    @patch("apps.notifications.services.log_event")
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_enqueue_notification_outbox_logs_enqueued_event(self, delay_mock, log_event_mock):
        outbox = create_notification_outbox(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            recipient="guest@example.com",
            payload=[],
        )

        enqueue_notification_outbox(outbox)

        self.assertEqual(log_event_mock.call_args.args[2], "notification.outbox.enqueued")
        self.assertEqual(log_event_mock.call_args.kwargs["outbox_id"], outbox.id)

    @patch("apps.notifications.services.resolve_notification_outbox_failure_incident")
    @patch("apps.notifications.services.log_event")
    @patch("apps.notifications.services.send_notification")
    def test_process_notification_outbox_logs_processing_started_event(
        self,
        send_notification_mock,
        log_event_mock,
        resolve_notification_outbox_failure_incident_mock,
    ):
        outbox = create_notification_outbox(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
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
        resolve_notification_outbox_failure_incident_mock.assert_called_once_with(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            channel=NotificationOutbox.Channel.EMAIL,
        )

    @patch("apps.notifications.services.record_notification_outbox_failure_incident")
    @patch("apps.notifications.services.send_notification", side_effect=RuntimeError("boom"))
    def test_process_notification_outbox_records_incident_for_critical_failures(
        self,
        send_notification_mock,
        record_notification_outbox_failure_incident_mock,
    ):
        outbox = create_notification_outbox(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            recipient="guest@example.com",
            payload=[],
        )

        with self.assertRaises(RuntimeError):
            process_notification_outbox(outbox_id=outbox.id)

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.FAILED)
        record_notification_outbox_failure_incident_mock.assert_called_once_with(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            channel=NotificationOutbox.Channel.EMAIL,
        )

    @patch("apps.notifications.services.record_notification_outbox_failure_incident")
    @patch("apps.notifications.services.send_notification", side_effect=RuntimeError("boom"))
    def test_process_notification_outbox_records_incident_for_invoice_created_user_failures(
        self,
        send_notification_mock,
        record_notification_outbox_failure_incident_mock,
    ):
        outbox = create_notification_outbox(
            notification_type=NotificationType.INVOICE_CREATED_USER,
            recipient="guest@example.com",
            payload={"invoice_id": 1},
        )

        with self.assertRaises(RuntimeError):
            process_notification_outbox(outbox_id=outbox.id)

        record_notification_outbox_failure_incident_mock.assert_called_once_with(
            notification_type=NotificationType.INVOICE_CREATED_USER,
            channel=NotificationOutbox.Channel.EMAIL,
        )

    @patch("apps.notifications.services.record_notification_outbox_failure_incident")
    @patch("apps.notifications.services.send_notification", side_effect=RuntimeError("boom"))
    def test_process_notification_outbox_records_incident_for_auth_account_email_failures(
        self,
        send_notification_mock,
        record_notification_outbox_failure_incident_mock,
    ):
        outbox = create_notification_outbox(
            notification_type=NotificationType.AUTH_ACCOUNT_EMAIL,
            recipient="user@example.com",
            payload={"template_prefix": "account/email/email_confirmation", "key": "abc"},
        )

        with self.assertRaises(RuntimeError):
            process_notification_outbox(outbox_id=outbox.id)

        record_notification_outbox_failure_incident_mock.assert_called_once_with(
            notification_type=NotificationType.AUTH_ACCOUNT_EMAIL,
            channel=NotificationOutbox.Channel.EMAIL,
        )

    @patch("apps.notifications.services.record_notification_outbox_failure_incident")
    @patch("apps.notifications.services.send_notification", side_effect=RuntimeError("boom"))
    def test_process_notification_outbox_skips_incident_for_non_critical_failures(
        self,
        send_notification_mock,
        record_notification_outbox_failure_incident_mock,
    ):
        outbox = create_notification_outbox(
            notification_type=NotificationType.ORDER_REWARD_USER,
            recipient="user@example.com",
            payload={},
        )

        with self.assertRaises(RuntimeError):
            process_notification_outbox(outbox_id=outbox.id)

        record_notification_outbox_failure_incident_mock.assert_called_once_with(
            notification_type=NotificationType.ORDER_REWARD_USER,
            channel=NotificationOutbox.Channel.EMAIL,
        )


class NotificationOutboxProcessingRecoveryTests(TestCase):
    @override_settings(NOTIFICATION_OUTBOX_PROCESSING_TIMEOUT_MINUTES=15)
    @patch("apps.notifications.services.send_notification")
    def test_process_notification_outbox_skips_fresh_processing(self, send_notification_mock):
        outbox = NotificationOutbox.objects.create(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            channel=NotificationOutbox.Channel.EMAIL,
            recipient="guest@example.com",
            payload_encrypted=b"{}",
            status=NotificationOutbox.Status.PROCESSING,
            attempts=1,
            last_attempt_at=timezone.now(),
        )

        processed = process_notification_outbox(outbox_id=outbox.id)

        self.assertFalse(processed)
        send_notification_mock.assert_not_called()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.PROCESSING)
        self.assertEqual(outbox.attempts, 1)

    @override_settings(NOTIFICATION_OUTBOX_PROCESSING_TIMEOUT_MINUTES=15)
    @patch("apps.notifications.services.resolve_notification_outbox_failure_incident")
    def test_process_notification_outbox_reconciles_stale_processing_from_email_log(
        self,
        resolve_incident_mock,
    ):
        sent_at = timezone.now() - timedelta(minutes=30)
        outbox = NotificationOutbox.objects.create(
            notification_type=NotificationType.INVOICE_CREATED_USER,
            channel=NotificationOutbox.Channel.EMAIL,
            recipient="guest@example.com",
            payload_encrypted=b"{}",
            status=NotificationOutbox.Status.PROCESSING,
            attempts=1,
            last_attempt_at=timezone.now() - timedelta(minutes=20),
        )
        EmailLog.objects.create(
            notification_outbox=outbox,
            recipient="guest@example.com",
            subject="Pay",
            notification_type=NotificationType.INVOICE_CREATED_USER,
            status=EmailLog.Status.SENT,
            sent_at=sent_at,
        )

        processed = process_notification_outbox(outbox_id=outbox.id)

        self.assertTrue(processed)
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(outbox.sent_at, sent_at)
        resolve_incident_mock.assert_called_once_with(
            notification_type=NotificationType.INVOICE_CREATED_USER,
            channel=NotificationOutbox.Channel.EMAIL,
        )

    @override_settings(NOTIFICATION_OUTBOX_PROCESSING_TIMEOUT_MINUTES=15)
    @patch("apps.notifications.services.resolve_notification_outbox_failure_incident")
    @patch("apps.notifications.services.send_notification")
    def test_process_notification_outbox_retries_stale_processing_without_email_log(
        self,
        send_notification_mock,
        resolve_incident_mock,
    ):
        outbox = NotificationOutbox.objects.create(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            channel=NotificationOutbox.Channel.EMAIL,
            recipient="guest@example.com",
            payload_encrypted=encrypt_outbox_payload([]),
            status=NotificationOutbox.Status.PROCESSING,
            attempts=2,
            last_attempt_at=timezone.now() - timedelta(minutes=20),
        )

        processed = process_notification_outbox(outbox_id=outbox.id)

        self.assertTrue(processed)
        send_notification_mock.assert_called_once()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(outbox.attempts, 3)
        resolve_incident_mock.assert_called_once()


class TelegramRouteTests(TestCase):
    @override_settings(
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
        TELEGRAM_SUPPORT_THREAD_ID=7,
    )
    def test_get_telegram_route_returns_notifications_topic(self):
        route = get_telegram_route(TelegramDestination.NOTIFICATIONS)

        self.assertEqual(route.chat_id, "-1001234567890")
        self.assertEqual(route.message_thread_id, 3)

    @override_settings(
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=0,
    )
    def test_get_telegram_route_raises_for_missing_thread(self):
        with self.assertRaises(TelegramConfigurationError):
            get_telegram_route(TelegramDestination.NOTIFICATIONS)


class TelegramDestinationSkipReasonTests(TestCase):
    @override_settings(
        TELEGRAM_REDIS_URL="",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    def test_returns_reason_when_redis_is_missing(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.NOTIFICATIONS)

        self.assertEqual(reason, "telegram_redis_not_configured")

    @override_settings(
        TELEGRAM_REDIS_URL="redis://localhost:6379/1",
        TELEGRAM_FORUM_CHAT_ID="",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    def test_returns_reason_when_forum_chat_is_missing(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.NOTIFICATIONS)

        self.assertEqual(reason, "telegram_forum_chat_not_configured")

    @override_settings(
        TELEGRAM_REDIS_URL="redis://localhost:6379/1",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=0,
    )
    def test_returns_reason_when_notifications_thread_is_missing(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.NOTIFICATIONS)

        self.assertEqual(reason, "telegram_notifications_route_not_configured")

    @override_settings(
        TELEGRAM_REDIS_URL="redis://localhost:6379/1",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_SUPPORT_THREAD_ID=0,
    )
    def test_returns_reason_when_support_thread_is_missing(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.SUPPORT)

        self.assertEqual(reason, "telegram_support_route_not_configured")

    @override_settings(
        TELEGRAM_REDIS_URL="redis://localhost:6379/1",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
        TELEGRAM_SUPPORT_THREAD_ID=7,
    )
    def test_returns_none_when_destination_is_fully_configured(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.NOTIFICATIONS)

        self.assertIsNone(reason)


@override_settings(SITE_BASE_URL="https://example.com")
class CustomGameRequestPaidAdminTelegramTests(TestCase):
    def setUp(self):
        self.custom_game_request = CustomGameRequest.objects.create(
            contact_name="Анна",
            contact_email="custom@example.com",
            subject="Космос",
            idea="Нужна игра про космос",
            audience="Дети 6-8 лет",
            page_count="8",
            quoted_price=Decimal("80.00"),
            deadline=timezone.localdate() + timedelta(days=7),
            status=CustomGameRequest.Status.IN_PROGRESS,
        )
        self.invoice = Invoice.objects.create(
            custom_game_request=self.custom_game_request,
            provider_invoice_no="87654321",
            status=Invoice.InvoiceStatus.PAID,
            amount=Decimal("80.00"),
            currency=933,
            paid_at=timezone.now(),
        )

    def test_format_custom_game_request_paid_admin_telegram_includes_payment_details(self):
        text = format_custom_game_request_paid_admin_telegram(
            custom_game_request=self.custom_game_request,
            invoice=self.invoice,
        )

        self.assertIn("Оплачена заявка на игру", text)
        self.assertIn(self.custom_game_request.payment_account_no, text)
        self.assertIn("80.00 BYN", text)
        self.assertIn("В работе", text)
        self.assertIn("/admin/custom_games/customgamerequest/", text)

    @override_settings(
        TELEGRAM_REDIS_URL="redis://localhost:6379/1",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    @patch("apps.notifications.handlers.send_telegram_message")
    def test_process_custom_game_request_paid_admin_telegram_notification(self, send_telegram_message_mock):
        outbox = create_notification_outbox(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_PAID_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
            recipient=TelegramDestination.NOTIFICATIONS.value,
            payload={
                "custom_game_request_id": self.custom_game_request.id,
                "invoice_id": self.invoice.id,
                "destination": TelegramDestination.NOTIFICATIONS.value,
            },
            target=self.custom_game_request,
        )

        self.assertTrue(process_notification_outbox(outbox_id=outbox.id))

        send_telegram_message_mock.assert_called_once()
        self.assertEqual(
            send_telegram_message_mock.call_args.kwargs["destination"],
            TelegramDestination.NOTIFICATIONS,
        )
        self.assertEqual(send_telegram_message_mock.call_args.kwargs["source"], "outbox")
        self.assertEqual(send_telegram_message_mock.call_args.kwargs["correlation_id"], str(outbox.id))
        self.assertIn("Оплачена заявка на игру", send_telegram_message_mock.call_args.kwargs["text"])
        self.assertIn("80.00 BYN", send_telegram_message_mock.call_args.kwargs["text"])
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.DELIVERING)


@override_settings(
    TELEGRAM_REDIS_URL="redis://localhost:6379/1",
    TELEGRAM_FORUM_CHAT_ID="-1001234567890",
    TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
)
class PaymentsMonthlyReportAdminTelegramTests(TestCase):
    @patch("apps.notifications.handlers.send_telegram_message")
    def test_process_payments_monthly_report_admin_telegram_notification(self, send_telegram_message_mock):
        outbox = create_notification_outbox(
            notification_type=NotificationType.PAYMENTS_MONTHLY_REPORT_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
            recipient="npd_monthly_report:2026-07:1/1",
            payload={
                "report_text": "<b>Отчет о платежах (для НПД)</b>",
                "destination": TelegramDestination.NOTIFICATIONS.value,
            },
            target=None,
        )

        self.assertTrue(process_notification_outbox(outbox_id=outbox.id))

        send_telegram_message_mock.assert_called_once()
        self.assertEqual(
            send_telegram_message_mock.call_args.kwargs["destination"],
            TelegramDestination.NOTIFICATIONS,
        )
        self.assertEqual(send_telegram_message_mock.call_args.kwargs["source"], "outbox")
        self.assertEqual(send_telegram_message_mock.call_args.kwargs["correlation_id"], str(outbox.id))
        self.assertIn("Отчет о платежах", send_telegram_message_mock.call_args.kwargs["text"])
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.DELIVERING)


@override_settings(
    TELEGRAM_REDIS_URL="redis://localhost:6379/1",
    TELEGRAM_OUTBOUND_STREAM="telegram:outbound",
)
class PublishTelegramOutboundTests(TestCase):
    @patch("apps.notifications.telegram.get_telegram_redis_client")
    def test_publish_telegram_outbound_writes_to_stream(self, redis_client_mock):
        redis_client = MagicMock()
        redis_client.xadd.return_value = "1-0"
        redis_client_mock.return_value = redis_client

        stream_id = publish_telegram_outbound(
            destination=TelegramDestination.NOTIFICATIONS,
            text="<b>Hello</b>",
            source="outbox",
            correlation_id="42",
        )

        self.assertEqual(stream_id, "1-0")
        redis_client.xadd.assert_called_once_with(
            "telegram:outbound",
            {
                "source": "outbox",
                "destination": "notifications",
                "text": "<b>Hello</b>",
                "parse_mode": "HTML",
                "correlation_id": "42",
            },
        )


@override_settings(
    TELEGRAM_REDIS_URL="redis://localhost:6379/1",
    TELEGRAM_OUTBOUND_ACK_STREAM="telegram:outbound:ack",
    TELEGRAM_OUTBOUND_FAILED_STREAM="telegram:outbound:failed",
    TELEGRAM_OUTBOUND_FEEDBACK_CONSUMER_GROUP="django-telegram-feedback",
)
class TelegramOutboxDeliveryFeedbackTests(TestCase):
    @patch("apps.notifications.telegram_delivery.get_telegram_redis_client")
    def test_read_feedback_stream_uses_non_blocking_xreadgroup(self, redis_client_mock):
        redis_client = MagicMock()
        redis_client.xreadgroup.return_value = None
        redis_client_mock.return_value = redis_client

        result = _read_feedback_stream(stream="telegram:outbound:ack")

        self.assertEqual(result, [])
        redis_client.xreadgroup.assert_called_once_with(
            groupname="django-telegram-feedback",
            consumername="django-celery",
            streams={"telegram:outbound:ack": ">"},
            count=100,
        )

    def _create_delivering_outbox(self) -> NotificationOutbox:
        return NotificationOutbox.objects.create(
            notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
            recipient=TelegramDestination.NOTIFICATIONS.value,
            payload_encrypted=b"{}",
            status=NotificationOutbox.Status.DELIVERING,
            attempts=1,
            last_attempt_at=timezone.now(),
        )

    @patch("apps.notifications.telegram_delivery.resolve_notification_outbox_failure_incident")
    def test_confirm_telegram_outbox_delivery_marks_sent(self, resolve_mock):
        outbox = self._create_delivering_outbox()

        confirmed = confirm_telegram_outbox_delivery(outbox_id=outbox.id)

        outbox.refresh_from_db()
        self.assertTrue(confirmed)
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertIsNotNone(outbox.sent_at)
        resolve_mock.assert_called_once()

    @patch("apps.notifications.telegram_delivery.record_notification_outbox_failure_incident")
    def test_fail_telegram_outbox_delivery_marks_failed(self, record_mock):
        outbox = self._create_delivering_outbox()

        failed = fail_telegram_outbox_delivery(outbox_id=outbox.id, error="telegram delivery failed")

        outbox.refresh_from_db()
        self.assertTrue(failed)
        self.assertEqual(outbox.status, NotificationOutbox.Status.FAILED)
        self.assertEqual(outbox.last_error, "telegram delivery failed")
        record_mock.assert_called_once()

    @override_settings(TELEGRAM_OUTBOX_DELIVERING_TIMEOUT_MINUTES=30)
    @patch("apps.notifications.telegram_delivery.record_notification_outbox_failure_incident")
    def test_reap_stuck_telegram_outbox_deliveries_marks_timeout_failed(self, record_mock):
        outbox = self._create_delivering_outbox()
        NotificationOutbox.objects.filter(pk=outbox.pk).update(
            last_attempt_at=timezone.now() - timedelta(minutes=31),
        )

        reaped = reap_stuck_telegram_outbox_deliveries()

        outbox.refresh_from_db()
        self.assertEqual(reaped, 1)
        self.assertEqual(outbox.status, NotificationOutbox.Status.FAILED)
        self.assertEqual(outbox.last_error, "telegram delivery ack timeout")

    @patch("apps.notifications.telegram_delivery._ack_feedback_entry")
    @patch("apps.notifications.telegram_delivery._read_feedback_stream")
    @patch("apps.notifications.telegram_delivery.ensure_telegram_feedback_consumer_groups")
    def test_process_telegram_outbound_feedback_handles_ack_and_failed(
        self,
        ensure_groups_mock,
        read_stream_mock,
        ack_entry_mock,
    ):
        delivering_outbox = NotificationOutbox.objects.create(
            notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
            recipient=TelegramDestination.NOTIFICATIONS.value,
            payload_encrypted=b"{}",
            status=NotificationOutbox.Status.DELIVERING,
            attempts=1,
            last_attempt_at=timezone.now(),
        )
        failed_outbox = NotificationOutbox.objects.create(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
            recipient=TelegramDestination.NOTIFICATIONS.value,
            payload_encrypted=b"{}",
            status=NotificationOutbox.Status.DELIVERING,
            attempts=1,
            last_attempt_at=timezone.now(),
        )
        read_stream_mock.side_effect = [
            [("1-0", {"source": "outbox", "correlation_id": str(delivering_outbox.id)})],
            [("2-0", {"source": "outbox", "correlation_id": str(failed_outbox.id), "error": "boom"})],
        ]

        result = process_telegram_outbound_feedback()

        delivering_outbox.refresh_from_db()
        failed_outbox.refresh_from_db()
        self.assertEqual(result, {"ack_processed": 1, "failed_processed": 1})
        self.assertEqual(delivering_outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(failed_outbox.status, NotificationOutbox.Status.FAILED)
        ensure_groups_mock.assert_called_once()
        self.assertEqual(read_stream_mock.call_count, 2)
        self.assertEqual(ack_entry_mock.call_count, 2)


class TelegramOutboxProcessingTests(TestCase):
    @override_settings(
        TELEGRAM_REDIS_URL="redis://localhost:6379/1",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    @patch("apps.notifications.handlers.send_telegram_message")
    def test_process_notification_outbox_skips_when_already_delivering(self, send_mock):
        outbox = NotificationOutbox.objects.create(
            notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
            recipient=TelegramDestination.NOTIFICATIONS.value,
            payload_encrypted=b"{}",
            status=NotificationOutbox.Status.DELIVERING,
            attempts=1,
            last_attempt_at=timezone.now(),
        )

        processed = process_notification_outbox(outbox_id=outbox.id)

        self.assertFalse(processed)
        send_mock.assert_not_called()
