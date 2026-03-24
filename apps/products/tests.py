from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.access.models import UserProductAccess
from apps.orders.models import Order
from apps.products.models import AgeGroup, AgeGroupTag, Category, Product, ProductFile, SubType
from apps.products.services.s3 import (
    ProductFileDownloadUrlError,
    ProductFileMetadataError,
    ProductFileUploadError,
    generate_presigned_download_url,
    head_product_file,
    upload_product_file,
)


class CatalogViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Дидактические игры", slug="didactic-games")
        cls.other_category = Category.objects.create(title="Настольные игры", slug="board-games")

        cls.age_2_3 = AgeGroupTag.objects.create(value=AgeGroup.AGE_2_3)
        cls.age_4_5 = AgeGroupTag.objects.create(value=AgeGroup.AGE_4_5)

        cls.subtype_cards = SubType.objects.create(title="Карточки", category=cls.category)
        cls.subtype_sets = SubType.objects.create(title="Наборы", category=cls.category)

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
        cls.subtype = SubType.objects.create(title="Карточки", category=cls.category)
        cls.alt_subtype = SubType.objects.create(title="Наборы", category=cls.category)
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

    @patch("apps.products.services.s3.get_s3_client")
    def test_generate_presigned_download_url_uses_bucket_and_filename(self, mock_get_s3_client):
        mock_get_s3_client.return_value.generate_presigned_url.return_value = "https://example.com/download"

        result = generate_presigned_download_url(
            file_key="products/alpha/test.zip",
            original_filename="game.zip",
        )

        self.assertEqual(result, "https://example.com/download")
        mock_get_s3_client.return_value.generate_presigned_url.assert_called_once()

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

    @patch("apps.products.services.s3.get_s3_client")
    def test_generate_presigned_download_url_wraps_storage_errors(self, mock_get_s3_client):
        mock_get_s3_client.return_value.generate_presigned_url.side_effect = ValueError("boom")

        with self.assertRaises(ProductFileDownloadUrlError):
            generate_presigned_download_url(
                file_key="alpha/test.zip",
                original_filename="game.zip",
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

    @patch("apps.products.views.generate_presigned_download_url")
    def test_download_redirects_to_presigned_url(self, mock_generate_presigned_download_url):
        mock_generate_presigned_download_url.return_value = "https://example.com/download"
        self.client.force_login(self.user)

        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/download")
        mock_generate_presigned_download_url.assert_called_once_with(
            file_key=self.active_file.file_key,
            original_filename=self.active_file.original_filename,
        )

    def test_download_returns_404_without_access(self):
        other_user = get_user_model().objects.create_user(email="other@example.com", password="test-pass-123")
        self.client.force_login(other_user)

        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 404)

    def test_download_returns_404_without_active_file(self):
        self.active_file.is_active = False
        self.active_file.save(update_fields=["is_active", "updated_at"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 404)

    def test_download_redirects_anonymous_user_to_login(self):
        response = self.client.get(reverse("product-download", kwargs={"product_id": self.product.id}))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/?dialog=login", response["Location"])


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
