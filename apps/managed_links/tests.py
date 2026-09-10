from __future__ import annotations

import json
from unittest.mock import patch

import qrcode
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.managed_links.models import ManagedLink
from apps.managed_links.services.qr import build_managed_link_qr_url, generate_qr_png, generate_qr_svg
from apps.managed_links.services.storage import _allowed_upload_extensions
from apps.managed_links.tokens import generate_managed_link_token
from apps.managed_links.upload_intents import create_managed_link_upload_intent

MANAGED_LINK_SETTINGS = {
    "S3_MANAGED_LINKS_BUCKET_NAME": "qr-assets",
    "MANAGED_LINK_DIRECT_S3_UPLOAD_ENABLED": True,
    "MANAGED_LINK_UPLOAD_MAX_BYTES": 524288000,
    "MANAGED_LINK_UPLOAD_PRESIGN_TTL_SECONDS": 900,
    "MANAGED_LINK_UPLOAD_ALLOWED_EXTENSIONS": ".pdf,.png,.jpg,.jpeg",
    "MANAGED_LINK_REDIRECT_IP_RATE_LIMIT": 60,
    "MANAGED_LINK_REDIRECT_IP_RATE_LIMIT_WINDOW_SECONDS": 60,
    "SITE_BASE_URL": "https://palingames.by",
}


def create_managed_link(**kwargs) -> ManagedLink:
    defaults = {
        "title": "Test material",
        "external_url": "https://disk.yandex.ru/i/example",
        "is_active": True,
    }
    defaults.update(kwargs)
    link = ManagedLink(**defaults)
    link.full_clean()
    link.save()
    return link


@override_settings(**MANAGED_LINK_SETTINGS)
class ManagedLinkModelTests(TestCase):
    def test_token_is_generated_and_url_safe(self):
        link = create_managed_link()
        self.assertGreaterEqual(len(link.token), 20)
        self.assertRegex(link.token, r"^[A-Za-z0-9_-]+$")
        self.assertEqual(link.token_prefix, link.token[:8])

    def test_active_link_requires_destination(self):
        link = ManagedLink(title="Draft", is_active=True)
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_new_link_defaults_to_inactive(self):
        link = ManagedLink(title="Draft")
        self.assertFalse(link.is_active)

    def test_inactive_link_can_be_saved_without_destination(self):
        link = ManagedLink(title="Draft", is_active=False)
        link.full_clean()
        link.save()
        self.assertFalse(link.is_active)

    def test_rejects_non_http_external_url(self):
        link = ManagedLink(
            title="Bad",
            external_url="javascript:alert(1)",
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_token_cannot_change_after_creation(self):
        link = create_managed_link()
        link.token = generate_managed_link_token()
        with self.assertRaises(ValidationError):
            link.save()

    def test_permanent_url_uses_site_base_url(self):
        link = create_managed_link()
        self.assertEqual(link.permanent_url, f"https://palingames.by/go/{link.token}/")

    def test_active_source_prefers_s3(self):
        link = create_managed_link(
            s3_file_key="abc/file.pdf",
            original_filename="file.pdf",
        )
        self.assertEqual(link.active_source, ManagedLink.ActiveSource.S3)


@override_settings(**MANAGED_LINK_SETTINGS)
class ManagedLinkRedirectViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("apps.managed_links.services.redirect.generate_presigned_download_url")
    def test_redirects_to_external_url(self, mock_presign):
        link = create_managed_link(external_url="https://disk.yandex.ru/i/example")
        response = self.client.get(reverse("managed-link-redirect", args=[link.token]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://disk.yandex.ru/i/example")
        mock_presign.assert_not_called()
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow, noarchive")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    @patch("apps.managed_links.services.redirect.generate_presigned_download_url")
    def test_prefers_s3_over_external(self, mock_presign):
        mock_presign.return_value = "https://storage.example/presigned"
        link = create_managed_link(
            external_url="https://disk.yandex.ru/i/example",
            s3_file_key="token/file.pdf",
            original_filename="file.pdf",
        )
        response = self.client.get(reverse("managed-link-redirect", args=[link.token]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://storage.example/presigned")
        mock_presign.assert_called_once()

    @patch("apps.managed_links.services.redirect.generate_presigned_download_url")
    def test_falls_back_to_external_on_s3_error(self, mock_presign):
        from apps.managed_links.services.storage import ManagedLinkDownloadUrlError

        mock_presign.side_effect = ManagedLinkDownloadUrlError("boom")
        link = create_managed_link(
            external_url="https://disk.yandex.ru/i/fallback",
            s3_file_key="token/file.pdf",
            original_filename="file.pdf",
        )
        response = self.client.get(reverse("managed-link-redirect", args=[link.token]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://disk.yandex.ru/i/fallback")

    @patch("apps.managed_links.services.redirect.generate_presigned_download_url")
    def test_returns_503_when_s3_fails_without_fallback(self, mock_presign):
        from apps.managed_links.services.storage import ManagedLinkDownloadUrlError

        mock_presign.side_effect = ManagedLinkDownloadUrlError("boom")
        link = create_managed_link(
            external_url="",
            s3_file_key="token/file.pdf",
            original_filename="file.pdf",
        )
        response = self.client.get(reverse("managed-link-redirect", args=[link.token]))
        self.assertEqual(response.status_code, 503)

    def test_unknown_token_returns_404(self):
        response = self.client.get(reverse("managed-link-redirect", args=["unknown-token-value"]))
        self.assertEqual(response.status_code, 404)

    def test_inactive_link_returns_404(self):
        link = create_managed_link(is_active=False)
        response = self.client.get(reverse("managed-link-redirect", args=[link.token]))
        self.assertEqual(response.status_code, 404)

    @patch("apps.managed_links.services.redirect.generate_presigned_download_url")
    def test_head_returns_redirect_headers(self, mock_presign):
        mock_presign.return_value = "https://storage.example/presigned"
        link = create_managed_link(
            s3_file_key="token/file.pdf",
            original_filename="file.pdf",
        )
        response = self.client.head(reverse("managed-link-redirect", args=[link.token]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://storage.example/presigned")


@override_settings(**MANAGED_LINK_SETTINGS)
class ManagedLinkAdminUploadTests(TestCase):
    def setUp(self):
        caches["default"].clear()
        _allowed_upload_extensions.cache_clear()
        self.link = create_managed_link()
        self.staff_user = get_user_model().objects.create_user(
            email="staff@example.com",
            password="pass-123",
            is_staff=True,
            is_superuser=True,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.staff_user)

    def _csrf_token(self) -> str:
        response = self.client.get(reverse("admin:managed_links_managedlink_change", args=[self.link.pk]))
        self.assertEqual(response.status_code, 200)
        return self.client.cookies["csrftoken"].value

    @patch("apps.managed_links.admin_upload_views.generate_presigned_upload_url")
    def test_presign_returns_upload_metadata(self, mock_generate_presigned_upload_url):
        mock_generate_presigned_upload_url.return_value = {
            "upload_url": "https://storage.example/upload",
            "required_headers": {"Content-Type": "application/pdf"},
            "expires_in": 900,
        }
        token = self._csrf_token()
        response = self.client.post(
            reverse("admin-managed-link-presign"),
            data=json.dumps(
                {
                    "managed_link_id": self.link.id,
                    "filename": "material.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 128,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertTrue(payload["file_key"].startswith(f"{self.link.token}/"))

    def test_presign_requires_staff(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("home"))
        token = client.cookies["csrftoken"].value
        response = client.post(
            reverse("admin-managed-link-presign"),
            data=json.dumps(
                {
                    "managed_link_id": self.link.id,
                    "filename": "material.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 128,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 403)

    @patch("apps.managed_links.admin_upload_views.delete_managed_link_file")
    @patch("apps.managed_links.admin_upload_views.verify_uploaded_object")
    @patch("apps.managed_links.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_replaces_file_and_deletes_previous(
        self,
        mock_generate_presigned_upload_url,
        mock_verify_uploaded_object,
        mock_delete_managed_link_file,
    ):
        self.link.s3_file_key = f"{self.link.token}/old.pdf"
        self.link.original_filename = "old.pdf"
        self.link.save()
        mock_generate_presigned_upload_url.return_value = {
            "upload_url": "https://storage.example/upload",
            "required_headers": {"Content-Type": "application/pdf"},
            "expires_in": 900,
        }
        mock_verify_uploaded_object.return_value = {"ContentLength": 128}
        intent_id = create_managed_link_upload_intent(
            user_id=self.staff_user.id,
            managed_link_id=self.link.id,
            file_key=f"{self.link.token}/new.pdf",
            size_bytes=128,
            content_type="application/pdf",
        )
        token = self._csrf_token()
        response = self.client.post(
            reverse("admin-managed-link-finalize"),
            data=json.dumps(
                {
                    "intent_id": intent_id,
                    "managed_link_id": self.link.id,
                    "file_key": f"{self.link.token}/new.pdf",
                    "original_filename": "new.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 128,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
        self.link.refresh_from_db()
        self.assertEqual(self.link.s3_file_key, f"{self.link.token}/new.pdf")
        mock_delete_managed_link_file.assert_called_once_with(file_key=f"{self.link.token}/old.pdf")

    @patch("apps.managed_links.admin_upload_views.delete_managed_link_file")
    @patch("apps.managed_links.admin_upload_views.verify_uploaded_object")
    @patch("apps.managed_links.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_auto_activates_inactive_link(
        self,
        mock_generate_presigned_upload_url,
        mock_verify_uploaded_object,
        mock_delete_managed_link_file,
    ):
        self.link.is_active = False
        self.link.save()
        mock_generate_presigned_upload_url.return_value = {
            "upload_url": "https://storage.example/upload",
            "required_headers": {"Content-Type": "application/pdf"},
            "expires_in": 900,
        }
        mock_verify_uploaded_object.return_value = {"ContentLength": 128}
        intent_id = create_managed_link_upload_intent(
            user_id=self.staff_user.id,
            managed_link_id=self.link.id,
            file_key=f"{self.link.token}/material.pdf",
            size_bytes=128,
            content_type="application/pdf",
        )
        token = self._csrf_token()
        response = self.client.post(
            reverse("admin-managed-link-finalize"),
            data=json.dumps(
                {
                    "intent_id": intent_id,
                    "managed_link_id": self.link.id,
                    "file_key": f"{self.link.token}/material.pdf",
                    "original_filename": "material.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 128,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
        self.link.refresh_from_db()
        self.assertTrue(self.link.is_active)
        self.assertEqual(self.link.s3_file_key, f"{self.link.token}/material.pdf")
        mock_delete_managed_link_file.assert_not_called()


@override_settings(**MANAGED_LINK_SETTINGS)
class ManagedLinkQrTests(TestCase):
    def test_qr_url_contains_permanent_site_url(self):
        link = create_managed_link()
        self.assertEqual(build_managed_link_qr_url(token=link.token), link.permanent_url)

    def test_png_qr_encodes_permanent_url(self):
        link = create_managed_link()
        captured: dict[str, str] = {}

        original_add_data = qrcode.QRCode.add_data

        def capture_add_data(qr_self, data, optimize=20):
            captured["data"] = data
            return original_add_data(qr_self, data, optimize=optimize)

        with patch.object(qrcode.QRCode, "add_data", capture_add_data):
            png_bytes = generate_qr_png(managed_link=link, with_logo=False)

        self.assertTrue(png_bytes.startswith(b"\x89PNG"))
        self.assertEqual(captured["data"], link.permanent_url)

    def test_svg_qr_is_valid_and_uses_permanent_url(self):
        link = create_managed_link()
        svg_bytes = generate_qr_svg(managed_link=link)
        self.assertTrue(svg_bytes.startswith(b"<?xml"))
        self.assertIn(b"segno", svg_bytes)
        self.assertNotIn(b"disk.yandex.ru", svg_bytes)
        self.assertIn(b"qrline", svg_bytes)

    def test_admin_qr_png_requires_staff(self):
        link = create_managed_link()
        response = Client().get(reverse("admin:managed_links_managedlink_qr_png", args=[link.pk]))
        self.assertEqual(response.status_code, 302)

    def test_admin_qr_png_available_for_staff(self):
        link = create_managed_link()
        staff = get_user_model().objects.create_user(
            email="qr-staff@example.com",
            password="pass-123",
            is_staff=True,
            is_superuser=True,
        )
        client = Client()
        client.force_login(staff)
        response = client.get(reverse("admin:managed_links_managedlink_qr_png", args=[link.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")


@override_settings(**MANAGED_LINK_SETTINGS)
class ManagedLinkMiddlewareTests(TestCase):
    def test_managed_link_path_redacted_in_logs(self):
        from apps.core.middleware import redact_request_path_for_logging

        self.assertEqual(
            redact_request_path_for_logging("/go/abc123token/"),
            "/go/<redacted>/",
        )
        self.assertEqual(
            redact_request_path_for_logging("/catalog/"),
            "/catalog/",
        )
