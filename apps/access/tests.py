from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import NotificationOutbox
from apps.notifications.types import GUEST_ORDER_DOWNLOAD
from apps.orders.models import Order
from apps.products.models import Product, ProductFile
from apps.products.services.s3 import ProductFileDownloadUrlError

from .email_outbox import (
    cleanup_old_guest_access_email_outboxes,
    create_guest_access_email_outbox,
    decrypt_outbox_payload,
    process_guest_access_email_outbox,
)
from .emails import send_guest_order_download_email
from .models import GuestAccess
from .services import create_guest_access, mark_guest_access_used, release_guest_access_use, resolve_guest_access


class GuestAccessModelTests(TestCase):
    def test_guest_access_is_active_when_not_revoked_not_expired_and_has_remaining_downloads(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив", slug="archive-access", price="10.00")
        guest_access = GuestAccess.objects.create(
            order=order,
            product=product,
            token_hash="a" * 64,
            email=order.email,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.assertTrue(guest_access.is_active)

    def test_guest_access_is_not_active_when_download_limit_exhausted(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 2", slug="archive-access-2", price="10.00")
        guest_access = GuestAccess.objects.create(
            order=order,
            product=product,
            token_hash="b" * 64,
            email=order.email,
            expires_at=timezone.now() + timedelta(hours=1),
            downloads_count=3,
            max_downloads=3,
        )

        self.assertFalse(guest_access.is_active)


class GuestAccessServiceTests(TestCase):
    def test_create_guest_access_returns_raw_token_and_stores_hash(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 3", slug="archive-access-3", price="10.00")

        guest_access, raw_token = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
        )

        self.assertTrue(raw_token)
        self.assertEqual(len(guest_access.token_hash), 64)
        self.assertNotEqual(guest_access.token_hash, raw_token)
        self.assertEqual(guest_access.downloads_count, 0)
        self.assertEqual(guest_access.max_downloads, 3)

    def test_resolve_guest_access_returns_active_grant(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 4", slug="archive-access-4", price="10.00")
        guest_access, raw_token = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
        )

        resolved = resolve_guest_access(raw_token)

        self.assertEqual(resolved, guest_access)

    def test_mark_guest_access_used_increments_counter_and_sets_last_used_at(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 5", slug="archive-access-5", price="10.00")
        guest_access, _ = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
        )

        updated = mark_guest_access_used(guest_access)
        guest_access.refresh_from_db()

        self.assertTrue(updated)
        self.assertEqual(guest_access.downloads_count, 1)
        self.assertIsNotNone(guest_access.last_used_at)

    def test_resolve_guest_access_returns_none_when_download_limit_exhausted(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 6", slug="archive-access-6", price="10.00")
        guest_access, raw_token = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
            max_downloads=1,
        )
        mark_guest_access_used(guest_access)

        resolved = resolve_guest_access(raw_token)

        self.assertIsNone(resolved)

    def test_mark_guest_access_used_returns_false_when_limit_is_exhausted(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 6b", slug="archive-access-6b", price="10.00")
        guest_access, _ = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
            max_downloads=1,
        )
        self.assertTrue(mark_guest_access_used(guest_access))

        updated = mark_guest_access_used(guest_access)

        self.assertFalse(updated)

    def test_release_guest_access_use_decrements_counter(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 6c", slug="archive-access-6c", price="10.00")
        guest_access, _ = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
        )
        self.assertTrue(mark_guest_access_used(guest_access))

        release_guest_access_use(guest_access)

        guest_access.refresh_from_db()
        self.assertEqual(guest_access.downloads_count, 0)


class GuestProductDownloadViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        cls.product = Product.objects.create(title="Архив 7", slug="archive-access-7", price="10.00")
        cls.product_file = ProductFile.objects.create(
            product=cls.product,
            file_key="archive-access-7/archive.zip",
            original_filename="archive.zip",
            mime_type="application/zip",
            is_active=True,
        )

    @override_settings(SITE_BASE_URL="http://127.0.0.1:8000")
    def test_guest_download_redirects_to_presigned_url_and_increments_counter(self):
        guest_access, raw_token = create_guest_access(
            order=self.order,
            product=self.product,
            expires_in=timedelta(hours=24),
            max_downloads=3,
        )

        with patch("apps.access.views.generate_presigned_download_url", return_value="https://example.com/download"):
            response = self.client.get(reverse("guest-product-download", kwargs={"token": raw_token}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/download")
        guest_access.refresh_from_db()
        self.assertEqual(guest_access.downloads_count, 1)
        self.assertIsNotNone(guest_access.last_used_at)

    def test_guest_download_returns_410_for_exhausted_token(self):
        guest_access, raw_token = create_guest_access(
            order=self.order,
            product=self.product,
            expires_in=timedelta(hours=24),
            max_downloads=1,
        )
        mark_guest_access_used(guest_access)

        response = self.client.get(reverse("guest-product-download", kwargs={"token": raw_token}))

        self.assertEqual(response.status_code, 410)

    def test_guest_download_returns_410_when_limit_is_exhausted_during_race(self):
        guest_access, raw_token = create_guest_access(
            order=self.order,
            product=self.product,
            expires_in=timedelta(hours=24),
            max_downloads=1,
        )

        with (
            patch("apps.access.views.generate_presigned_download_url", return_value="https://example.com/download"),
            patch("apps.access.views.mark_guest_access_used", return_value=False),
        ):
            response = self.client.get(reverse("guest-product-download", kwargs={"token": raw_token}))

        self.assertEqual(response.status_code, 410)
        guest_access.refresh_from_db()
        self.assertEqual(guest_access.downloads_count, 0)

    def test_guest_download_marks_used_before_generating_presigned_url(self):
        guest_access, raw_token = create_guest_access(
            order=self.order,
            product=self.product,
            expires_in=timedelta(hours=24),
            max_downloads=1,
        )

        def assert_marked_before_generating_url(*args, **kwargs):
            guest_access.refresh_from_db()
            self.assertEqual(guest_access.downloads_count, 1)
            return "https://example.com/download"

        with patch(
            "apps.access.views.generate_presigned_download_url",
            side_effect=assert_marked_before_generating_url,
        ):
            response = self.client.get(reverse("guest-product-download", kwargs={"token": raw_token}))

        self.assertEqual(response.status_code, 302)

    def test_guest_download_releases_use_when_presigned_url_generation_fails(self):
        guest_access, raw_token = create_guest_access(
            order=self.order,
            product=self.product,
            expires_in=timedelta(hours=24),
            max_downloads=1,
        )

        with patch(
            "apps.access.views.generate_presigned_download_url",
            side_effect=ProductFileDownloadUrlError("boom"),
        ):
            response = self.client.get(reverse("guest-product-download", kwargs={"token": raw_token}))

        self.assertEqual(response.status_code, 503)
        guest_access.refresh_from_db()
        self.assertEqual(guest_access.downloads_count, 0)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_BASE_URL="http://127.0.0.1:8000",
    GUEST_ACCESS_EXPIRE_HOURS=24,
    GUEST_ACCESS_MAX_DOWNLOADS=3,
    APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
)
class GuestAccessEmailTests(TestCase):
    def test_send_guest_order_download_email_sends_card_links(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
            payment_account_no="PG250326ABCDEFGH",
        )

        send_guest_order_download_email(
            order=order,
            guest_access_payloads=[
                {
                    "title": "Архив 8",
                    "category": "Дидактическая игра",
                    "price": "10.00",
                    "image_url": "/static/images/example-product-image-1.png",
                    "token": "token-123",
                },
            ],
        )

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("PG250326ABCDEFGH", email.subject)
        self.assertIn("token-123", email.body)
        self.assertIn("Скачать", email.alternatives[0][0])


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_BASE_URL="http://127.0.0.1:8000",
    GUEST_ACCESS_EXPIRE_HOURS=24,
    GUEST_ACCESS_MAX_DOWNLOADS=3,
    APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
)
class GuestAccessEmailOutboxTests(TestCase):
    def test_create_guest_access_email_outbox_encrypts_payload(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        payload = [
            {
                "title": "Архив 9",
                "category": "Категория",
                "price": "10.00",
                "image_url": "/static/images/example-product-image-1.png",
                "token": "token-abc",
            },
        ]

        outbox = create_guest_access_email_outbox(order=order, guest_access_payloads=payload)

        self.assertEqual(outbox.status, NotificationOutbox.Status.PENDING)
        self.assertEqual(outbox.notification_type, GUEST_ORDER_DOWNLOAD)
        self.assertNotIn(b"token-abc", outbox.payload_encrypted)
        self.assertEqual(decrypt_outbox_payload(outbox.payload_encrypted), payload)

    def test_process_guest_access_email_outbox_sends_email_and_marks_sent(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
            payment_account_no="PG250326OUTBOX",
        )
        outbox = create_guest_access_email_outbox(
            order=order,
            guest_access_payloads=[
                {
                    "title": "Архив 10",
                    "category": "Категория",
                    "price": "10.00",
                    "image_url": "/static/images/example-product-image-1.png",
                    "token": "token-outbox",
                },
            ],
        )

        processed = process_guest_access_email_outbox(outbox_id=outbox.id)
        outbox.refresh_from_db()

        self.assertTrue(processed)
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(outbox.attempts, 1)
        self.assertIsNotNone(outbox.sent_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_process_guest_access_email_outbox_is_idempotent_for_sent_record(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
            payment_account_no="PG250326OUTBOX2",
        )
        outbox = create_guest_access_email_outbox(
            order=order,
            guest_access_payloads=[
                {
                    "title": "Архив 11",
                    "category": "Категория",
                    "price": "10.00",
                    "image_url": "/static/images/example-product-image-1.png",
                    "token": "token-outbox-2",
                },
            ],
        )

        self.assertTrue(process_guest_access_email_outbox(outbox_id=outbox.id))
        processed_again = process_guest_access_email_outbox(outbox_id=outbox.id)
        outbox.refresh_from_db()

        self.assertFalse(processed_again)
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(outbox.attempts, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_cleanup_old_guest_access_email_outboxes_removes_sent_and_failed_records(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        old_sent = NotificationOutbox.objects.create(
            notification_type=GUEST_ORDER_DOWNLOAD,
            recipient=order.email,
            payload_encrypted=b"encrypted-1",
            status=NotificationOutbox.Status.SENT,
            sent_at=timezone.now() - timedelta(days=40),
        )
        old_failed = NotificationOutbox.objects.create(
            notification_type=GUEST_ORDER_DOWNLOAD,
            recipient=order.email,
            payload_encrypted=b"encrypted-2",
            status=NotificationOutbox.Status.FAILED,
        )
        NotificationOutbox.objects.filter(pk=old_failed.pk).update(
            updated_at=timezone.now() - timedelta(days=100),
        )
        fresh_sent = NotificationOutbox.objects.create(
            notification_type=GUEST_ORDER_DOWNLOAD,
            recipient=order.email,
            payload_encrypted=b"encrypted-3",
            status=NotificationOutbox.Status.SENT,
            sent_at=timezone.now() - timedelta(days=5),
        )

        result = cleanup_old_guest_access_email_outboxes(
            sent_retention_days=30,
            failed_retention_days=90,
        )

        self.assertEqual(result["sent_deleted"], 1)
        self.assertEqual(result["failed_deleted"], 1)
        self.assertFalse(NotificationOutbox.objects.filter(pk=old_sent.pk).exists())
        self.assertFalse(NotificationOutbox.objects.filter(pk=old_failed.pk).exists())
        self.assertTrue(NotificationOutbox.objects.filter(pk=fresh_sent.pk).exists())
