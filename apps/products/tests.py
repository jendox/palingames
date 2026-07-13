import json
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import caches
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.access.models import UserProductAccess
from apps.notifications.destinations import TelegramDestination
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import process_notification_outbox
from apps.notifications.types import NotificationType
from apps.orders.models import Order
from apps.products.models import (
    AgeGroup,
    AgeGroupTag,
    Category,
    Product,
    ProductFile,
    ProductImage,
    Review,
    ReviewStatus,
    SubType,
)
from apps.products.services.s3 import (
    ProductFileDownloadUrlError,
    ProductFileMetadataError,
    ProductFileUploadError,
    ProductFileUploadUrlError,
    _allowed_upload_extensions,
    generate_presigned_download_url,
    generate_presigned_upload_url,
    head_product_file,
    upload_product_file,
    validate_upload_filename,
    verify_uploaded_object,
)
from apps.promocodes.models import PromoCode


class CatalogViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Дидактические игры", slug="didactic-games")
        cls.other_category = Category.objects.create(title="Настольные игры", slug="board-games")

        cls.age_2_3 = AgeGroupTag.objects.create(value=AgeGroup.AGE_2_3)
        cls.age_4_5 = AgeGroupTag.objects.create(value=AgeGroup.AGE_4_5)

        cls.subtype_cards = SubType.objects.create(title="Карточки")
        cls.subtype_sets = SubType.objects.create(title="Наборы")

        cls.alpha = cls._make_product("Альфа", "alpha", Decimal("30.00"), cls.category, cls.subtype_cards, cls.age_2_3)
        cls.beta = cls._make_product("Бета", "beta", Decimal("10.00"), cls.category, cls.subtype_sets, cls.age_4_5)
        cls.gamma = cls._make_product("Гамма", "gamma", Decimal("20.00"), cls.category, cls.subtype_cards, cls.age_4_5)

        cls.foreign_product = Product.objects.create(
            title="Чужой товар",
            slug="foreign-product",
            price=Decimal("99.00"),
        )
        cls.foreign_product.categories.add(cls.other_category)

    @classmethod
    def _make_product(cls, title, slug, price, category, subtype, age_group):
        product = Product.objects.create(
            title=title,
            slug=slug,
            price=price,
        )
        product.categories.add(category)
        product.subtypes.add(subtype)
        product.age_groups.add(age_group)
        return product

    def test_catalog_filters_products_by_selected_category(self):
        response = self.client.get(reverse("catalog"), {"category": self.category.slug})

        self.assertEqual(response.status_code, 200)
        products = response.context["catalog_products"]
        titles = [product["title"] for product in products]

        self.assertCountEqual(titles, ["Альфа", "Бета", "Гамма"])
        self.assertNotIn("Чужой товар", titles)

    @patch("apps.products.views.inc_catalog_page_view")
    def test_catalog_page_view_increments_metric_for_full_page(self, inc_catalog_page_view_mock):
        response = self.client.get(reverse("catalog"))

        self.assertEqual(response.status_code, 200)
        inc_catalog_page_view_mock.assert_called_once_with(user_type="guest")

    def test_catalog_renders_analytics_payload_attributes(self):
        response = self.client.get(reverse("catalog"), {"category": self.category.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-analytics-item")
        self.assertContains(response, 'data-analytics-item-id="')

    def test_catalog_global_search_shows_matching_products(self):
        response = self.client.get(reverse("catalog"), {"q": "Альф"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["catalog_mode"], "products")
        self.assertTrue(response.context["catalog_search_active"])
        titles = [product["title"] for product in response.context["catalog_products"]]
        self.assertIn("Альфа", titles)

    def test_catalog_search_within_category(self):
        response = self.client.get(reverse("catalog"), {"category": self.category.slug, "q": "Бета"})

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["catalog_products"]]
        self.assertEqual(titles, ["Бета"])

    def test_catalog_search_suggest_returns_results(self):
        response = self.client.get(reverse("catalog-search-suggest"), {"q": "Ал"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreater(len(payload["results"]), 0)
        self.assertIn("title", payload["results"][0])
        self.assertIn("url", payload["results"][0])

    def test_catalog_sorts_products_by_price_ascending(self):
        response = self.client.get(
            reverse("catalog"),
            {"category": self.category.slug, "sort": "price_asc"},
        )

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["catalog_products"]]

        self.assertEqual(titles, ["Бета", "Гамма", "Альфа"])

    def test_catalog_filters_products_by_subtype_and_age(self):
        response = self.client.get(
            reverse("catalog"),
            {
                "category": self.category.slug,
                "subtype": str(self.subtype_cards.pk),
                "age": str(self.age_4_5.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["catalog_products"]]

        self.assertEqual(titles, ["Гамма"])

    def test_mobile_htmx_request_returns_mobile_listing_partial(self):
        response = self.client.get(
            reverse("catalog"),
            {"category": self.category.slug, "sort": "price_desc"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="catalog-mobile-listing-root",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('id="catalog-mobile-listing-root"', content)
        self.assertIn('data-catalog-mobile-filter-form', content)
        self.assertIn('name="sort" value="price_desc"', content)
        self.assertIn("Сортировка", content)

    def test_catalog_uses_different_page_sizes_for_desktop_and_mobile(self):
        for index in range(6):
            self._make_product(
                f"Дополнительный {index}",
                f"extra-{index}",
                Decimal(f"{40 + index}.00"),
                self.category,
                self.subtype_cards,
                self.age_2_3,
            )

        response = self.client.get(reverse("catalog"), {"category": self.category.slug})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["catalog_products"]), 9)
        self.assertEqual(len(response.context["catalog_mobile_products"]), 8)

    def test_catalog_marks_purchased_products_for_authenticated_user(self):
        user = get_user_model().objects.create_user(email="catalog-access@example.com", password="test-pass-123")
        order = Order.objects.create(
            user=user,
            email=user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("30.00"),
            total_amount=Decimal("30.00"),
            items_count=1,
        )
        UserProductAccess.objects.create(user=user, product=self.alpha, order=order)
        self.client.force_login(user)

        response = self.client.get(reverse("catalog"), {"category": self.category.slug})

        self.assertEqual(response.status_code, 200)
        products_by_title = {product["title"]: product for product in response.context["catalog_products"]}
        self.assertTrue(products_by_title["Альфа"]["is_purchased"])
        self.assertFalse(products_by_title["Альфа"]["is_in_cart"])
        self.assertEqual(
            products_by_title["Альфа"]["download_url"],
            reverse("product-download", kwargs={"product_id": self.alpha.id}),
        )

    def test_desktop_results_panel_shows_current_page_range(self):
        for index in range(15):
            self._make_product(
                f"Диапазон {index}",
                f"range-{index}",
                Decimal(f"{40 + index}.00"),
                self.category,
                self.subtype_cards,
                self.age_2_3,
            )

        response = self.client.get(reverse("catalog"), {"category": self.category.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1-9 из 18")

    def test_mobile_pagination_uses_compact_page_items_with_ellipsis(self):
        for index in range(22):
            self._make_product(
                f"Пагинация {index}",
                f"pagination-{index}",
                Decimal(f"{40 + index}.00"),
                self.category,
                self.subtype_cards,
                self.age_2_3,
            )

        response = self.client.get(reverse("catalog"), {"category": self.category.slug, "page": 2})

        self.assertEqual(response.status_code, 200)
        pagination_items = response.context["catalog_mobile_pagination_items"]

        self.assertEqual(
            pagination_items,
            [
                {"type": "page", "number": 1, "current": False},
                {"type": "page", "number": 2, "current": True},
                {"type": "page", "number": 3, "current": False},
                {"type": "ellipsis"},
                {"type": "page", "number": 4, "current": False},
            ],
        )


class AlphabetNavigatorViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Дидактические игры", slug="didactic-games")
        cls.subtype = SubType.objects.create(title="Карточки")
        cls.alt_subtype = SubType.objects.create(title="Наборы")
        cls.age = AgeGroupTag.objects.create(value=AgeGroup.AGE_2_3)
        cls.alt_age = AgeGroupTag.objects.create(value=AgeGroup.AGE_4_5)

        cls._make_product("Альфа", "alpha-nav", Decimal("30.00"))
        cls._make_product("Арфа", "harp-nav", Decimal("20.00"))
        cls._make_product("Бета", "beta-nav", Decimal("10.00"))
        cls._make_product("Астра", "astra-nav", Decimal("15.00"), subtype=cls.alt_subtype, age=cls.alt_age)

    @classmethod
    def _make_product(cls, title, slug, price, subtype=None, age=None):
        product = Product.objects.create(title=title, slug=slug, price=price)
        product.categories.add(cls.category)
        product.subtypes.add(subtype or cls.subtype)
        product.age_groups.add(age or cls.age)
        return product

    def test_alphabet_navigator_defaults_to_letter_a(self):
        response = self.client.get(reverse("alphabet-navigator"))

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["alphabet_products"]]

        self.assertCountEqual(titles, ["Альфа", "Арфа", "Астра"])
        self.assertNotIn("Бета", titles)
        self.assertEqual(response.context["alphabet_selected_letter"], "А")

    def test_alphabet_navigator_filters_by_requested_letter(self):
        response = self.client.get(reverse("alphabet-navigator"), {"letter": "Б"})

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["alphabet_products"]]

        self.assertEqual(titles, ["Бета"])

    def test_alphabet_navigator_mobile_htmx_returns_mobile_partial(self):
        response = self.client.get(
            reverse("alphabet-navigator"),
            {"letter": "А", "sort": "price_desc"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="alphabet-mobile-listing-root",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('id="alphabet-mobile-listing-root"', content)
        self.assertIn("Алфавитный навигатор", content)
        self.assertIn("сортировка", content.lower())

    def test_alphabet_navigator_filters_inside_selected_letter(self):
        response = self.client.get(
            reverse("alphabet-navigator"),
            {"letter": "А", "subtype": str(self.alt_subtype.pk), "age": str(self.alt_age.pk)},
        )

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["alphabet_products"]]

        self.assertEqual(titles, ["Астра"])

    def test_alphabet_navigator_uses_catalog_like_htmx_targets(self):
        response = self.client.get(reverse("alphabet-navigator"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('hx-target="#alphabet-desktop-results"', content)
        self.assertIn('hx-target="#alphabet-mobile-listing-root"', content)

    def test_alphabet_navigator_desktop_htmx_returns_results_panel_partial(self):
        response = self.client.get(
            reverse("alphabet-navigator"),
            {"letter": "А", "subtype": str(self.subtype.pk)},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="alphabet-desktop-results",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('id="alphabet-desktop-results"', content)
        self.assertNotIn('id="alphabet-desktop-listing-root"', content)

    def test_alphabet_navigator_desktop_letter_switch_returns_full_listing_partial(self):
        response = self.client.get(
            reverse("alphabet-navigator"),
            {"letter": "Б"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="alphabet-desktop-listing-root",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('id="alphabet-desktop-listing-root"', content)
        self.assertIn("Алфавитный навигатор", content)


@override_settings(SITE_BASE_URL="https://example.com")
class ProductSeoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Дидактические игры", slug="didactic-games-seo")
        cls.product = Product.objects.create(
            title="Математическая игра",
            slug="math-game",
            price=Decimal("25.00"),
            description="Подробное описание игры для занятий дома и в детском саду.",
        )
        cls.product.categories.add(cls.category)

    def test_product_page_renders_dynamic_seo_meta(self):
        response = self.client.get(reverse("product-detail", kwargs={"slug": self.product.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>Математическая игра — PalinGames</title>", html=True)
        self.assertContains(
            response,
            'content="Подробное описание игры для занятий дома и в детском саду."',
            html=False,
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="https://example.com/products/math-game/" />',
            html=True,
        )
        self.assertContains(response, '"@type":"Product"', html=False)

    def test_sitemap_lists_product_urls(self):
        response = self.client.get(reverse("sitemap-xml"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<loc>https://example.com/products/math-game/</loc>", html=False)


class ProductFileModelTests(TestCase):
    def test_product_allows_only_one_active_file(self):
        product = Product.objects.create(title="Архив", slug="archive", price=Decimal("10.00"))
        ProductFile.objects.create(product=product, file_key="products/archive/one.zip", is_active=True)

        with self.assertRaises(IntegrityError):
            ProductFile.objects.create(product=product, file_key="products/archive/two.zip", is_active=True)

    def test_product_can_have_inactive_files(self):
        product = Product.objects.create(title="Архив 2", slug="archive-2", price=Decimal("10.00"))
        ProductFile.objects.create(product=product, file_key="products/archive-2/one.zip", is_active=True)
        ProductFile.objects.create(product=product, file_key="products/archive-2/old.zip", is_active=False)

        self.assertEqual(ProductFile.objects.filter(product=product).count(), 2)


class ProductS3ServiceTests(TestCase):
    @patch("apps.products.services.s3.get_s3_client")
    def test_upload_product_file_returns_expected_metadata(self, mock_get_s3_client):
        mock_get_s3_client.return_value = Mock()
        uploaded_file = SimpleUploadedFile(
            "game.zip",
            b"archive-content",
            content_type="application/zip",
        )

        result = upload_product_file(product_slug="alpha", uploaded_file=uploaded_file)

        self.assertTrue(result["file_key"].startswith("alpha/"))
        self.assertTrue(result["file_key"].endswith(".zip"))
        self.assertEqual(result["original_filename"], "game.zip")
        self.assertEqual(result["mime_type"], "application/zip")
        self.assertEqual(result["size_bytes"], len(b"archive-content"))
        self.assertEqual(len(result["checksum_sha256"]), 64)
        mock_get_s3_client.return_value.upload_fileobj.assert_called_once()

    @patch("apps.products.services.s3.resolve_storage_unavailable_incident")
    @patch("apps.products.services.s3.get_s3_client")
    def test_generate_presigned_download_url_uses_bucket_and_filename(
        self,
        mock_get_s3_client,
        resolve_storage_unavailable_incident_mock,
    ):
        mock_get_s3_client.return_value.generate_presigned_url.return_value = "https://example.com/download"

        result = generate_presigned_download_url(
            file_key="products/alpha/test.zip",
            original_filename="game.zip",
        )

        self.assertEqual(result, "https://example.com/download")
        mock_get_s3_client.return_value.generate_presigned_url.assert_called_once()
        resolve_storage_unavailable_incident_mock.assert_called_once_with(
            operation="generate_presigned_download_url",
        )

    @patch("apps.products.services.s3.get_s3_client")
    def test_upload_product_file_wraps_storage_errors(self, mock_get_s3_client):
        mock_get_s3_client.return_value.upload_fileobj.side_effect = ValueError("boom")
        uploaded_file = SimpleUploadedFile(
            "game.zip",
            b"archive-content",
            content_type="application/zip",
        )

        with self.assertRaises(ProductFileUploadError):
            upload_product_file(product_slug="alpha", uploaded_file=uploaded_file)

    @patch("apps.products.services.s3.get_s3_client")
    def test_head_product_file_wraps_storage_errors(self, mock_get_s3_client):
        mock_get_s3_client.return_value.head_object.side_effect = ValueError("boom")

        with self.assertRaises(ProductFileMetadataError):
            head_product_file(file_key="alpha/test.zip")

    @patch("apps.products.services.s3.record_storage_unavailable_incident")
    @patch("apps.products.services.s3.get_s3_client")
    def test_generate_presigned_download_url_wraps_storage_errors(
        self,
        mock_get_s3_client,
        record_storage_unavailable_incident_mock,
    ):
        mock_get_s3_client.return_value.generate_presigned_url.side_effect = ValueError("boom")

        with self.assertRaises(ProductFileDownloadUrlError):
            generate_presigned_download_url(
                file_key="alpha/test.zip",
                original_filename="game.zip",
            )
        record_storage_unavailable_incident_mock.assert_called_once()


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
class AdminDirectUploadTests(TestCase):
    def setUp(self):
        caches["default"].clear()
        _allowed_upload_extensions.cache_clear()
        self.product = Product.objects.create(title="Архив", slug="archive", price=Decimal("10.00"))
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
        response = self.client.get(reverse("admin:products_productfile_add"))
        self.assertEqual(response.status_code, 200)
        return self.client.cookies["csrftoken"].value

    def _presign_payload(self, **overrides):
        payload = {
            "product_id": self.product.id,
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
            reverse("admin-product-file-presign"),
            data=json.dumps(self._presign_payload()),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertIn("intent_id", payload)
        self.assertTrue(payload["file_key"].startswith("archive/"))
        self.assertEqual(payload["upload_url"], "https://storage.example/upload")
        self.assertEqual(payload["required_headers"], {"Content-Type": "application/zip"})
        self.assertEqual(payload["expires_in"], 900)

    def test_presign_requires_staff(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("home"))
        token = client.cookies["csrftoken"].value

        response = client.post(
            reverse("admin-product-file-presign"),
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
            reverse("admin-product-file-presign"),
            data=json.dumps(self._presign_payload()),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.content.decode()), {"error": "feature_disabled"})

    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_presign_rejects_oversized_file(self, mock_generate_presigned_upload_url):
        token = self._csrf_token()

        response = self.client.post(
            reverse("admin-product-file-presign"),
            data=json.dumps(
                self._presign_payload(
                    size_bytes=ADMIN_DIRECT_S3_SETTINGS["ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES"] + 1,
                ),
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 400)
        mock_generate_presigned_upload_url.assert_not_called()

    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_presign_rejects_bad_extension(self, mock_generate_presigned_upload_url):
        token = self._csrf_token()

        response = self.client.post(
            reverse("admin-product-file-presign"),
            data=json.dumps(self._presign_payload(filename="game.exe")),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 400)
        mock_generate_presigned_upload_url.assert_not_called()

    @patch("apps.products.admin_upload_views.verify_uploaded_object")
    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_happy_path_creates_product_file(
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

        presign_response = self.client.post(
            reverse("admin-product-file-presign"),
            data=json.dumps(self._presign_payload()),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        presign_payload = json.loads(presign_response.content.decode())

        finalize_response = self.client.post(
            reverse("admin-product-file-finalize"),
            data=json.dumps(
                {
                    "intent_id": presign_payload["intent_id"],
                    "product_id": self.product.id,
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
        self.assertEqual(ProductFile.objects.count(), 1)

        product_file = ProductFile.objects.get()
        self.assertEqual(product_file.product_id, self.product.id)
        self.assertEqual(product_file.file_key, presign_payload["file_key"])
        self.assertEqual(product_file.original_filename, "game.zip")
        self.assertEqual(product_file.size_bytes, 128)
        self.assertTrue(product_file.is_active)
        self.assertEqual(
            finalize_payload["redirect_url"],
            reverse("admin:products_productfile_change", args=[product_file.pk]),
        )

    @patch("apps.products.admin_upload_views.verify_uploaded_object")
    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_rejects_consumed_intent(
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
                reverse("admin-product-file-presign"),
                data=json.dumps(self._presign_payload()),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            ).content.decode(),
        )
        finalize_body = {
            "intent_id": presign_payload["intent_id"],
            "product_id": self.product.id,
            "file_key": presign_payload["file_key"],
            "original_filename": "game.zip",
            "mime_type": "application/zip",
            "size_bytes": 128,
            "is_active": True,
        }

        first_response = self.client.post(
            reverse("admin-product-file-finalize"),
            data=json.dumps(finalize_body),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.post(
            reverse("admin-product-file-finalize"),
            data=json.dumps(finalize_body),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(second_response.status_code, 400)

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
                reverse("admin-product-file-presign"),
                data=json.dumps(self._presign_payload()),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            ).content.decode(),
        )

        other_client = Client(enforce_csrf_checks=True)
        other_client.force_login(self.other_staff)
        other_client.get(reverse("admin:products_productfile_add"))
        other_token = other_client.cookies["csrftoken"].value

        response = other_client.post(
            reverse("admin-product-file-finalize"),
            data=json.dumps(
                {
                    "intent_id": presign_payload["intent_id"],
                    "product_id": self.product.id,
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
        self.assertEqual(ProductFile.objects.count(), 0)

        staff_finalize = self.client.post(
            reverse("admin-product-file-finalize"),
            data=json.dumps(
                {
                    "intent_id": presign_payload["intent_id"],
                    "product_id": self.product.id,
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

    @patch("apps.products.admin_upload_views.verify_uploaded_object", side_effect=ProductFileMetadataError("missing"))
    @patch("apps.products.admin_upload_views.generate_presigned_upload_url")
    def test_finalize_rejects_missing_s3_object(
        self,
        mock_generate_presigned_upload_url,
        _mock_verify_uploaded_object,
    ):
        mock_generate_presigned_upload_url.return_value = {
            "upload_url": "https://storage.example/upload",
            "required_headers": {"Content-Type": "application/zip"},
            "expires_in": 900,
        }
        token = self._csrf_token()
        presign_payload = json.loads(
            self.client.post(
                reverse("admin-product-file-presign"),
                data=json.dumps(self._presign_payload()),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            ).content.decode(),
        )

        response = self.client.post(
            reverse("admin-product-file-finalize"),
            data=json.dumps(
                {
                    "intent_id": presign_payload["intent_id"],
                    "product_id": self.product.id,
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

        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content.decode()), {"error": "uploaded_file_not_found"})
        self.assertEqual(ProductFile.objects.count(), 0)

    def test_product_file_admin_includes_direct_upload_assets_when_enabled(self):
        response = self.client.get(reverse("admin:products_productfile_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin-product-file-upload.js")
        self.assertContains(response, "product-file-direct-upload-config")

    @override_settings(ADMIN_DIRECT_S3_UPLOAD_ENABLED=False)
    def test_product_file_admin_hides_direct_upload_assets_when_disabled(self):
        response = self.client.get(reverse("admin:products_productfile_add"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "admin-product-file-upload.js")
        self.assertNotContains(response, "product-file-direct-upload-config")

    @patch("apps.products.forms.validate_storage_bucket_access")
    @patch("apps.products.admin.upload_product_file")
    def test_save_model_uses_server_side_upload_when_feature_flag_disabled(
        self,
        mock_upload_product_file,
        _mock_validate_storage_bucket_access,
    ):
        mock_upload_product_file.return_value = {
            "file_key": "archive/server.zip",
            "original_filename": "game.zip",
            "mime_type": "application/zip",
            "size_bytes": 12,
            "checksum_sha256": "a" * 64,
        }
        admin_user = self.staff_user
        self.client.force_login(admin_user)

        with override_settings(ADMIN_DIRECT_S3_UPLOAD_ENABLED=False):
            add_response = self.client.get(reverse("admin:products_productfile_add"))
            csrf_token = add_response.cookies["csrftoken"].value
            response = self.client.post(
                reverse("admin:products_productfile_add"),
                {
                    "csrfmiddlewaretoken": csrf_token,
                    "product": str(self.product.id),
                    "is_active": "on",
                    "upload": SimpleUploadedFile("game.zip", b"zip-content", content_type="application/zip"),
                },
            )

        self.assertEqual(response.status_code, 302)
        mock_upload_product_file.assert_called_once()
        product_file = ProductFile.objects.get()
        self.assertEqual(product_file.file_key, "archive/server.zip")


class ProductDirectS3ServiceTests(TestCase):
    def setUp(self):
        _allowed_upload_extensions.cache_clear()

    @override_settings(ADMIN_DIRECT_S3_UPLOAD_ALLOWED_EXTENSIONS=".zip,.pdf")
    def test_validate_upload_filename_accepts_whitelisted_extension(self):
        _allowed_upload_extensions.cache_clear()
        self.assertEqual(validate_upload_filename("folder/game.ZIP"), "game.ZIP")

    @override_settings(ADMIN_DIRECT_S3_UPLOAD_ALLOWED_EXTENSIONS=".zip,.pdf")
    def test_validate_upload_filename_rejects_unknown_extension(self):
        _allowed_upload_extensions.cache_clear()
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            validate_upload_filename("game.exe")

    @patch("apps.products.services.s3.resolve_storage_unavailable_incident")
    @patch("apps.products.services.s3.get_s3_client")
    def test_generate_presigned_upload_url_returns_required_headers(
        self,
        mock_get_s3_client,
        resolve_storage_unavailable_incident_mock,
    ):
        mock_get_s3_client.return_value.generate_presigned_url.return_value = "https://example.com/upload"

        result = generate_presigned_upload_url(
            file_key="archive/test.zip",
            content_type="application/zip",
        )

        self.assertEqual(result["upload_url"], "https://example.com/upload")
        self.assertEqual(result["required_headers"], {"Content-Type": "application/zip"})
        self.assertEqual(result["expires_in"], settings.ADMIN_DIRECT_S3_UPLOAD_PRESIGN_TTL_SECONDS)
        resolve_storage_unavailable_incident_mock.assert_called_once_with(
            operation="generate_presigned_upload_url",
        )

    @patch("apps.products.services.s3.get_s3_client")
    def test_generate_presigned_upload_url_wraps_storage_errors(self, mock_get_s3_client):
        mock_get_s3_client.return_value.generate_presigned_url.side_effect = ValueError("boom")

        with self.assertRaises(ProductFileUploadUrlError):
            generate_presigned_upload_url(
                file_key="archive/test.zip",
                content_type="application/zip",
            )

    @patch("apps.products.services.s3.head_product_file")
    def test_verify_uploaded_object_checks_size_and_content_type(self, mock_head_product_file):
        mock_head_product_file.return_value = {
            "ContentLength": 128,
            "ContentType": "application/zip",
        }

        metadata = verify_uploaded_object(
            file_key="archive/test.zip",
            expected_size=128,
            content_type="application/zip",
        )

        self.assertEqual(metadata["ContentLength"], 128)

    @patch("apps.products.services.s3.head_product_file")
    def test_verify_uploaded_object_rejects_size_mismatch(self, mock_head_product_file):
        from django.core.exceptions import ValidationError

        mock_head_product_file.return_value = {
            "ContentLength": 64,
            "ContentType": "application/zip",
        }

        with self.assertRaises(ValidationError):
            verify_uploaded_object(
                file_key="archive/test.zip",
                expected_size=128,
                content_type="application/zip",
            )

    @override_settings(S3_PRODUCT_IMAGES_PREFIX="previews")
    def test_verify_uploaded_object_rejects_preview_prefix(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            verify_uploaded_object(
                file_key="previews/image.jpg",
                expected_size=128,
                content_type="image/jpeg",
            )


PRODUCT_IMAGE_S3_SETTINGS = {
    "S3_PRODUCT_IMAGES_ENABLED": True,
    "S3_PRODUCT_IMAGES_PREFIX": "previews",
    "S3_PRODUCT_IMAGES_PUBLIC_BASE_URL": "",
    "S3_ENDPOINT_URL": "http://127.0.0.1:9000",
    "S3_BUCKET_NAME": "products",
    "S3_ADDRESSING_STYLE": "path",
}


def mock_product_image_s3_client() -> Mock:
    from botocore.exceptions import ClientError

    client = Mock()
    error_response = {"Error": {"Code": "404"}}
    client.head_object.side_effect = ClientError(error_response, "HeadObject")
    return client


@override_settings(**PRODUCT_IMAGE_S3_SETTINGS)
class ProductImageStorageTests(TestCase):
    @patch("apps.products.storage.get_s3_client")
    def test_save_uploads_with_content_type_and_cache_control(self, mock_get_s3_client):
        from apps.products.storage import ProductImageS3Storage

        mock_get_s3_client.return_value = Mock()
        storage = ProductImageS3Storage()
        content = SimpleUploadedFile("photo.png", b"png-bytes", content_type="image/png")
        key = "previews/alpha/abc123.png"

        saved_name = storage._save(key, content)

        self.assertEqual(saved_name, key)
        mock_get_s3_client.return_value.upload_fileobj.assert_called_once_with(
            Fileobj=content,
            Bucket="products",
            Key=key,
            ExtraArgs={
                "ContentType": "image/png",
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )

    @patch("apps.products.storage.get_s3_client")
    def test_delete_removes_object_from_bucket(self, mock_get_s3_client):
        from apps.products.storage import ProductImageS3Storage

        mock_get_s3_client.return_value = Mock()
        storage = ProductImageS3Storage()

        storage.delete("previews/alpha/abc123.png")

        mock_get_s3_client.return_value.delete_object.assert_called_once_with(
            Bucket="products",
            Key="previews/alpha/abc123.png",
        )

    @patch("apps.products.storage.get_s3_client")
    def test_exists_returns_false_for_missing_object(self, mock_get_s3_client):
        from botocore.exceptions import ClientError

        from apps.products.storage import ProductImageS3Storage

        error_response = {"Error": {"Code": "404"}}
        mock_get_s3_client.return_value.head_object.side_effect = ClientError(error_response, "HeadObject")
        storage = ProductImageS3Storage()

        self.assertFalse(storage.exists("previews/alpha/missing.png"))

    def test_url_builds_path_style_public_url(self):
        from apps.products.storage import ProductImageS3Storage, build_product_image_public_url

        url = build_product_image_public_url("previews/slug/file.png")
        self.assertEqual(url, "http://127.0.0.1:9000/products/previews/slug/file.png")
        self.assertEqual(
            ProductImageS3Storage().url("previews/slug/file.png"),
            "http://127.0.0.1:9000/products/previews/slug/file.png",
        )

    @override_settings(S3_PRODUCT_IMAGES_PUBLIC_BASE_URL="https://cdn.example.com/assets")
    def test_url_uses_explicit_public_base_url(self):
        from apps.products.storage import build_product_image_public_url

        url = build_product_image_public_url("previews/slug/file.png")
        self.assertEqual(url, "https://cdn.example.com/assets/previews/slug/file.png")

    def test_build_product_image_object_key(self):
        from apps.products.storage import build_product_image_object_key

        with patch("apps.products.storage.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "deadbeefcafebabe"
            key = build_product_image_object_key(product_slug="my-slug", filename="photo.PNG")

        self.assertEqual(key, "previews/my-slug/deadbeefcafebabe.png")

    @override_settings(S3_PRODUCT_IMAGES_ENABLED=False)
    def test_get_product_image_storage_returns_filesystem_when_disabled(self):
        from django.core.files.storage import FileSystemStorage

        from apps.products.storage import get_product_image_storage

        self.assertIsInstance(get_product_image_storage(), FileSystemStorage)

    @override_settings(S3_PRODUCT_IMAGES_ENABLED=True)
    def test_get_product_image_storage_returns_s3_when_enabled(self):
        from apps.products.storage import ProductImageS3Storage, get_product_image_storage

        self.assertIsInstance(get_product_image_storage(), ProductImageS3Storage)


@override_settings(**PRODUCT_IMAGE_S3_SETTINGS)
class ProductImageSignalTests(TestCase):
    @patch("apps.products.storage.get_s3_client")
    def test_replace_deletes_previous_object_key(self, mock_get_s3_client):
        mock_get_s3_client.return_value = mock_product_image_s3_client()
        product = Product.objects.create(title="Signal", slug="signal", price=Decimal("10.00"))
        image = ProductImage(product=product, order=0)
        image.image = SimpleUploadedFile("first.png", b"first", content_type="image/png")
        image.save()
        old_key = image.image.name

        image.image = SimpleUploadedFile("second.png", b"second", content_type="image/png")
        image.save()

        deleted_keys = [
            call.kwargs["Key"]
            for call in mock_get_s3_client.return_value.delete_object.call_args_list
        ]
        self.assertIn(old_key, deleted_keys)
        self.assertNotEqual(image.image.name, old_key)

    @patch("apps.products.storage.get_s3_client")
    def test_row_delete_removes_object_from_storage(self, mock_get_s3_client):
        mock_get_s3_client.return_value = mock_product_image_s3_client()
        product = Product.objects.create(title="Delete", slug="delete-me", price=Decimal("10.00"))
        image = ProductImage(product=product, order=0)
        image.image = SimpleUploadedFile("remove.png", b"remove", content_type="image/png")
        image.save()
        object_key = image.image.name

        image.delete()

        deleted_keys = [
            call.kwargs["Key"]
            for call in mock_get_s3_client.return_value.delete_object.call_args_list
        ]
        self.assertIn(object_key, deleted_keys)
        self.assertFalse(ProductImage.objects.filter(pk=image.pk).exists())


class ProductFileSignalTests(TestCase):
    @patch("apps.products.services.s3.get_s3_client")
    def test_row_delete_removes_object_from_storage(self, mock_get_s3_client):
        mock_get_s3_client.return_value = Mock()
        product = Product.objects.create(title="File delete", slug="file-delete", price=Decimal("10.00"))
        product_file = ProductFile.objects.create(
            product=product,
            file_key="file-delete/archive.zip",
            original_filename="archive.zip",
            size_bytes=128,
            is_active=True,
        )

        product_file.delete()

        mock_get_s3_client.return_value.delete_object.assert_called_once_with(
            Bucket=settings.S3_BUCKET_NAME,
            Key="file-delete/archive.zip",
        )
        self.assertFalse(ProductFile.objects.filter(pk=product_file.pk).exists())

    @patch("apps.products.services.s3.get_s3_client")
    def test_product_delete_cascades_file_storage_delete(self, mock_get_s3_client):
        mock_get_s3_client.return_value = Mock()
        product = Product.objects.create(title="Cascade", slug="cascade-delete", price=Decimal("10.00"))
        ProductFile.objects.create(
            product=product,
            file_key="cascade-delete/active.zip",
            original_filename="active.zip",
            is_active=True,
        )
        ProductFile.objects.create(
            product=product,
            file_key="cascade-delete/old.zip",
            original_filename="old.zip",
            is_active=False,
        )

        product.delete()

        deleted_keys = [
            call.kwargs["Key"]
            for call in mock_get_s3_client.return_value.delete_object.call_args_list
        ]
        self.assertEqual(
            sorted(deleted_keys),
            ["cascade-delete/active.zip", "cascade-delete/old.zip"],
        )
        self.assertFalse(ProductFile.objects.filter(product_id=product.pk).exists())

    @patch("apps.products.services.s3.get_s3_client")
    @override_settings(S3_PRODUCT_IMAGES_PREFIX="previews")
    def test_row_delete_skips_preview_prefix_keys(self, mock_get_s3_client):
        mock_get_s3_client.return_value = Mock()
        product = Product.objects.create(title="Preview guard", slug="preview-guard", price=Decimal("10.00"))
        product_file = ProductFile.objects.create(
            product=product,
            file_key="previews/legacy-mistake.zip",
            is_active=True,
        )

        product_file.delete()

        mock_get_s3_client.return_value.delete_object.assert_not_called()
        self.assertFalse(ProductFile.objects.filter(pk=product_file.pk).exists())


@override_settings(**PRODUCT_IMAGE_S3_SETTINGS)
class MigrateProductImagesCommandTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="product-image-migration-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def _create_local_product_image(self, *, slug: str, old_key: str, content: bytes = b"png-data"):
        local_path = Path(settings.MEDIA_ROOT) / old_key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        product = Product.objects.create(title=f"Product {slug}", slug=slug, price=Decimal("10.00"))
        image = ProductImage.objects.create(product=product, order=0, image=old_key)
        return product, image, local_path

    @patch("apps.products.storage.get_s3_client")
    def test_migrates_local_file_to_previews_prefix(self, mock_get_s3_client):
        mock_get_s3_client.return_value = mock_product_image_s3_client()
        _, image, local_path = self._create_local_product_image(
            slug="migrate-me",
            old_key="products/migrate-me/photo.png",
        )

        with patch("apps.products.storage.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "deadbeefcafebabe"
            call_command("migrate_product_images_to_s3", verbosity=0)

        image.refresh_from_db()
        self.assertEqual(image.image.name, "previews/migrate-me/deadbeefcafebabe.png")
        self.assertFalse(local_path.exists())
        mock_get_s3_client.return_value.upload_fileobj.assert_called_once()
        mock_get_s3_client.return_value.delete_object.assert_called_once_with(
            Bucket="products",
            Key="products/migrate-me/photo.png",
        )

    @patch("apps.products.storage.get_s3_client")
    def test_dry_run_does_not_upload_or_update(self, mock_get_s3_client):
        mock_get_s3_client.return_value = mock_product_image_s3_client()
        _, image, local_path = self._create_local_product_image(
            slug="dry-run",
            old_key="products/dry-run/photo.png",
        )

        call_command("migrate_product_images_to_s3", "--dry-run", verbosity=0)

        image.refresh_from_db()
        self.assertEqual(image.image.name, "products/dry-run/photo.png")
        self.assertTrue(local_path.exists())
        mock_get_s3_client.return_value.upload_fileobj.assert_not_called()
        mock_get_s3_client.return_value.delete_object.assert_not_called()

    @patch("apps.products.storage.get_s3_client")
    def test_skips_already_migrated_image(self, mock_get_s3_client):
        mock_get_s3_client.return_value = mock_product_image_s3_client()
        product = Product.objects.create(title="Done", slug="done", price=Decimal("10.00"))
        image = ProductImage.objects.create(
            product=product,
            order=0,
            image="previews/done/already.png",
        )

        call_command("migrate_product_images_to_s3", verbosity=0)

        image.refresh_from_db()
        self.assertEqual(image.image.name, "previews/done/already.png")
        mock_get_s3_client.return_value.upload_fileobj.assert_not_called()

    @patch("apps.products.storage.get_s3_client")
    def test_filters_by_product_slug(self, mock_get_s3_client):
        mock_get_s3_client.return_value = mock_product_image_s3_client()
        self._create_local_product_image(slug="alpha", old_key="products/alpha/one.png")
        self._create_local_product_image(slug="beta", old_key="products/beta/two.png")

        with patch("apps.products.storage.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "abc123456789abcd"
            call_command("migrate_product_images_to_s3", "--product-slug=alpha", verbosity=0)

        self.assertEqual(
            ProductImage.objects.filter(product__slug="alpha").get().image.name,
            "previews/alpha/abc123456789abcd.png",
        )
        self.assertEqual(
            ProductImage.objects.filter(product__slug="beta").get().image.name,
            "products/beta/two.png",
        )
        self.assertEqual(mock_get_s3_client.return_value.upload_fileobj.call_count, 1)

    @patch("apps.products.storage.get_s3_client")
    def test_limit_processes_only_requested_number(self, mock_get_s3_client):
        mock_get_s3_client.return_value = mock_product_image_s3_client()
        self._create_local_product_image(slug="one", old_key="products/one/1.png")
        self._create_local_product_image(slug="two", old_key="products/two/2.png")

        call_command("migrate_product_images_to_s3", "--limit=1", verbosity=0)

        migrated_count = ProductImage.objects.filter(image__startswith="previews/").count()
        self.assertEqual(migrated_count, 1)
        self.assertEqual(mock_get_s3_client.return_value.upload_fileobj.call_count, 1)

    @patch("apps.products.storage.get_s3_client")
    def test_migrates_shared_legacy_key_for_multiple_products(self, mock_get_s3_client):
        mock_client = mock_product_image_s3_client()
        mock_get_s3_client.return_value = mock_client
        shared_key = "products/shared/photo.png"
        shared_path = Path(settings.MEDIA_ROOT) / shared_key
        shared_path.parent.mkdir(parents=True, exist_ok=True)
        shared_path.write_bytes(b"shared-image")

        product_a = Product.objects.create(title="A", slug="product-a", price=Decimal("10.00"))
        product_b = Product.objects.create(title="B", slug="product-b", price=Decimal("10.00"))
        image_a = ProductImage.objects.create(product=product_a, order=0, image=shared_key)
        image_b = ProductImage.objects.create(product=product_b, order=0, image=shared_key)

        with patch("apps.products.storage.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "abc123456789abcd"
            call_command("migrate_product_images_to_s3", verbosity=0)

        image_a.refresh_from_db()
        image_b.refresh_from_db()
        self.assertEqual(image_a.image.name, "previews/product-a/abc123456789abcd.png")
        self.assertEqual(image_b.image.name, "previews/product-b/abc123456789abcd.png")
        self.assertFalse(shared_path.exists())
        self.assertEqual(mock_client.upload_fileobj.call_count, 2)
        mock_client.delete_object.assert_called_once_with(
            Bucket="products",
            Key=shared_key,
        )

    @patch("apps.products.storage.get_s3_client")
    def test_reuses_migrated_peer_when_legacy_source_missing(self, mock_get_s3_client):
        from botocore.exceptions import ClientError

        mock_client = mock_product_image_s3_client()
        mock_get_s3_client.return_value = mock_client
        shared_key = "products/shared/photo.png"

        product_a = Product.objects.create(title="A", slug="product-a", price=Decimal("10.00"))
        product_b = Product.objects.create(title="B", slug="product-b", price=Decimal("10.00"))
        ProductImage.objects.create(
            product=product_a,
            order=0,
            image="previews/product-a/already-migrated.png",
        )
        image_b = ProductImage.objects.create(product=product_b, order=0, image=shared_key)

        def get_object_side_effect(*, Bucket, Key, **kwargs):
            if Key == shared_key:
                raise ClientError({"Error": {"Code": "404"}}, "GetObject")
            if Key == "previews/product-a/already-migrated.png":
                return {"Body": SimpleNamespace(read=lambda: b"peer-image-bytes")}
            raise ClientError({"Error": {"Code": "404"}}, "GetObject")

        mock_client.get_object.side_effect = get_object_side_effect

        with patch("apps.products.storage.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "def987654321fedc"
            call_command("migrate_product_images_to_s3", verbosity=0)

        image_b.refresh_from_db()
        self.assertEqual(image_b.image.name, "previews/product-b/def987654321fedc.png")
        self.assertEqual(mock_client.upload_fileobj.call_count, 1)
        mock_client.get_object.assert_any_call(
            Bucket="products",
            Key="previews/product-a/already-migrated.png",
        )

    @override_settings(S3_PRODUCT_IMAGES_ENABLED=False)
    def test_command_requires_s3_enabled(self):
        with self.assertRaisesMessage(CommandError, "S3_PRODUCT_IMAGES_ENABLED must be true"):
            call_command("migrate_product_images_to_s3", verbosity=0)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "products-test-default",
        },
        "rate_limit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "products-test-rate-limit",
        },
    },
)
class ProductDownloadViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="download@example.com", password="test-pass-123")
        cls.product = Product.objects.create(title="Скачивание", slug="download-product", price=Decimal("25.00"))
        cls.active_file = ProductFile.objects.create(
            product=cls.product,
            file_key="download-product/archive.zip",
            original_filename="archive.zip",
            mime_type="application/zip",
            is_active=True,
        )
        cls.order = Order.objects.create(
            user=cls.user,
            email=cls.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        UserProductAccess.objects.create(user=cls.user, product=cls.product, order=cls.order)

    def setUp(self):
        caches["rate_limit"].clear()

    @patch("apps.products.views.resolve_download_delivery_failure_incident")
    @patch("apps.products.views.generate_presigned_download_url")
    def test_download_returns_presigned_url_json(
        self,
        mock_generate_presigned_download_url,
        resolve_download_delivery_failure_incident_mock,
    ):
        mock_generate_presigned_download_url.return_value = "https://example.com/download"
        self.client.force_login(self.user)

        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"download_url": "https://example.com/download"})
        mock_generate_presigned_download_url.assert_called_once_with(
            file_key=self.active_file.file_key,
            original_filename=self.active_file.original_filename,
        )
        resolve_download_delivery_failure_incident_mock.assert_called_once_with(
            delivery_type="product",
            reason="download_unavailable",
        )

    def test_download_returns_404_without_access(self):
        other_user = get_user_model().objects.create_user(email="other@example.com", password="test-pass-123")
        self.client.force_login(other_user)

        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_download_returns_404_without_active_file(self):
        self.active_file.is_active = False
        self.active_file.save(update_fields=["is_active", "updated_at"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    @patch("apps.products.views.record_download_delivery_failure_incident")
    @patch("apps.products.views.generate_presigned_download_url")
    def test_download_returns_503_when_presigned_url_generation_fails(
        self,
        mock_generate_presigned_download_url,
        record_download_delivery_failure_incident_mock,
    ):
        mock_generate_presigned_download_url.side_effect = ProductFileDownloadUrlError("boom")
        self.client.force_login(self.user)

        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "download_unavailable")
        record_download_delivery_failure_incident_mock.assert_called_once()

    def test_download_redirects_anonymous_user_to_login(self):
        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/?dialog=login", response["Location"])

    @override_settings(
        PRODUCT_DOWNLOAD_USER_RATE_LIMIT=1,
        PRODUCT_DOWNLOAD_USER_RATE_LIMIT_WINDOW_SECONDS=600,
        PRODUCT_DOWNLOAD_PRODUCT_RATE_LIMIT=100,
        PRODUCT_DOWNLOAD_PRODUCT_RATE_LIMIT_WINDOW_SECONDS=600,
    )
    @patch("apps.products.views.generate_presigned_download_url")
    def test_download_enforces_user_rate_limit(self, mock_generate_presigned_download_url):
        mock_generate_presigned_download_url.return_value = "https://example.com/download"
        self.client.force_login(self.user)
        url = reverse("product-download", kwargs={"product_id": self.product.id})

        first_response = self.client.get(url)
        second_response = self.client.get(url)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json(), {"download_url": "https://example.com/download"})
        self.assertEqual(second_response.status_code, 429)
        self.assertEqual(second_response["Retry-After"], "600")
        self.assertEqual(
            second_response.json(),
            {
                "code": "rate_limited",
                "message": "Слишком много запросов на скачивание.",
                "retry_after_seconds": 600,
            },
        )
        mock_generate_presigned_download_url.assert_called_once()

    @override_settings(
        PRODUCT_DOWNLOAD_USER_RATE_LIMIT=100,
        PRODUCT_DOWNLOAD_USER_RATE_LIMIT_WINDOW_SECONDS=600,
        PRODUCT_DOWNLOAD_PRODUCT_RATE_LIMIT=1,
        PRODUCT_DOWNLOAD_PRODUCT_RATE_LIMIT_WINDOW_SECONDS=300,
    )
    @patch("apps.products.views.generate_presigned_download_url")
    def test_download_enforces_product_rate_limit(self, mock_generate_presigned_download_url):
        mock_generate_presigned_download_url.return_value = "https://example.com/download"
        self.client.force_login(self.user)
        url = reverse("product-download", kwargs={"product_id": self.product.id})

        first_response = self.client.get(url)
        second_response = self.client.get(url)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 429)
        self.assertEqual(second_response["Retry-After"], "300")
        self.assertEqual(second_response.json()["code"], "rate_limited")
        self.assertEqual(second_response.json()["retry_after_seconds"], 300)
        mock_generate_presigned_download_url.assert_called_once()


class ProductDetailDownloadContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="detail@example.com", password="test-pass-123")
        cls.product = Product.objects.create(title="Деталь", slug="detail-product", price=Decimal("12.00"))
        order = Order.objects.create(
            user=cls.user,
            email=cls.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("12.00"),
            total_amount=Decimal("12.00"),
            items_count=1,
        )
        UserProductAccess.objects.create(user=cls.user, product=cls.product, order=order)

    def test_product_detail_uses_download_endpoint_for_purchased_product(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("product-detail", kwargs={"slug": self.product.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["product_is_purchased"])
        self.assertEqual(
            response.context["product_download_url"],
            reverse("product-download", kwargs={"product_id": self.product.id}),
        )
        self.assertContains(response, "data-product-download-link")

    @patch("apps.products.views.inc_product_page_view")
    def test_product_detail_increments_metric_for_full_page(self, inc_product_page_view_mock):
        response = self.client.get(reverse("product-detail", kwargs={"slug": self.product.slug}))

        self.assertEqual(response.status_code, 200)
        inc_product_page_view_mock.assert_called_once_with(user_type="guest")

    def test_product_detail_renders_product_analytics_payload(self):
        response = self.client.get(reverse("product-detail", kwargs={"slug": self.product.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="product-analytics-item"')
        self.assertContains(response, 'data-analytics-item-id="')


class ProductReviewFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="rev@example.com", password="secret12345")
        cls.product = Product.objects.create(title="Reviewed", slug="reviewed-game", price=Decimal("10.00"))
        UserProductAccess.objects.create(user=cls.user, product=cls.product, order=None)

    @patch("apps.products.views.schedule_review_submitted_notifications")
    @patch("apps.products.services.reviews.inc_review_submitted")
    def test_submit_review_creates_pending(self, inc_review_submitted_mock, mock_sched):
        self.client.force_login(self.user)
        url = reverse("product-review-submit", kwargs={"slug": self.product.slug})
        response = self.client.post(
            url,
            {"rating": "5", "comment": "Отличная игра для ребёнка, рекомендую."},
        )
        self.assertEqual(response.status_code, 200)
        mock_sched.assert_called_once()
        review = Review.objects.get(product=self.product, user=self.user)
        self.assertEqual(review.status, ReviewStatus.PENDING)
        self.assertEqual(review.rating, 5)
        inc_review_submitted_mock.assert_called_once()
        self.assertEqual(response.headers["HX-Trigger"], "review:submitted")
        self.assertContains(response, "Ваш отзыв принят и появится на сайте после проверки модератором.")
        self.assertNotContains(response, "data-review-form", html=False)

    @patch("apps.products.views.schedule_review_submitted_notifications")
    @patch("apps.products.services.reviews.inc_review_resubmitted")
    def test_resubmit_review_increments_resubmitted_metric(self, inc_review_resubmitted_mock, mock_sched):
        Review.objects.create(
            product=self.product,
            user=self.user,
            rating=3,
            comment="Старая версия отзыва.",
            status=ReviewStatus.REJECTED,
            rejection_reason="Нужно доработать.",
        )
        self.client.force_login(self.user)
        url = reverse("product-review-submit", kwargs={"slug": self.product.slug})

        response = self.client.post(
            url,
            {"rating": "5", "comment": "Новая версия отзыва после правок."},
        )

        self.assertEqual(response.status_code, 200)
        inc_review_resubmitted_mock.assert_called_once()
        mock_sched.assert_called_once()

    def test_submit_without_purchase_forbidden(self):
        other = Product.objects.create(title="Other", slug="other-game", price=Decimal("1.00"))
        self.client.force_login(self.user)
        url = reverse("product-review-submit", kwargs={"slug": other.slug})
        response = self.client.post(
            url,
            {"rating": "4", "comment": "Достаточно длинный текст отзыва для прохождения валидации."},
        )
        self.assertEqual(response.status_code, 403)

    def test_detail_shows_review_form_for_purchaser(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("product-detail", kwargs={"slug": self.product.slug}),
            {"tab": "reviews"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["review_panel"]["show_form"])
        self.assertContains(response, "Пока отзывов нет.", count=2)
        self.assertContains(response, 'hx-target="closest #product-review-panel"', count=2, html=False)

    def test_detail_shows_prefilled_form_without_rejection_hint_for_rejected_review(self):
        Review.objects.create(
            product=self.product,
            user=self.user,
            rating=3,
            comment="Нужно немного переработать и отправить ещё раз.",
            status=ReviewStatus.REJECTED,
            rejection_reason="Причина для письма пользователю.",
        )

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("product-detail", kwargs={"slug": self.product.slug}),
            {"tab": "reviews"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["review_panel"]["show_form"])
        self.assertNotContains(response, "Отзыв не прошёл модерацию")
        self.assertContains(response, "Нужно немного переработать и отправить ещё раз.")

    def test_detail_hides_review_panel_for_published_review(self):
        Review.objects.create(
            product=self.product,
            user=self.user,
            rating=5,
            comment="Уже опубликованный отзыв для проверки витрины.",
            status=ReviewStatus.PUBLISHED,
        )

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("product-detail", kwargs={"slug": self.product.slug}),
            {"tab": "reviews"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["review_panel"]["show_published"])
        self.assertNotContains(response, "Спасибо! Вы уже оставили отзыв к этой игре.")
        self.assertNotContains(response, "Оставить отзыв")

    @override_settings(
        REVIEW_ADMIN_EMAILS=["ops@example.com"],
        SITE_BASE_URL="https://example.com",
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_submit_review_creates_admin_notification_outboxes(self, delay_mock):
        self.client.force_login(self.user)
        url = reverse("product-review-submit", kwargs={"slug": self.product.slug})

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {"rating": "5", "comment": "Отличная игра для ребёнка, рекомендую."},
            )

        self.assertEqual(response.status_code, 200)
        email_outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
            channel=NotificationOutbox.Channel.EMAIL,
        )
        telegram_outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
        )
        self.assertEqual(email_outbox.recipient, "ops@example.com")
        self.assertEqual(email_outbox.status, NotificationOutbox.Status.PENDING)
        self.assertEqual(telegram_outbox.recipient, "notifications")
        self.assertEqual(telegram_outbox.status, NotificationOutbox.Status.PENDING)
        self.assertCountEqual(
            [call.args[0] for call in delay_mock.call_args_list],
            [email_outbox.id, telegram_outbox.id],
        )
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        REVIEW_ADMIN_EMAILS=["ops@example.com"],
        SITE_BASE_URL="https://example.com",
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_FORUM_CHAT_ID="",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=0,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_process_review_submitted_admin_email_notification_sends_email(self, delay_mock):
        self.client.force_login(self.user)
        url = reverse("product-review-submit", kwargs={"slug": self.product.slug})

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {"rating": "5", "comment": "Отличная игра для ребёнка, рекомендую."},
            )

        self.assertEqual(response.status_code, 200)
        delay_mock.assert_called_once()
        outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
            channel=NotificationOutbox.Channel.EMAIL,
        )

        processed = process_notification_outbox(outbox_id=outbox.id)

        self.assertTrue(processed)
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["ops@example.com"])
        self.assertIn("Новый отзыв на", email.subject)
        self.assertIn("Reviewed", email.body)

    @override_settings(
        REVIEW_ADMIN_EMAILS=["ops@example.com"],
        SITE_BASE_URL="https://example.com",
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    @patch("apps.notifications.handlers.send_telegram_message")
    def test_process_review_submitted_admin_telegram_notification_sends_message(
        self,
        send_telegram_message_mock,
        delay_mock,
    ):
        self.client.force_login(self.user)
        url = reverse("product-review-submit", kwargs={"slug": self.product.slug})

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {"rating": "5", "comment": "Отличная игра для ребёнка, рекомендую."},
            )

        self.assertEqual(response.status_code, 200)
        outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
            channel=NotificationOutbox.Channel.TELEGRAM,
        )
        self.assertEqual(delay_mock.call_count, 2)

        processed = process_notification_outbox(outbox_id=outbox.id)

        self.assertTrue(processed)
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        send_telegram_message_mock.assert_called_once()
        self.assertEqual(
            send_telegram_message_mock.call_args.kwargs["destination"],
            TelegramDestination.NOTIFICATIONS,
        )
        self.assertIn("Новый отзыв на товар", send_telegram_message_mock.call_args.kwargs["text"])

    @override_settings(
        REVIEW_ADMIN_EMAILS=[],
        SITE_BASE_URL="https://example.com",
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_FORUM_CHAT_ID="-1001234567890",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=3,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_submit_review_without_admin_recipients_skips_email_outbox(self, delay_mock):
        self.client.force_login(self.user)
        url = reverse("product-review-submit", kwargs={"slug": self.product.slug})

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {"rating": "5", "comment": "Отличная игра для ребёнка, рекомендую."},
            )

        self.assertEqual(response.status_code, 200)
        delay_mock.assert_called_once()
        self.assertFalse(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
                channel=NotificationOutbox.Channel.EMAIL,
            ).exists(),
        )
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
                channel=NotificationOutbox.Channel.TELEGRAM,
            ).exists(),
        )
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        REVIEW_ADMIN_EMAILS=["ops@example.com"],
        SITE_BASE_URL="https://example.com",
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_FORUM_CHAT_ID="",
        TELEGRAM_NOTIFICATIONS_THREAD_ID=0,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    def test_submit_review_without_telegram_settings_skips_telegram_outbox(self, delay_mock):
        self.client.force_login(self.user)
        url = reverse("product-review-submit", kwargs={"slug": self.product.slug})

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {"rating": "5", "comment": "Отличная игра для ребёнка, рекомендую."},
            )

        self.assertEqual(response.status_code, 200)
        delay_mock.assert_called_once()
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
                channel=NotificationOutbox.Channel.EMAIL,
            ).exists(),
        )
        self.assertFalse(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
                channel=NotificationOutbox.Channel.TELEGRAM,
            ).exists(),
        )
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(SITE_BASE_URL="https://example.com")
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    @patch("apps.products.admin.inc_review_rejected")
    def test_admin_rejection_creates_notification_outbox(self, inc_review_rejected_mock, delay_mock):
        admin_user = get_user_model().objects.create_user(
            email="admin@example.com",
            password="secret12345",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            product=self.product,
            user=self.user,
            rating=4,
            comment="Достаточно длинный текст отзыва для прохождения модерации.",
            status=ReviewStatus.PENDING,
        )

        self.client.force_login(admin_user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:products_review_change", args=[review.id]),
                {
                    "product": str(self.product.id),
                    "user": str(self.user.id),
                    "rating": "4",
                    "comment": review.comment,
                    "status": ReviewStatus.REJECTED,
                    "rejection_reason": "Нужно убрать рекламный текст.",
                    "moderated_at_0": "",
                    "moderated_at_1": "",
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, ReviewStatus.REJECTED)
        self.assertEqual(review.rejection_reason, "Нужно убрать рекламный текст.")
        self.assertIsNotNone(review.moderated_at)
        self.assertIsNone(review.rejection_notified_at)
        inc_review_rejected_mock.assert_called_once()
        delay_mock.assert_called_once()
        outbox = NotificationOutbox.objects.get(notification_type=NotificationType.REVIEW_REJECTED_USER)
        self.assertEqual(outbox.recipient, self.user.email)
        self.assertEqual(outbox.status, NotificationOutbox.Status.PENDING)
        self.assertEqual(outbox.object_id, review.id)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(SITE_BASE_URL="https://example.com")
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    @patch("apps.products.admin.inc_review_rejected")
    def test_process_review_rejection_notification_sends_user_email(
        self,
        inc_review_rejected_mock,
        delay_mock,
    ):
        admin_user = get_user_model().objects.create_user(
            email="admin@example.com",
            password="secret12345",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            product=self.product,
            user=self.user,
            rating=4,
            comment="Достаточно длинный текст отзыва для прохождения модерации.",
            status=ReviewStatus.PENDING,
        )

        self.client.force_login(admin_user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:products_review_change", args=[review.id]),
                {
                    "product": str(self.product.id),
                    "user": str(self.user.id),
                    "rating": "4",
                    "comment": review.comment,
                    "status": ReviewStatus.REJECTED,
                    "rejection_reason": "Нужно убрать рекламный текст.",
                    "moderated_at_0": "",
                    "moderated_at_1": "",
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 302)
        inc_review_rejected_mock.assert_called_once()
        delay_mock.assert_called_once()
        outbox = NotificationOutbox.objects.get(notification_type=NotificationType.REVIEW_REJECTED_USER)

        processed = process_notification_outbox(outbox_id=outbox.id)

        self.assertTrue(processed)
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        review.refresh_from_db()
        self.assertIsNotNone(review.rejection_notified_at)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [self.user.email])
        self.assertIn("не прошёл модерацию", email.subject)
        self.assertIn("Нужно убрать рекламный текст.", email.body)

    @override_settings(SITE_BASE_URL="https://example.com")
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    @patch("apps.products.admin.inc_review_rejected")
    def test_admin_rejection_with_empty_user_email_skips_notification_enqueue(
        self,
        inc_review_rejected_mock,
        delay_mock,
    ):
        admin_user = get_user_model().objects.create_user(
            email="admin@example.com",
            password="secret12345",
            is_staff=True,
            is_superuser=True,
        )
        self.user.email = ""
        self.user.save(update_fields=["email"])
        review = Review.objects.create(
            product=self.product,
            user=self.user,
            rating=4,
            comment="Достаточно длинный текст отзыва для прохождения модерации.",
            status=ReviewStatus.PENDING,
        )

        self.client.force_login(admin_user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:products_review_change", args=[review.id]),
                {
                    "product": str(self.product.id),
                    "user": str(self.user.id),
                    "rating": "4",
                    "comment": review.comment,
                    "status": ReviewStatus.REJECTED,
                    "rejection_reason": "Нужно убрать рекламный текст.",
                    "moderated_at_0": "",
                    "moderated_at_1": "",
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, ReviewStatus.REJECTED)
        self.assertIsNone(review.rejection_notified_at)
        inc_review_rejected_mock.assert_called_once()
        delay_mock.assert_not_called()
        self.assertFalse(
            NotificationOutbox.objects.filter(notification_type=NotificationType.REVIEW_REJECTED_USER).exists(),
        )
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        SITE_BASE_URL="https://example.com",
        REVIEW_REWARD_DISCOUNT_PERCENT=10,
        REVIEW_REWARD_VALID_DAYS=14,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    @patch("apps.products.services.review_rewards.inc_review_reward_issued")
    @patch("apps.products.admin.inc_review_published")
    def test_admin_publish_creates_reward_promo_and_notification_outbox(
        self,
        inc_review_published_mock,
        inc_review_reward_issued_mock,
        delay_mock,
    ):
        admin_user = get_user_model().objects.create_user(
            email="admin@example.com",
            password="secret12345",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            product=self.product,
            user=self.user,
            rating=5,
            comment="Достаточно длинный текст отзыва для публикации и награды.",
            status=ReviewStatus.PENDING,
        )

        self.client.force_login(admin_user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:products_review_change", args=[review.id]),
                {
                    "product": str(self.product.id),
                    "user": str(self.user.id),
                    "rating": "5",
                    "comment": review.comment,
                    "status": ReviewStatus.PUBLISHED,
                    "rejection_reason": "",
                    "moderated_at_0": "",
                    "moderated_at_1": "",
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, ReviewStatus.PUBLISHED)
        inc_review_published_mock.assert_called_once()
        inc_review_reward_issued_mock.assert_called_once()
        delay_mock.assert_called_once()
        self.assertIsNotNone(review.moderated_at)
        self.assertIsNotNone(review.reward_promo_code)
        self.assertIsNotNone(review.reward_issued_at)
        self.assertIsNone(review.reward_email_sent_at)

        promo_code = review.reward_promo_code
        self.assertEqual(promo_code.discount_percent, 10)
        self.assertEqual(promo_code.max_total_redemptions, 1)
        self.assertEqual(promo_code.max_redemptions_per_user, 1)
        self.assertEqual(promo_code.max_redemptions_per_email, 1)
        self.assertEqual(promo_code.assigned_user, self.user)
        self.assertEqual(promo_code.assigned_email, self.user.email)
        self.assertEqual((promo_code.ends_at - promo_code.starts_at).days, 14)
        self.assertIn(f"Reward for published review #{review.id}", promo_code.note)

        outbox = NotificationOutbox.objects.get(notification_type=NotificationType.REVIEW_REWARD_USER)
        self.assertEqual(outbox.recipient, self.user.email)
        self.assertEqual(outbox.status, NotificationOutbox.Status.PENDING)
        self.assertEqual(outbox.object_id, review.id)
        self.assertEqual(len(mail.outbox), 0)

        with self.captureOnCommitCallbacks(execute=True):
            response_repeat = self.client.post(
                reverse("admin:products_review_change", args=[review.id]),
                {
                    "product": str(self.product.id),
                    "user": str(self.user.id),
                    "rating": "5",
                    "comment": review.comment,
                    "status": ReviewStatus.PUBLISHED,
                    "rejection_reason": "",
                    "moderated_at_0": review.moderated_at.strftime("%Y-%m-%d"),
                    "moderated_at_1": review.moderated_at.strftime("%H:%M:%S"),
                    "_save": "Save",
                },
            )

        self.assertEqual(response_repeat.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(PromoCode.objects.filter(id=promo_code.id).count(), 1)
        self.assertEqual(PromoCode.objects.count(), 1)
        self.assertEqual(
            NotificationOutbox.objects.filter(notification_type=NotificationType.REVIEW_REWARD_USER).count(),
            1,
        )

    @override_settings(
        SITE_BASE_URL="https://example.com",
        REVIEW_REWARD_DISCOUNT_PERCENT=10,
        REVIEW_REWARD_VALID_DAYS=14,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    @patch("apps.products.services.review_rewards.inc_review_reward_issued")
    @patch("apps.products.admin.inc_review_published")
    def test_process_review_reward_notification_sends_user_email_once(
        self,
        inc_review_published_mock,
        inc_review_reward_issued_mock,
        delay_mock,
    ):
        admin_user = get_user_model().objects.create_user(
            email="admin@example.com",
            password="secret12345",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            product=self.product,
            user=self.user,
            rating=5,
            comment="Достаточно длинный текст отзыва для публикации и награды.",
            status=ReviewStatus.PENDING,
        )

        self.client.force_login(admin_user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:products_review_change", args=[review.id]),
                {
                    "product": str(self.product.id),
                    "user": str(self.user.id),
                    "rating": "5",
                    "comment": review.comment,
                    "status": ReviewStatus.PUBLISHED,
                    "rejection_reason": "",
                    "moderated_at_0": "",
                    "moderated_at_1": "",
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 302)
        inc_review_published_mock.assert_called_once()
        inc_review_reward_issued_mock.assert_called_once()
        delay_mock.assert_called_once()
        outbox = NotificationOutbox.objects.get(notification_type=NotificationType.REVIEW_REWARD_USER)

        processed = process_notification_outbox(outbox_id=outbox.id)

        self.assertTrue(processed)
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)
        review.refresh_from_db()
        self.assertIsNotNone(review.reward_email_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [self.user.email])
        self.assertIn("промокод на 10%", email.subject)
        self.assertIn(review.reward_promo_code.code, email.body)

    @override_settings(
        SITE_BASE_URL="https://example.com",
        REVIEW_REWARD_DISCOUNT_PERCENT=10,
        REVIEW_REWARD_VALID_DAYS=14,
    )
    @patch("apps.notifications.tasks.send_notification_outbox_task.delay")
    @patch("apps.products.services.review_rewards.inc_review_reward_issued")
    @patch("apps.products.admin.inc_review_published")
    def test_admin_publish_with_empty_user_email_skips_reward_notification_enqueue(
        self,
        inc_review_published_mock,
        inc_review_reward_issued_mock,
        delay_mock,
    ):
        admin_user = get_user_model().objects.create_user(
            email="admin@example.com",
            password="secret12345",
            is_staff=True,
            is_superuser=True,
        )
        self.user.email = ""
        self.user.save(update_fields=["email"])
        review = Review.objects.create(
            product=self.product,
            user=self.user,
            rating=5,
            comment="Достаточно длинный текст отзыва для публикации и награды.",
            status=ReviewStatus.PENDING,
        )

        self.client.force_login(admin_user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:products_review_change", args=[review.id]),
                {
                    "product": str(self.product.id),
                    "user": str(self.user.id),
                    "rating": "5",
                    "comment": review.comment,
                    "status": ReviewStatus.PUBLISHED,
                    "rejection_reason": "",
                    "moderated_at_0": "",
                    "moderated_at_1": "",
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, ReviewStatus.PUBLISHED)
        self.assertIsNotNone(review.reward_promo_code)
        self.assertIsNone(review.reward_email_sent_at)
        inc_review_published_mock.assert_called_once()
        inc_review_reward_issued_mock.assert_called_once()
        delay_mock.assert_not_called()
        self.assertFalse(NotificationOutbox.objects.filter(notification_type=NotificationType.REVIEW_REWARD_USER).exists())
        self.assertEqual(len(mail.outbox), 0)
