from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.custom_games.models import CustomGameRequest
from apps.notifications.destinations import TelegramDestination
from apps.notifications.formatters import format_custom_game_request_paid_admin_telegram
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import (
    create_notification_outbox,
    enqueue_notification_outbox,
    process_notification_outbox,
)
from apps.notifications.telegram import (
    TelegramConfigurationError,
    get_telegram_destination_skip_reason,
    get_telegram_route,
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
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    def test_returns_reason_when_bot_token_is_missing(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.NOTIFICATIONS)

        self.assertEqual(reason, "telegram_bot_token_not_configured")

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    def test_returns_reason_when_forum_chat_is_missing(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.NOTIFICATIONS)

        self.assertEqual(reason, "telegram_forum_chat_not_configured")

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=0,
    )
    def test_returns_reason_when_notifications_thread_is_missing(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.NOTIFICATIONS)

        self.assertEqual(reason, "telegram_notifications_route_not_configured")

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_SUPPORT_THREAD_ID=0,
    )
    def test_returns_reason_when_support_thread_is_missing(self):
        reason = get_telegram_destination_skip_reason(TelegramDestination.SUPPORT)

        self.assertEqual(reason, "telegram_support_route_not_configured")

    @override_settings(
        TELEGRAM_BOT_TOKEN="telegram-token",
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
        TELEGRAM_BOT_TOKEN="telegram-token",
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
        self.assertIn("Оплачена заявка на игру", send_telegram_message_mock.call_args.kwargs["text"])
        self.assertIn("80.00 BYN", send_telegram_message_mock.call_args.kwargs["text"])
