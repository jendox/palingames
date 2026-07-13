import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import OrderSource
from apps.custom_games.forms import AUDIENCE_OTHER, AUDIENCE_PRESET_67, CustomGameRequestForm
from apps.custom_games.models import CustomGameFile, CustomGameRequest
from apps.custom_games.services import (
    create_custom_game_download_token,
    deliver_custom_game_request,
    notify_custom_game_request_created,
    send_custom_game_download_link,
)
from apps.notifications.destinations import TelegramDestination
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import process_notification_outbox
from apps.notifications.types import NotificationType
from apps.payments.models import Invoice
from apps.products.models import Currency
from apps.products.services.s3 import ProductFileDownloadUrlError, upload_custom_game_file

CUSTOM_GAME_MODEL_DATA = {
    "subject": "Космос",
    "audience": "Дети 6-8 лет, группа до 10 человек",
    "page_count": "8",
    "idea": "Нужна игра про космос для детей с заданиями на внимание.",
    "contact_name": "Анна",
    "contact_email": "anna@example.com",
}

CUSTOM_GAME_POST_DATA = {
    "subject": "Космос",
    "audience_preset": AUDIENCE_PRESET_67,
    "audience_other": "",
    "page_count": "8",
    "idea": "Нужна игра про космос для детей с заданиями на внимание.",
    "contact_name": "Анна",
    "contact_email": "anna@example.com",
}


class CustomGameRequestFormTests(TestCase):
    def test_form_accepts_empty_idea(self):
        data = {**CUSTOM_GAME_POST_DATA, "idea": ""}
        form = CustomGameRequestForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["idea"], "")

    def test_audience_other_required_when_preset_is_other(self):
        data = {**CUSTOM_GAME_POST_DATA, "audience_preset": AUDIENCE_OTHER, "audience_other": ""}
        form = CustomGameRequestForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("audience_other", form.errors)

    def test_audience_other_stored_when_preset_is_other(self):
        data = {**CUSTOM_GAME_POST_DATA, "audience_preset": AUDIENCE_OTHER, "audience_other": "5 лет, особый случай"}
        form = CustomGameRequestForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["audience"], "5 лет, особый случай")


class CustomGameRequestModelTests(TestCase):
    def test_request_generates_payment_account_no(self):
        custom_game_request = CustomGameRequest.objects.create(**CUSTOM_GAME_MODEL_DATA)

        self.assertIsNotNone(custom_game_request.payment_account_no)
        self.assertEqual(len(custom_game_request.payment_account_no), 16)
        self.assertTrue(custom_game_request.payment_account_no.startswith(OrderSource.PALINGAMES))
        self.assertEqual(
            custom_game_request.payment_account_no[2:8],
            timezone.localtime(custom_game_request.created_at).strftime("%d%m%y"),
        )

    def test_mark_in_progress_requires_paid_invoice(self):
        custom_game_request = CustomGameRequest.objects.create(**CUSTOM_GAME_MODEL_DATA)
        custom_game_request.quoted_price = Decimal("100.00")
        custom_game_request.currency = Currency.BYN
        custom_game_request.deadline = timezone.localdate() + timedelta(days=7)

        with self.assertRaises(ValidationError):
            custom_game_request.mark_in_progress()

        Invoice.objects.create(
            custom_game_request=custom_game_request,
            provider_invoice_no="12345678",
            status=Invoice.InvoiceStatus.PAID,
            amount=Decimal("100.00"),
            currency=Currency.BYN,
            paid_at=timezone.now(),
        )
        custom_game_request.mark_in_progress()

        custom_game_request.refresh_from_db()
        self.assertEqual(custom_game_request.status, CustomGameRequest.Status.IN_PROGRESS)

    def test_in_progress_status_requires_paid_invoice_on_model_validation(self):
        custom_game_request = CustomGameRequest.objects.create(**CUSTOM_GAME_MODEL_DATA)
        custom_game_request.quoted_price = Decimal("100.00")
        custom_game_request.deadline = timezone.localdate() + timedelta(days=7)
        custom_game_request.status = CustomGameRequest.Status.IN_PROGRESS

        with self.assertRaises(ValidationError) as exc:
            custom_game_request.full_clean()

        self.assertIn("status", exc.exception.message_dict)

        Invoice.objects.create(
            custom_game_request=custom_game_request,
            provider_invoice_no="12345678",
            status=Invoice.InvoiceStatus.PAID,
            amount=Decimal("100.00"),
            currency=Currency.BYN,
            paid_at=timezone.now(),
        )
        custom_game_request.full_clean()

    def test_mark_ready_requires_in_progress_status(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
            deadline=timezone.localdate() + timedelta(days=7),
            status=CustomGameRequest.Status.NEW,
        )
        CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/request.zip",
            original_filename="request.zip",
            size_bytes=100,
        )

        with self.assertRaises(ValidationError):
            custom_game_request.mark_ready()

    def test_mark_ready_requires_active_file(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
            deadline=timezone.localdate() + timedelta(days=7),
            status=CustomGameRequest.Status.IN_PROGRESS,
        )

        with self.assertRaises(ValidationError):
            custom_game_request.mark_ready()

        CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/request.zip",
            original_filename="request.zip",
            size_bytes=100,
        )
        custom_game_request.mark_ready()

        custom_game_request.refresh_from_db()
        self.assertEqual(custom_game_request.status, CustomGameRequest.Status.READY)

    def test_ready_status_requires_active_file_on_model_validation(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
            deadline=timezone.localdate() + timedelta(days=7),
            status=CustomGameRequest.Status.IN_PROGRESS,
        )
        custom_game_request.status = CustomGameRequest.Status.READY

        with self.assertRaises(ValidationError):
            custom_game_request.full_clean()

        CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/request.zip",
            original_filename="request.zip",
            size_bytes=100,
        )
        custom_game_request.full_clean()


class CustomGameDeliveryTests(TestCase):
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="https://example.com",
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_deliver_custom_game_request_sends_link_and_marks_delivered(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
            deadline=timezone.localdate() + timedelta(days=7),
            status=CustomGameRequest.Status.READY,
        )
        CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/request.zip",
            original_filename="request.zip",
            size_bytes=100,
        )

        deliver_custom_game_request(custom_game_request=custom_game_request)
        custom_game_request.refresh_from_db()

        self.assertEqual(custom_game_request.status, CustomGameRequest.Status.DELIVERED)
        self.assertIsNotNone(custom_game_request.delivered_at)
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.CUSTOM_GAME_DOWNLOAD,
                object_id=custom_game_request.id,
            ).exists(),
        )


@override_settings(CUSTOM_GAME_ADMIN_EMAILS=["ops@example.com"])
class CustomGameRequestAdminActionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            email="staff@example.com",
            password="staff-pass-123",
            is_staff=True,
            is_superuser=True,
        )

    def test_create_invoice_button_available_for_new_request_with_price_and_deadline(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
            deadline=timezone.localdate() + timedelta(days=7),
        )

        self.client.force_login(self.staff)
        with patch("apps.custom_games.admin.enqueue_invoice_creation") as enqueue:
            response = self.client.get(
                reverse("admin:custom_games_customgamerequest_create_invoice", args=[custom_game_request.pk]),
            )

        enqueue.assert_called_once_with(custom_game_request.id, payment_target="custom_game_request")
        self.assertEqual(response.status_code, 302)

    def test_create_invoice_blocked_without_deadline(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
        )

        self.client.force_login(self.staff)
        with patch("apps.custom_games.admin.enqueue_invoice_creation") as enqueue:
            response = self.client.get(
                reverse("admin:custom_games_customgamerequest_create_invoice", args=[custom_game_request.pk]),
                follow=True,
            )

        enqueue.assert_not_called()
        self.assertContains(response, "Инвойс можно создать только после указания цены и дедлайна")

    def test_create_invoice_blocked_when_pending_invoice_exists(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
            deadline=timezone.localdate() + timedelta(days=7),
        )
        Invoice.objects.create(
            custom_game_request=custom_game_request,
            provider_invoice_no="12345678",
            status=Invoice.InvoiceStatus.PENDING,
            amount=Decimal("100.00"),
            currency=Currency.BYN,
        )

        self.client.force_login(self.staff)
        with patch("apps.custom_games.admin.enqueue_invoice_creation") as enqueue:
            response = self.client.get(
                reverse("admin:custom_games_customgamerequest_create_invoice", args=[custom_game_request.pk]),
                follow=True,
            )

        enqueue.assert_not_called()
        self.assertContains(response, "Инвойс можно создать только после указания цены и дедлайна")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="https://example.com",
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_deliver_view_marks_request_delivered(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
            deadline=timezone.localdate() + timedelta(days=7),
            status=CustomGameRequest.Status.READY,
        )
        CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/request.zip",
            original_filename="request.zip",
            size_bytes=100,
        )

        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("admin:custom_games_customgamerequest_deliver", args=[custom_game_request.pk]),
        )

        custom_game_request.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(custom_game_request.status, CustomGameRequest.Status.DELIVERED)
        self.assertIsNotNone(custom_game_request.delivered_at)


class CustomGameS3ServiceTests(TestCase):
    @patch("apps.products.services.s3.get_s3_client")
    def test_upload_custom_game_file_returns_expected_metadata(self, mock_get_s3_client):
        mock_get_s3_client.return_value = Mock()
        uploaded_file = SimpleUploadedFile(
            "custom-game.zip",
            b"archive-content",
            content_type="application/zip",
        )

        result = upload_custom_game_file(payment_account_no="PG190426ABC12345", uploaded_file=uploaded_file)

        self.assertTrue(result["file_key"].startswith("custom-games/PG190426ABC12345/"))
        self.assertTrue(result["file_key"].endswith(".zip"))
        self.assertEqual(result["original_filename"], "custom-game.zip")
        self.assertEqual(result["mime_type"], "application/zip")
        self.assertEqual(result["size_bytes"], len(b"archive-content"))
        self.assertEqual(len(result["checksum_sha256"]), 64)
        mock_get_s3_client.return_value.upload_fileobj.assert_called_once()


class CustomGameDownloadTests(TestCase):
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="https://example.com",
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_send_custom_game_download_link_creates_token_and_email(self):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            status=CustomGameRequest.Status.DELIVERED,
        )
        CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/request.zip",
            original_filename="request.zip",
            size_bytes=100,
        )

        download_token = send_custom_game_download_link(custom_game_request=custom_game_request)
        outbox = NotificationOutbox.objects.get(notification_type=NotificationType.CUSTOM_GAME_DOWNLOAD)

        self.assertEqual(outbox.status, NotificationOutbox.Status.PENDING)
        self.assertEqual(outbox.object_id, custom_game_request.id)
        self.assertTrue(process_notification_outbox(outbox_id=outbox.id))

        download_token.refresh_from_db()
        self.assertIsNotNone(download_token.sent_at)
        self.assertEqual(download_token.email, custom_game_request.contact_email)
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(custom_game_request.payment_account_no, mail.outbox[0].subject)
        self.assertIn("https://example.com/custom-game/downloads/", mail.outbox[0].body)

    @patch("apps.custom_games.views.resolve_download_delivery_failure_incident")
    @patch("apps.custom_games.views.generate_presigned_download_url", return_value="https://storage.example/file.zip")
    def test_download_view_redirects_and_marks_token_used(
        self,
        mock_generate_url,
        resolve_download_delivery_failure_incident_mock,
    ):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            status=CustomGameRequest.Status.DELIVERED,
        )
        CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/request.zip",
            original_filename="request.zip",
            size_bytes=100,
        )
        download_token, raw_token = create_custom_game_download_token(custom_game_request)

        response = self.client.get(reverse("custom-game-download", kwargs={"token": raw_token}))

        self.assertRedirects(response, "https://storage.example/file.zip", fetch_redirect_response=False)
        download_token.refresh_from_db()
        self.assertEqual(download_token.downloads_count, 1)
        mock_generate_url.assert_called_once_with(
            file_key="custom-games/request.zip",
            original_filename="request.zip",
        )
        resolve_download_delivery_failure_incident_mock.assert_called_once_with(
            delivery_type="custom_game",
            reason="download_unavailable",
        )

    @patch("apps.custom_games.views.record_download_delivery_failure_incident")
    @patch("apps.custom_games.views.generate_presigned_download_url", side_effect=ProductFileDownloadUrlError("boom"))
    def test_download_view_records_incident_when_presigned_url_generation_fails(
        self,
        mock_generate_url,
        record_download_delivery_failure_incident_mock,
    ):
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            status=CustomGameRequest.Status.DELIVERED,
        )
        CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/request.zip",
            original_filename="request.zip",
            size_bytes=100,
        )
        download_token, raw_token = create_custom_game_download_token(custom_game_request)

        response = self.client.get(reverse("custom-game-download", kwargs={"token": raw_token}))

        self.assertEqual(response.status_code, 503)
        download_token.refresh_from_db()
        self.assertEqual(download_token.downloads_count, 0)
        record_download_delivery_failure_incident_mock.assert_called_once()


class CustomGamePageTests(TestCase):
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CUSTOM_GAME_ADMIN_EMAILS=["admin@example.com"],
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_guest_can_submit_custom_game_request(self, delay_mock):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        custom_game_request = CustomGameRequest.objects.get()
        self.assertIsNone(custom_game_request.user)
        self.assertEqual(custom_game_request.contact_email, "anna@example.com")
        customer_outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_CUSTOMER,
            channel=NotificationOutbox.Channel.EMAIL,
        )
        admin_email_outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
            channel=NotificationOutbox.Channel.EMAIL,
        )
        admin_telegram_outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
        )
        self.assertEqual(customer_outbox.recipient, custom_game_request.contact_email)
        self.assertEqual(customer_outbox.status, NotificationOutbox.Status.PENDING)
        self.assertEqual(admin_email_outbox.recipient, "admin@example.com")
        self.assertEqual(admin_email_outbox.status, NotificationOutbox.Status.PENDING)
        self.assertEqual(admin_telegram_outbox.recipient, "notifications")
        self.assertEqual(admin_telegram_outbox.status, NotificationOutbox.Status.PENDING)
        self.assertCountEqual(
            [call.args[0] for call in delay_mock.call_args_list],
            [customer_outbox.id, admin_email_outbox.id, admin_telegram_outbox.id],
        )
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'data-checkout-order-created="true"')
        self.assertContains(response, custom_game_request.payment_account_no)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CUSTOM_GAME_ADMIN_EMAILS=["admin@example.com"],
        SITE_BASE_URL="https://example.com",
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
    )
    def test_process_custom_game_request_customer_notification_sends_email(self):
        response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        custom_game_request = CustomGameRequest.objects.get()
        outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_CUSTOMER,
            channel=NotificationOutbox.Channel.EMAIL,
        )

        self.assertTrue(process_notification_outbox(outbox_id=outbox.id))

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [custom_game_request.contact_email])
        self.assertIn(custom_game_request.payment_account_no, mail.outbox[0].subject)
        self.assertIn("Спасибо за заявку", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CUSTOM_GAME_ADMIN_EMAILS=["admin@example.com"],
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
    )
    def test_process_custom_game_request_admin_notification_sends_email(self):
        response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        custom_game_request = CustomGameRequest.objects.get()
        outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
            channel=NotificationOutbox.Channel.EMAIL,
        )

        self.assertTrue(process_notification_outbox(outbox_id=outbox.id))

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["admin@example.com"])
        self.assertIn(custom_game_request.payment_account_no, mail.outbox[0].subject)
        self.assertIn("Новая заявка на игру", mail.outbox[0].subject)

    @override_settings(
        CUSTOM_GAME_ADMIN_EMAILS=["admin@example.com"],
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    @patch("apps.notifications.handlers.send_telegram_message")
    def test_process_custom_game_request_admin_telegram_notification_sends_message(self, send_telegram_message_mock):
        response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
        )

        self.assertTrue(process_notification_outbox(outbox_id=outbox.id))

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        send_telegram_message_mock.assert_called_once()
        self.assertEqual(
            send_telegram_message_mock.call_args.kwargs["destination"],
            TelegramDestination.NOTIFICATIONS,
        )
        self.assertIn("Новая заявка на игру", send_telegram_message_mock.call_args.kwargs["text"])

    @patch("apps.custom_games.services.observe_custom_game_request_creation_duration")
    def test_guest_submit_observes_creation_duration(self, observe_custom_game_request_creation_duration_mock):
        response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        observe_custom_game_request_creation_duration_mock.assert_called_once()
        self.assertEqual(
            observe_custom_game_request_creation_duration_mock.call_args.kwargs["user_type"],
            "guest",
        )
        self.assertEqual(
            observe_custom_game_request_creation_duration_mock.call_args.kwargs["result"],
            "success",
        )
        self.assertGreaterEqual(
            observe_custom_game_request_creation_duration_mock.call_args.kwargs["duration_seconds"],
            0,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CUSTOM_GAME_ADMIN_EMAILS=[],
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_FORUM_CHAT_ID="",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=0,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_authenticated_submit_links_request_to_user(self, delay_mock):
        user = get_user_model().objects.create_user(email="user@example.com", password="test-pass-123")
        self.client.force_login(user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        custom_game_request = CustomGameRequest.objects.get()
        self.assertEqual(custom_game_request.user, user)
        customer_outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_CUSTOMER,
        )
        self.assertEqual(customer_outbox.recipient, custom_game_request.contact_email)
        self.assertFalse(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
            ).exists(),
        )
        delay_mock.assert_called_once_with(customer_outbox.id)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'data-checkout-order-created="true"')
        self.assertContains(response, custom_game_request.payment_account_no)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CUSTOM_GAME_ADMIN_EMAILS=["admin@example.com"],
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_FORUM_CHAT_ID="",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=0,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_submit_without_telegram_settings_skips_admin_telegram_outbox(self, delay_mock):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.CUSTOM_GAME_REQUEST_CUSTOMER,
                channel=NotificationOutbox.Channel.EMAIL,
            ).exists(),
        )
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
                channel=NotificationOutbox.Channel.EMAIL,
            ).exists(),
        )
        self.assertFalse(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
                channel=NotificationOutbox.Channel.TELEGRAM,
            ).exists(),
        )
        self.assertEqual(delay_mock.call_count, 2)

    @override_settings(
        CUSTOM_GAME_ADMIN_EMAILS=["admin@example.com"],
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_FORUM_CHAT_ID="",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=0,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_notify_custom_game_request_created_without_contact_email_skips_customer_notification_outbox(
        self,
        delay_mock,
    ):
        custom_game_request = CustomGameRequest.objects.create(**{**CUSTOM_GAME_MODEL_DATA, "contact_email": ""})

        with self.captureOnCommitCallbacks(execute=True):
            notify_custom_game_request_created(custom_game_request)

        self.assertFalse(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.CUSTOM_GAME_REQUEST_CUSTOMER,
            ).exists(),
        )
        admin_outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.CUSTOM_GAME_REQUEST_ADMIN,
            channel=NotificationOutbox.Channel.EMAIL,
        )
        delay_mock.assert_called_once_with(admin_outbox.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_post_does_not_create_request(self):
        response = self.client.post(reverse("custom-game"), data={**CUSTOM_GAME_POST_DATA, "contact_email": "bad"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomGameRequest.objects.exists())

    def test_get_prefills_contact_email_for_authenticated_user(self):
        user = get_user_model().objects.create_user(email="user@example.com", password="test-pass-123")
        self.client.force_login(user)
        response = self.client.get(reverse("custom-game"))
        self.assertEqual(response.context["form"].initial.get("contact_email"), "user@example.com")


ADMIN_DIRECT_S3_SETTINGS = {
    "ADMIN_DIRECT_S3_UPLOAD_ENABLED": True,
    "ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES": 1024 * 1024,
    "ADMIN_DIRECT_S3_UPLOAD_PRESIGN_TTL_SECONDS": 900,
    "ADMIN_DIRECT_S3_UPLOAD_ALLOWED_EXTENSIONS": ".zip,.pdf",
    "S3_BUCKET_NAME": "products",
    "S3_ENDPOINT_URL": "http://127.0.0.1:9000",
    "S3_PRODUCT_IMAGES_PREFIX": "previews",
}


@override_settings(**ADMIN_DIRECT_S3_SETTINGS)
class AdminCustomGameDirectUploadTests(TestCase):
    def setUp(self):
        caches["default"].clear()
        self.custom_game_request = CustomGameRequest.objects.create(**CUSTOM_GAME_MODEL_DATA)
        self.staff_user = get_user_model().objects.create_user(
            email="staff@example.com",
            password="pass-123",
            is_staff=True,
            is_superuser=True,
        )
        self.other_staff = get_user_model().objects.create_user(
            email="other-staff@example.com",
            password="pass-123",
            is_staff=True,
            is_superuser=True,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.staff_user)

    def _csrf_token(self) -> str:
        response = self.client.get(reverse("admin:custom_games_customgamefile_add"))
        self.assertEqual(response.status_code, 200)
        return self.client.cookies["csrftoken"].value

    def _presign_payload(self, **overrides):
        payload = {
            "request_id": self.custom_game_request.id,
            "filename": "game.zip",
            "content_type": "application/zip",
            "size_bytes": 128,
        }
        payload.update(overrides)
        return payload

    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_presign_returns_upload_metadata(self, mock_generate_presigned_upload_url):
        mock_generate_presigned_upload_url.return_value = {
            "upload_url": "https://storage.example/upload",
            "required_headers": {"Content-Type": "application/zip"},
            "expires_in": 900,
        }
        token = self._csrf_token()

        response = self.client.post(
            reverse("admin-custom-game-file-presign"),
            data=json.dumps(self._presign_payload()),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertIn("intent_id", payload)
        self.assertTrue(payload["file_key"].startswith("custom-games/"))
        self.assertIn(self.custom_game_request.payment_account_no, payload["file_key"])

    def test_presign_requires_staff(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("home"))
        token = client.cookies["csrftoken"].value

        response = client.post(
            reverse("admin-custom-game-file-presign"),
            data=json.dumps(self._presign_payload()),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content.decode()), {"error": "staff_required"})

    @override_settings(ADMIN_DIRECT_S3_UPLOAD_ENABLED=False)
    def test_presign_disabled_when_feature_flag_off(self):
        token = self._csrf_token()

        response = self.client.post(
            reverse("admin-custom-game-file-presign"),
            data=json.dumps(self._presign_payload()),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.content.decode()), {"error": "feature_disabled"})

    @patch("apps.products.admin_upload_views.verify_uploaded_object")
    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_happy_path_creates_custom_game_file(
        self,
        mock_generate_presigned_upload_url,
        mock_verify_uploaded_object,
    ):
        mock_generate_presigned_upload_url.return_value = {
            "upload_url": "https://storage.example/upload",
            "required_headers": {"Content-Type": "application/zip"},
            "expires_in": 900,
        }
        mock_verify_uploaded_object.return_value = {
            "ContentLength": 128,
            "ContentType": "application/zip",
        }
        token = self._csrf_token()
        presign_payload = json.loads(
            self.client.post(
                reverse("admin-custom-game-file-presign"),
                data=json.dumps(self._presign_payload()),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            ).content.decode(),
        )

        finalize_response = self.client.post(
            reverse("admin-custom-game-file-finalize"),
            data=json.dumps(
                {
                    "intent_id": presign_payload["intent_id"],
                    "request_id": self.custom_game_request.id,
                    "file_key": presign_payload["file_key"],
                    "original_filename": "game.zip",
                    "mime_type": "application/zip",
                    "size_bytes": 128,
                    "is_active": True,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(finalize_response.status_code, 200)
        finalize_payload = json.loads(finalize_response.content.decode())
        self.assertTrue(finalize_payload["ok"])

        custom_game_file = CustomGameFile.objects.get()
        self.assertEqual(custom_game_file.request_id, self.custom_game_request.id)
        self.assertEqual(custom_game_file.file_key, presign_payload["file_key"])
        self.assertEqual(custom_game_file.uploaded_by_id, self.staff_user.id)
        self.assertEqual(
            finalize_payload["redirect_url"],
            reverse("admin:custom_games_customgamefile_change", args=[custom_game_file.pk]),
        )

    @patch("apps.products.admin_upload_views.verify_uploaded_object")
    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_rejects_foreign_intent_user(
        self,
        mock_generate_presigned_upload_url,
        mock_verify_uploaded_object,
    ):
        mock_generate_presigned_upload_url.return_value = {
            "upload_url": "https://storage.example/upload",
            "required_headers": {"Content-Type": "application/zip"},
            "expires_in": 900,
        }
        mock_verify_uploaded_object.return_value = {
            "ContentLength": 128,
            "ContentType": "application/zip",
        }
        token = self._csrf_token()
        presign_payload = json.loads(
            self.client.post(
                reverse("admin-custom-game-file-presign"),
                data=json.dumps(self._presign_payload()),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            ).content.decode(),
        )

        other_client = Client(enforce_csrf_checks=True)
        other_client.force_login(self.other_staff)
        other_client.get(reverse("admin:custom_games_customgamefile_add"))
        other_token = other_client.cookies["csrftoken"].value

        response = other_client.post(
            reverse("admin-custom-game-file-finalize"),
            data=json.dumps(
                {
                    "intent_id": presign_payload["intent_id"],
                    "request_id": self.custom_game_request.id,
                    "file_key": presign_payload["file_key"],
                    "original_filename": "game.zip",
                    "mime_type": "application/zip",
                    "size_bytes": 128,
                    "is_active": True,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=other_token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(CustomGameFile.objects.count(), 0)

        staff_finalize = self.client.post(
            reverse("admin-custom-game-file-finalize"),
            data=json.dumps(
                {
                    "intent_id": presign_payload["intent_id"],
                    "request_id": self.custom_game_request.id,
                    "file_key": presign_payload["file_key"],
                    "original_filename": "game.zip",
                    "mime_type": "application/zip",
                    "size_bytes": 128,
                    "is_active": True,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(staff_finalize.status_code, 200)

    def test_custom_game_file_admin_includes_direct_upload_assets_when_enabled(self):
        response = self.client.get(reverse("admin:custom_games_customgamefile_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin-custom-game-file-upload.js")
        self.assertContains(response, "custom-game-file-direct-upload-config")

    @override_settings(ADMIN_DIRECT_S3_UPLOAD_ENABLED=False)
    def test_custom_game_file_admin_hides_direct_upload_assets_when_disabled(self):
        response = self.client.get(reverse("admin:custom_games_customgamefile_add"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "admin-custom-game-file-upload.js")
        self.assertNotContains(response, "custom-game-file-direct-upload-config")

    @patch("apps.custom_games.forms.validate_storage_bucket_access")
    @patch("apps.custom_games.admin.upload_custom_game_file")
    def test_save_model_uses_server_side_upload_when_feature_flag_disabled(
        self,
        mock_upload_custom_game_file,
        _mock_validate_storage_bucket_access,
    ):
        mock_upload_custom_game_file.return_value = {
            "file_key": "custom-games/server.zip",
            "original_filename": "game.zip",
            "mime_type": "application/zip",
            "size_bytes": 12,
            "checksum_sha256": "a" * 64,
        }

        with override_settings(ADMIN_DIRECT_S3_UPLOAD_ENABLED=False):
            add_response = self.client.get(reverse("admin:custom_games_customgamefile_add"))
            csrf_token = add_response.cookies["csrftoken"].value
            response = self.client.post(
                reverse("admin:custom_games_customgamefile_add"),
                {
                    "csrfmiddlewaretoken": csrf_token,
                    "request": str(self.custom_game_request.id),
                    "is_active": "on",
                    "upload": SimpleUploadedFile("game.zip", b"zip-content", content_type="application/zip"),
                },
            )

        self.assertEqual(response.status_code, 302)
        mock_upload_custom_game_file.assert_called_once()
        custom_game_file = CustomGameFile.objects.get()
        self.assertEqual(custom_game_file.file_key, "custom-games/server.zip")


class CustomGameFileSignalTests(TestCase):
    @patch("apps.products.services.s3.get_s3_client")
    def test_row_delete_removes_object_from_storage(self, mock_get_s3_client):
        mock_get_s3_client.return_value = Mock()
        custom_game_request = CustomGameRequest.objects.create(
            **CUSTOM_GAME_MODEL_DATA,
            quoted_price=Decimal("100.00"),
            deadline=timezone.localdate() + timedelta(days=7),
            status=CustomGameRequest.Status.IN_PROGRESS,
        )
        custom_game_file = CustomGameFile.objects.create(
            request=custom_game_request,
            file_key="custom-games/12345/game.zip",
            original_filename="game.zip",
            size_bytes=256,
            is_active=True,
        )

        custom_game_file.delete()

        mock_get_s3_client.return_value.delete_object.assert_called_once_with(
            Bucket=settings.S3_BUCKET_NAME,
            Key="custom-games/12345/game.zip",
        )
        self.assertFalse(CustomGameFile.objects.filter(pk=custom_game_file.pk).exists())
