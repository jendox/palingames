from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.products.alerts import build_product_smoke_check_fingerprint
from apps.products.models import Category, Product, ProductFile, ProductImage
from apps.products.services.s3 import ProductFileDownloadUrlError, ProductFileMetadataError
from apps.products.smoke_checks import ProductSmokeCheckProblem, run_product_smoke_check
from apps.products.tasks import run_product_smoke_check_task
from apps.products.tests import create_published_product

DEFAULT_HEAD_METADATA = {"ContentLength": 1024, "ContentType": "application/zip"}


def _problem_codes(result) -> list[str]:
    return [problem.code for problem in result.problems]


@contextmanager
def patched_smoke_check(**overrides):
    defaults = {
        "apps.products.smoke_checks.head_product_file": {"return_value": DEFAULT_HEAD_METADATA},
        "apps.products.smoke_checks.generate_presigned_download_url": {
            "return_value": "https://example.com/download",
        },
        "apps.products.storage.ProductImageS3Storage.exists": {"return_value": True},
        "apps.products.storage.ProductImageS3Storage.size": {"return_value": 1234},
    }
    config = {**defaults, **overrides}
    with (
        patch("apps.products.smoke_checks.head_product_file", **config["apps.products.smoke_checks.head_product_file"]),
        patch(
            "apps.products.smoke_checks.generate_presigned_download_url",
            **config["apps.products.smoke_checks.generate_presigned_download_url"],
        ),
        patch(
            "apps.products.smoke_checks._fetch_public_product_page",
            **config["apps.products.smoke_checks._fetch_public_product_page"],
        ),
        patch(
            "apps.products.storage.ProductImageS3Storage.exists",
            **config["apps.products.storage.ProductImageS3Storage.exists"],
        ),
        patch(
            "apps.products.storage.ProductImageS3Storage.size",
            **config["apps.products.storage.ProductImageS3Storage.size"],
        ),
    ):
        yield


class ProductSmokeCheckTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Каталог", slug="katalog")

    def _create_ready_product(self, **kwargs) -> Product:
        product = create_published_product(
            title=kwargs.pop("title", "Healthy"),
            slug=kwargs.pop("slug", "healthy"),
            price=kwargs.pop("price", Decimal("10.00")),
            **kwargs,
        )
        product.categories.add(self.category)
        product_image = ProductImage.objects.create(product=product, order=0)
        ProductImage.objects.filter(pk=product_image.pk).update(image="previews/healthy/test.jpg")
        ProductFile.objects.create(
            product=product,
            file_key="products/healthy/archive.zip",
            original_filename="archive.zip",
            mime_type="application/zip",
            size_bytes=1024,
            is_active=True,
        )
        return product

    def _run_smoke_check(self, product_id: int, **overrides):
        product = Product.objects.get(pk=product_id)
        overrides.setdefault(
            "apps.products.smoke_checks._fetch_public_product_page",
            {"return_value": (200, product.title)},
        )
        with patched_smoke_check(**overrides):
            return run_product_smoke_check(product_id)


@override_settings(S3_PRODUCT_IMAGES_ENABLED=True)
class ProductSmokeCheckSkipTests(ProductSmokeCheckTestBase):
    def test_draft_incomplete_product_is_skipped(self):
        product = Product.objects.create(
            title="Draft",
            slug="draft",
            price=Decimal("10.00"),
            is_published=False,
        )

        result = run_product_smoke_check(product.id)

        self.assertTrue(result.skipped)
        self.assertEqual(result.skip_reason, "not_published")
        self.assertEqual(result.problems, [])

    def test_missing_product_is_skipped(self):
        result = run_product_smoke_check(999_999)

        self.assertTrue(result.skipped)
        self.assertEqual(result.skip_reason, "product_missing")
        self.assertEqual(result.problems, [])

    def test_stale_task_after_unpublish_is_skipped(self):
        product = self._create_ready_product(title="Published", slug="published")

        healthy = self._run_smoke_check(product.id)
        self.assertFalse(healthy.skipped)
        self.assertEqual(healthy.problems, [])

        Product.objects.filter(pk=product.pk).update(is_published=False)

        stale = run_product_smoke_check(product.id)
        self.assertTrue(stale.skipped)
        self.assertEqual(stale.skip_reason, "not_published")
        self.assertEqual(stale.problems, [])


@override_settings(S3_PRODUCT_IMAGES_ENABLED=True)
class ProductSmokeCheckBasicFieldTests(ProductSmokeCheckTestBase):
    def test_healthy_published_product_has_no_problems(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(product.id)

        self.assertFalse(result.skipped)
        self.assertIsNone(result.skip_reason)
        self.assertEqual(result.problems, [])

    def test_invalid_price_is_reported(self):
        product = self._create_ready_product(price=Decimal("0.00"))

        result = self._run_smoke_check(product.id)

        self.assertIn("invalid_product_price", _problem_codes(result))

    def test_missing_category_is_reported(self):
        product = create_published_product(title="No category", slug="no-category", price=Decimal("10.00"))

        result = self._run_smoke_check(product.id)

        self.assertIn("product_category_missing", _problem_codes(result))

    def test_empty_title_is_reported(self):
        product = self._create_ready_product(title="Has title", slug="empty-title")
        Product.objects.filter(pk=product.pk).update(title="")

        result = self._run_smoke_check(product.id)

        self.assertIn("product_title_missing", _problem_codes(result))

    def test_empty_slug_is_reported(self):
        product = self._create_ready_product(title="Empty slug", slug="empty-slug")
        Product.objects.filter(pk=product.pk).update(slug="")

        result = self._run_smoke_check(product.id)

        self.assertIn("product_slug_missing", _problem_codes(result))


@override_settings(S3_PRODUCT_IMAGES_ENABLED=True)
class ProductSmokeCheckAssetTests(ProductSmokeCheckTestBase):
    def test_missing_image_is_reported(self):
        product = self._create_ready_product()
        product.images.all().delete()

        result = self._run_smoke_check(product.id)

        self.assertIn("product_image_missing", _problem_codes(result))

    def test_missing_image_object_is_reported(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.storage.ProductImageS3Storage.exists": {"return_value": False},
                "apps.products.storage.ProductImageS3Storage.size": {"return_value": 0},
            },
        )

        self.assertIn("product_image_object_missing", _problem_codes(result))

    def test_empty_image_object_is_reported(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.storage.ProductImageS3Storage.exists": {"return_value": True},
                "apps.products.storage.ProductImageS3Storage.size": {"return_value": 0},
            },
        )

        self.assertIn("product_image_empty", _problem_codes(result))

    def test_missing_active_file_is_reported(self):
        product = self._create_ready_product()
        product.files.all().delete()

        result = self._run_smoke_check(product.id)

        self.assertIn("active_product_file_missing", _problem_codes(result))

    def test_unavailable_file_object_is_reported(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.smoke_checks.head_product_file": {
                    "side_effect": ProductFileMetadataError("boom"),
                },
            },
        )

        self.assertIn("product_file_object_unavailable", _problem_codes(result))

    def test_empty_file_object_is_reported(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.smoke_checks.head_product_file": {
                    "return_value": {"ContentLength": 0, "ContentType": "application/zip"},
                },
            },
        )

        self.assertIn("product_file_object_empty", _problem_codes(result))

    def test_file_size_mismatch_is_reported(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.smoke_checks.head_product_file": {
                    "return_value": {"ContentLength": 2048, "ContentType": "application/zip"},
                },
            },
        )

        self.assertIn("product_file_size_mismatch", _problem_codes(result))

    def test_content_type_mismatch_is_warning(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.smoke_checks.head_product_file": {
                    "return_value": {"ContentLength": 1024, "ContentType": "application/pdf"},
                },
            },
        )

        problems = [problem for problem in result.problems if problem.code == "product_file_content_type_mismatch"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].severity, "warning")

    def test_download_url_generation_failure_is_reported(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.smoke_checks.generate_presigned_download_url": {
                    "side_effect": ProductFileDownloadUrlError("boom"),
                },
            },
        )

        self.assertIn("product_download_url_generation_failed", _problem_codes(result))

    def test_public_page_non_200_is_reported(self):
        product = self._create_ready_product()

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.smoke_checks._fetch_public_product_page": {"return_value": (404, "")},
            },
        )

        self.assertIn("product_page_unavailable", _problem_codes(result))

    def test_public_page_missing_title_is_warning(self):
        product = self._create_ready_product(title="Visible title", slug="visible-title")

        result = self._run_smoke_check(
            product.id,
            **{
                "apps.products.smoke_checks._fetch_public_product_page": {"return_value": (200, "No title here")},
            },
        )

        problems = [problem for problem in result.problems if problem.code == "product_page_content_invalid"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].severity, "warning")


class ProductSmokeCheckAlertTests(TestCase):
    def test_fingerprint_includes_image_id(self):
        problem = ProductSmokeCheckProblem(
            product_id=65,
            code="product_image_object_missing",
            severity="critical",
            details={"image_id": 123, "object_key": "previews/x.jpg"},
        )

        self.assertEqual(
            build_product_smoke_check_fingerprint(problem),
            "products.smoke_check:65:product_image_object_missing:123",
        )

    def test_fingerprint_includes_file_id(self):
        problem = ProductSmokeCheckProblem(
            product_id=65,
            code="product_file_size_mismatch",
            severity="critical",
            details={"file_id": 77, "db_size_bytes": 1024, "s3_size_bytes": 2048},
        )

        self.assertEqual(
            build_product_smoke_check_fingerprint(problem),
            "products.smoke_check:65:product_file_size_mismatch:77",
        )


@override_settings(S3_PRODUCT_IMAGES_ENABLED=True)
class ProductSmokeCheckTaskTests(ProductSmokeCheckTestBase):
    @patch("apps.products.tasks.alert_product_smoke_check_problem")
    def test_task_skipped_draft_sends_no_alerts(self, alert_mock):
        product = Product.objects.create(
            title="Draft",
            slug="draft-task",
            price=Decimal("10.00"),
            is_published=False,
        )

        summary = run_product_smoke_check_task(product.id)

        self.assertTrue(summary["skipped"])
        self.assertEqual(summary["skip_reason"], "not_published")
        self.assertEqual(summary["problems"], 0)
        self.assertEqual(summary["alerts_sent"], 0)
        alert_mock.assert_not_called()

    @patch("apps.products.tasks.alert_product_smoke_check_problem")
    def test_task_healthy_published_sends_no_alerts(self, alert_mock):
        product = self._create_ready_product(title="Task healthy", slug="task-healthy")

        with patched_smoke_check(
            **{
                "apps.products.smoke_checks._fetch_public_product_page": {
                    "return_value": (200, "Task healthy"),
                },
            },
        ):
            summary = run_product_smoke_check_task(product.id)

        self.assertFalse(summary["skipped"])
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["problems"], 0)
        self.assertEqual(summary["alerts_sent"], 0)
        alert_mock.assert_not_called()

    @patch("apps.products.tasks.alert_product_smoke_check_problem", return_value=True)
    def test_task_broken_published_sends_alerts(self, alert_mock):
        product = self._create_ready_product(title="Broken", slug="broken-task")
        product.images.all().delete()

        with patched_smoke_check(
            **{
                "apps.products.smoke_checks._fetch_public_product_page": {
                    "return_value": (200, "Broken"),
                },
            },
        ):
            summary = run_product_smoke_check_task(product.id)

        self.assertFalse(summary["skipped"])
        self.assertFalse(summary["ok"])
        self.assertGreaterEqual(summary["problems"], 1)
        self.assertGreaterEqual(summary["alerts_sent"], 1)
        alert_mock.assert_called()
        self.assertEqual(alert_mock.call_args.args[0].product_id, product.id)
