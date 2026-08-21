from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.core.admin_site import admin_site
from apps.products.admin import ProductAdmin, ProductFileAdmin
from apps.products.jobs import maybe_enqueue_product_smoke_check, should_enqueue_product_smoke_check
from apps.products.models import Category, Product, ProductFile
from apps.products.tests import ADMIN_DIRECT_S3_SETTINGS, create_published_product


class ProductSmokeCheckTriggerLogicTests(TestCase):
    def test_should_enqueue_only_for_published(self):
        self.assertFalse(should_enqueue_product_smoke_check(is_published=False))
        self.assertTrue(should_enqueue_product_smoke_check(is_published=True))


class ProductAdminSmokeCheckTriggerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Каталог", slug="katalog")
        cls.staff_user = get_user_model().objects.create_user(
            email="smoke-admin@example.com",
            password="pass-123",
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.product_admin = ProductAdmin(Product, admin_site)

    def _admin_request(self):
        request = RequestFactory().post("/admin/products/product/")
        request.user = self.staff_user
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def _minimal_form(self, product: Product):
        form_class = self.product_admin.get_form(self._admin_request())
        form = form_class(instance=product)
        form.save_m2m = MagicMock()
        return form

    @patch("apps.products.admin.enqueue_product_smoke_check")
    def test_save_related_draft_does_not_enqueue(self, enqueue_mock):
        product = Product.objects.create(
            title="Draft",
            slug="draft-trigger",
            price=Decimal("10.00"),
            is_published=False,
        )
        form = self._minimal_form(product)

        with self.captureOnCommitCallbacks(execute=True):
            self.product_admin.save_related(self._admin_request(), form, formsets=[], change=True)

        enqueue_mock.assert_not_called()

    @patch("apps.products.admin.enqueue_product_smoke_check")
    def test_save_related_publish_enqueues_once(self, enqueue_mock):
        product = Product.objects.create(
            title="Publish me",
            slug="publish-me",
            price=Decimal("10.00"),
            is_published=True,
        )
        product.categories.add(self.category)
        form = self._minimal_form(product)

        with self.captureOnCommitCallbacks(execute=True):
            self.product_admin.save_related(self._admin_request(), form, formsets=[], change=True)

        enqueue_mock.assert_called_once_with(product.pk)

    @patch("apps.products.admin.enqueue_product_smoke_check")
    def test_save_related_published_edit_enqueues_recheck(self, enqueue_mock):
        product = create_published_product(title="Published", slug="published-edit", price=Decimal("10.00"))
        product.categories.add(self.category)
        form = self._minimal_form(product)

        with self.captureOnCommitCallbacks(execute=True):
            self.product_admin.save_related(self._admin_request(), form, formsets=[], change=True)

        enqueue_mock.assert_called_once_with(product.pk)

    @patch("apps.products.admin.enqueue_product_smoke_checks_for_ids")
    def test_bulk_publish_enqueues_only_newly_published(self, enqueue_bulk_mock):
        draft_a = Product.objects.create(title="A", slug="bulk-a", price=Decimal("10.00"), is_published=False)
        draft_b = Product.objects.create(title="B", slug="bulk-b", price=Decimal("10.00"), is_published=False)
        already = create_published_product(title="Already", slug="bulk-already", price=Decimal("10.00"))

        queryset = Product.objects.filter(pk__in=[draft_a.pk, draft_b.pk, already.pk])

        with self.captureOnCommitCallbacks(execute=True):
            ProductAdmin.make_published(self.product_admin, self._admin_request(), queryset)

        enqueue_bulk_mock.assert_called_once()
        queued_ids = list(enqueue_bulk_mock.call_args.args[0])
        self.assertEqual(set(queued_ids), {draft_a.pk, draft_b.pk})

    @patch("apps.products.admin.enqueue_product_smoke_checks_for_ids")
    def test_bulk_unpublish_does_not_enqueue(self, enqueue_bulk_mock):
        product = create_published_product(title="Unpublish", slug="bulk-unpublish", price=Decimal("10.00"))
        queryset = Product.objects.filter(pk=product.pk)

        with self.captureOnCommitCallbacks(execute=True):
            ProductAdmin.make_unpublished(self.product_admin, self._admin_request(), queryset)

        enqueue_bulk_mock.assert_not_called()


class MaybeEnqueueProductSmokeCheckTests(TestCase):
    @patch("apps.products.jobs.enqueue_product_smoke_check")
    def test_maybe_enqueue_skips_draft_product(self, enqueue_mock):
        product = Product.objects.create(
            title="Draft file",
            slug="draft-file",
            price=Decimal("10.00"),
            is_published=False,
        )

        maybe_enqueue_product_smoke_check(product.id)

        enqueue_mock.assert_not_called()

    @patch("apps.products.jobs.enqueue_product_smoke_check")
    def test_maybe_enqueue_published_product(self, enqueue_mock):
        product = create_published_product(title="Published file", slug="published-file", price=Decimal("10.00"))

        maybe_enqueue_product_smoke_check(product.id)

        enqueue_mock.assert_called_once_with(product.id)


@override_settings(ADMIN_DIRECT_S3_UPLOAD_ENABLED=False)
class ProductFileAdminSmokeCheckTriggerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = get_user_model().objects.create_user(
            email="file-admin@example.com",
            password="pass-123",
            is_staff=True,
            is_superuser=True,
        )
        cls.published_product = create_published_product(
            title="File product",
            slug="file-product",
            price=Decimal("10.00"),
        )
        cls.draft_product = Product.objects.create(
            title="Draft file product",
            slug="draft-file-product",
            price=Decimal("10.00"),
            is_published=False,
        )

    def setUp(self):
        self.product_file_admin = ProductFileAdmin(ProductFile, admin_site)

    def _admin_request(self):
        request = RequestFactory().post("/admin/products/productfile/")
        request.user = self.staff_user
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def _upload_form(self):
        form = MagicMock()
        form.cleaned_data = {
            "upload": SimpleUploadedFile("game.zip", b"zip-content", content_type="application/zip"),
        }
        return form

    @patch("apps.products.admin.maybe_enqueue_product_smoke_check")
    @patch("apps.products.admin.upload_product_file")
    def test_product_file_upload_on_published_enqueues(self, upload_mock, maybe_enqueue_mock):
        upload_mock.return_value = {
            "file_key": "file-product/game.zip",
            "original_filename": "game.zip",
            "mime_type": "application/zip",
            "size_bytes": 12,
            "checksum_sha256": None,
        }
        product_file = ProductFile(product=self.published_product, is_active=True)

        with self.captureOnCommitCallbacks(execute=True):
            self.product_file_admin.save_model(self._admin_request(), product_file, self._upload_form(), change=False)

        maybe_enqueue_mock.assert_called_once_with(self.published_product.id)

    @patch("apps.products.jobs.enqueue_product_smoke_check")
    @patch("apps.products.admin.upload_product_file")
    def test_product_file_upload_on_draft_does_not_enqueue(self, upload_mock, enqueue_mock):
        upload_mock.return_value = {
            "file_key": "draft-file-product/game.zip",
            "original_filename": "game.zip",
            "mime_type": "application/zip",
            "size_bytes": 12,
            "checksum_sha256": None,
        }
        product_file = ProductFile(product=self.draft_product, is_active=True)

        with self.captureOnCommitCallbacks(execute=True):
            self.product_file_admin.save_model(self._admin_request(), product_file, self._upload_form(), change=False)

        enqueue_mock.assert_not_called()


@override_settings(**ADMIN_DIRECT_S3_SETTINGS)
class ProductFileFinalizeSmokeCheckTriggerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.published_product = create_published_product(
            title="Finalize published",
            slug="finalize-published",
            price=Decimal("10.00"),
        )
        cls.draft_product = Product.objects.create(
            title="Finalize draft",
            slug="finalize-draft",
            price=Decimal("10.00"),
            is_published=False,
        )
        cls.staff_user = get_user_model().objects.create_user(
            email="finalize-admin@example.com",
            password="pass-123",
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.staff_user)

    def _csrf_token(self) -> str:
        response = self.client.get(reverse("admin:products_productfile_add"))
        self.assertEqual(response.status_code, 200)
        return self.client.cookies["csrftoken"].value

    @patch("apps.products.admin_upload_views.maybe_enqueue_product_smoke_check")
    @patch("apps.products.admin_upload_views.verify_uploaded_object")
    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_published_product_enqueues_smoke_check(
        self,
        mock_generate_presigned_upload_url,
        mock_verify_uploaded_object,
        maybe_enqueue_mock,
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
        presign_response = self.client.post(
            reverse("admin-product-file-presign"),
            data=json.dumps(
                {
                    "product_id": self.published_product.id,
                    "filename": "game.zip",
                    "content_type": "application/zip",
                    "size_bytes": 128,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        presign_payload = json.loads(presign_response.content.decode())

        with self.captureOnCommitCallbacks(execute=True):
            finalize_response = self.client.post(
                reverse("admin-product-file-finalize"),
                data=json.dumps(
                    {
                        "intent_id": presign_payload["intent_id"],
                        "product_id": self.published_product.id,
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
        maybe_enqueue_mock.assert_called_once_with(self.published_product.id)

    @patch("apps.products.jobs.enqueue_product_smoke_check")
    @patch("apps.products.admin_upload_views.verify_uploaded_object")
    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_draft_product_does_not_enqueue(
        self,
        mock_generate_presigned_upload_url,
        mock_verify_uploaded_object,
        enqueue_mock,
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
        presign_response = self.client.post(
            reverse("admin-product-file-presign"),
            data=json.dumps(
                {
                    "product_id": self.draft_product.id,
                    "filename": "game.zip",
                    "content_type": "application/zip",
                    "size_bytes": 128,
                },
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        presign_payload = json.loads(presign_response.content.decode())

        with self.captureOnCommitCallbacks(execute=True):
            finalize_response = self.client.post(
                reverse("admin-product-file-finalize"),
                data=json.dumps(
                    {
                        "intent_id": presign_payload["intent_id"],
                        "product_id": self.draft_product.id,
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
        enqueue_mock.assert_not_called()
