import io
from decimal import Decimal
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from apps.products.models import Category, Product, ProductImage
from apps.products.services.product_image_cleanup import (
    DELETE_REASON_THUMB_REGENERATE,
    is_product_image_key_referenced,
)
from apps.products.services.product_image_thumbnails import (
    ProductImageThumbnailTransientError,
    generate_product_image_thumbnails,
)


def _png_bytes(width: int, height: int, *, rgba: bool = False) -> bytes:
    mode = "RGBA" if rgba else "RGB"
    img = Image.new(mode, (width, height), (255, 0, 0, 128) if rgba else (255, 0, 0))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@override_settings(
    S3_PRODUCT_IMAGES_ENABLED=True,
    CELERY_TASK_ALWAYS_EAGER=True,
    PRODUCT_IMAGE_THUMB_STALE_DELETE_DELAY_SECONDS=86400,
)
class ProductImageThumbnailServiceTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            title="Thumb product",
            slug="thumb-product",
            price=Decimal("10.00"),
            is_published=True,
        )
        self.image = ProductImage.objects.create(
            product=self.product,
            order=0,
            image="previews/thumb-product/source.png",
        )

    @patch("apps.products.services.product_image_thumbnails.get_s3_client")
    @patch("apps.products.services.product_image_thumbnails.read_product_image_source_bytes")
    def test_generates_webp_variants_and_skips_repeat(self, read_bytes_mock, s3_client_mock):
        read_bytes_mock.return_value = _png_bytes(1600, 1000)
        client = MagicMock()
        s3_client_mock.return_value = client
        client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

        result = generate_product_image_thumbnails(self.image.pk)
        self.assertEqual(result, "generated")
        self.image.refresh_from_db()
        self.assertTrue(self.image.has_fresh_thumbs)
        self.assertEqual(self.image.thumb_400_display_width, 400)
        self.assertEqual(self.image.thumb_800_display_width, 800)
        self.assertEqual(client.upload_fileobj.call_count, 2)
        extra = client.upload_fileobj.call_args.kwargs["ExtraArgs"]
        self.assertEqual(extra["ContentType"], "image/webp")
        self.assertIn("immutable", extra["CacheControl"])

        result_repeat = generate_product_image_thumbnails(self.image.pk)
        self.assertEqual(result_repeat, "skipped")
        self.assertEqual(client.upload_fileobj.call_count, 2)

    @patch("apps.products.services.product_image_thumbnails.get_s3_client")
    @patch("apps.products.services.product_image_thumbnails.read_product_image_source_bytes")
    def test_small_original_uses_single_object_and_actual_srcset_width(self, read_bytes_mock, s3_client_mock):
        read_bytes_mock.return_value = _png_bytes(300, 200)
        client = MagicMock()
        s3_client_mock.return_value = client
        client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

        generate_product_image_thumbnails(self.image.pk)
        self.image.refresh_from_db()
        self.assertEqual(self.image.thumb_400_key, self.image.thumb_800_key)
        self.assertEqual(self.image.thumb_400_display_width, 300)
        self.assertIn("300w", self.image.listing_srcset)
        self.assertNotIn("800w", self.image.listing_srcset)
        self.assertEqual(client.upload_fileobj.call_count, 1)

    @patch("apps.products.services.product_image_thumbnails.schedule_product_image_delayed_delete_on_commit")
    @patch("apps.products.services.product_image_thumbnails.get_s3_client")
    @patch("apps.products.services.product_image_thumbnails.read_product_image_source_bytes")
    def test_force_uses_delayed_cleanup_for_old_keys(self, read_bytes_mock, s3_client_mock, delayed_delete_mock):
        read_bytes_mock.return_value = _png_bytes(1200, 800)
        client = MagicMock()
        s3_client_mock.return_value = client
        client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        generate_product_image_thumbnails(self.image.pk)
        self.image.refresh_from_db()
        old_400 = self.image.thumb_400_key

        read_bytes_mock.return_value = _png_bytes(1200, 800, rgba=True)
        generate_product_image_thumbnails(self.image.pk, force=True)
        self.image.refresh_from_db()
        self.assertNotEqual(self.image.thumb_400_key, old_400)
        delayed_delete_mock.assert_called()
        regenerate_calls = [
            call
            for call in delayed_delete_mock.call_args_list
            if call.kwargs.get("reason") == DELETE_REASON_THUMB_REGENERATE
        ]
        self.assertTrue(regenerate_calls)

    @patch("apps.products.services.product_image_thumbnails.read_product_image_source_bytes")
    def test_transient_read_error_bubbles(self, read_bytes_mock):
        read_bytes_mock.side_effect = ProductImageThumbnailTransientError("503")
        with self.assertRaises(ProductImageThumbnailTransientError):
            generate_product_image_thumbnails(self.image.pk)

    @patch("apps.products.services.product_image_thumbnails._object_exists", return_value=False)
    @patch("apps.products.services.product_image_thumbnails.get_s3_client")
    @patch("apps.products.services.product_image_thumbnails.read_product_image_source_bytes")
    def test_second_worker_skips_after_first_publish(self, read_bytes_mock, s3_client_mock, _exists_mock):
        read_bytes_mock.return_value = _png_bytes(900, 600)
        client = MagicMock()
        s3_client_mock.return_value = client
        client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

        first = generate_product_image_thumbnails(self.image.pk)
        upload_count_after_first = client.upload_fileobj.call_count
        second = generate_product_image_thumbnails(self.image.pk)

        self.assertEqual(first, "generated")
        self.assertEqual(second, "skipped")
        self.assertEqual(client.upload_fileobj.call_count, upload_count_after_first)

    @patch("apps.products.services.product_image_thumbnails.schedule_product_image_delete_on_commit")
    @patch("apps.products.services.product_image_thumbnails._object_exists", return_value=False)
    @patch("apps.products.services.product_image_thumbnails.get_s3_client")
    @patch("apps.products.services.product_image_thumbnails.read_product_image_source_bytes")
    def test_force_second_worker_skips_when_keys_already_switched(
        self,
        read_bytes_mock,
        s3_client_mock,
        _exists_mock,
        delete_mock,
    ):
        read_bytes_mock.return_value = _png_bytes(900, 600)
        client = MagicMock()
        s3_client_mock.return_value = client
        client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        generate_product_image_thumbnails(self.image.pk)

        first = generate_product_image_thumbnails(self.image.pk, force=True)
        uploads_after_first_force = client.upload_fileobj.call_count
        second = generate_product_image_thumbnails(self.image.pk, force=True)

        self.assertEqual(first, "generated")
        self.assertEqual(second, "skipped")
        self.assertLessEqual(client.upload_fileobj.call_count, uploads_after_first_force + 2)

    @patch("apps.products.services.product_image_thumbnails.schedule_product_image_delete_on_commit")
    @patch("apps.products.services.product_image_thumbnails._upload_webp")
    @patch("apps.products.services.product_image_thumbnails.read_product_image_source_bytes")
    def test_publish_skips_when_source_changes_mid_flight(self, read_bytes_mock, upload_mock, delete_mock):
        read_bytes_mock.return_value = _png_bytes(900, 600)

        def change_source(*args, **kwargs):
            ProductImage.objects.filter(pk=self.image.pk).update(image="previews/thumb-product/new-source.png")

        upload_mock.side_effect = change_source
        result = generate_product_image_thumbnails(self.image.pk)
        self.assertEqual(result, "skipped")
        self.image.refresh_from_db()
        self.assertFalse(self.image.has_fresh_thumbs)
        delete_mock.assert_called()


class ThumbnailReferenceAndCommandTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            title="Ref product",
            slug="ref-product",
            price=Decimal("12.00"),
            is_published=True,
        )

    def test_is_product_image_key_referenced_includes_thumb_fields(self):
        image = ProductImage.objects.create(
            product=self.product,
            order=0,
            image="previews/ref-product/orig.png",
            thumb_400_key="previews/ref-product/orig_w400.webp",
            thumb_800_key="previews/ref-product/orig_w800.webp",
            thumbs_source_key="previews/ref-product/orig.png",
        )
        self.assertTrue(is_product_image_key_referenced(image.thumb_400_key))
        self.assertTrue(is_product_image_key_referenced(image.thumb_800_key))

    @patch("apps.products.management.commands.generate_product_image_thumbnails.generate_product_image_thumbnails")
    def test_command_dry_run_does_not_call_service(self, generate_mock):
        ProductImage.objects.create(product=self.product, order=0, image="previews/ref-product/a.png")
        call_command("generate_product_image_thumbnails", "--dry-run")
        generate_mock.assert_not_called()


class CatalogListingTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(
            title="Listing",
            slug="listing-product",
            price=Decimal("15.00"),
            is_published=True,
        )
        cls.category = Category.objects.create(title="List cat", slug="list-cat")
        cls.product.categories.add(cls.category)

    def test_catalog_desktop_card_uses_lazy_srcset(self):
        ProductImage.objects.create(
            product=self.product,
            order=0,
            image="previews/listing-product/a.png",
            thumb_400_key="previews/listing-product/a_w400.webp",
            thumb_800_key="previews/listing-product/a_w800.webp",
            thumb_400_display_width=400,
            thumb_800_display_width=800,
            thumbs_source_key="previews/listing-product/a.png",
            width=1200,
            height=800,
        )
        response = self.client.get("/catalog/", {"category": self.category.slug})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('sizes="436px"', content)
        self.assertIn('loading="lazy"', content)
        self.assertIn("400w", content)

    def test_mobile_json_includes_thumb_fields(self):
        response = self.client.get("/catalog/", {"category": self.category.slug})
        self.assertEqual(response.status_code, 200)
        self.assertIn("image_thumb_url", response.content.decode())
