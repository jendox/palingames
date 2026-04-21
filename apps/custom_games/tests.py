from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import OrderSource
from apps.custom_games.forms import AUDIENCE_OTHER, AUDIENCE_PRESET_67, CustomGameRequestForm
from apps.custom_games.models import CustomGameFile, CustomGameRequest
from apps.custom_games.services import create_custom_game_download_token, send_custom_game_download_link
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import process_notification_outbox
from apps.notifications.types import CUSTOM_GAME_DOWNLOAD
from apps.products.models import Currency
from apps.products.services.s3 import upload_custom_game_file

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

    def test_mark_in_progress_requires_price_and_deadline(self):
        custom_game_request = CustomGameRequest.objects.create(**CUSTOM_GAME_MODEL_DATA)

        with self.assertRaises(ValidationError):
            custom_game_request.mark_in_progress()

        custom_game_request.quoted_price = Decimal("100.00")
        custom_game_request.currency = Currency.BYN
        custom_game_request.deadline = timezone.localdate() + timedelta(days=7)
        custom_game_request.mark_in_progress()

        custom_game_request.refresh_from_db()
        self.assertEqual(custom_game_request.status, CustomGameRequest.Status.IN_PROGRESS)

    def test_in_progress_status_requires_price_and_deadline_on_model_validation(self):
        custom_game_request = CustomGameRequest.objects.create(**CUSTOM_GAME_MODEL_DATA)
        custom_game_request.status = CustomGameRequest.Status.IN_PROGRESS

        with self.assertRaises(ValidationError) as exc:
            custom_game_request.full_clean()

        self.assertIn("quoted_price", exc.exception.message_dict)
        self.assertIn("deadline", exc.exception.message_dict)

        custom_game_request.quoted_price = Decimal("100.00")
        custom_game_request.deadline = timezone.localdate() + timedelta(days=7)
        custom_game_request.full_clean()

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
        outbox = NotificationOutbox.objects.get(notification_type=CUSTOM_GAME_DOWNLOAD)

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

    @patch("apps.custom_games.views.generate_presigned_download_url", return_value="https://storage.example/file.zip")
    def test_download_view_redirects_and_marks_token_used(self, mock_generate_url):
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


class CustomGamePageTests(TestCase):
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CUSTOM_GAME_ADMIN_EMAILS=["admin@example.com"],
    )
    def test_guest_can_submit_custom_game_request(self):
        response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        custom_game_request = CustomGameRequest.objects.get()
        self.assertIsNone(custom_game_request.user)
        self.assertEqual(custom_game_request.contact_email, "anna@example.com")
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(custom_game_request.payment_account_no, mail.outbox[0].subject)
        self.assertIn(custom_game_request.payment_account_no, mail.outbox[1].subject)
        self.assertContains(response, 'data-checkout-order-created="true"')
        self.assertContains(response, custom_game_request.payment_account_no)

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
    )
    def test_authenticated_submit_links_request_to_user(self):
        user = get_user_model().objects.create_user(email="user@example.com", password="test-pass-123")
        self.client.force_login(user)

        response = self.client.post(reverse("custom-game"), data=CUSTOM_GAME_POST_DATA, follow=True)

        self.assertEqual(response.status_code, 200)
        custom_game_request = CustomGameRequest.objects.get()
        self.assertEqual(custom_game_request.user, user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(response, 'data-checkout-order-created="true"')
        self.assertContains(response, custom_game_request.payment_account_no)

    def test_invalid_post_does_not_create_request(self):
        response = self.client.post(reverse("custom-game"), data={**CUSTOM_GAME_POST_DATA, "contact_email": "bad"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomGameRequest.objects.exists())

    def test_get_prefills_contact_email_for_authenticated_user(self):
        user = get_user_model().objects.create_user(email="user@example.com", password="test-pass-123")
        self.client.force_login(user)
        response = self.client.get(reverse("custom-game"))
        self.assertEqual(response.context["form"].initial.get("contact_email"), "user@example.com")
