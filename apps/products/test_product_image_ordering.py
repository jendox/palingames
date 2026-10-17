from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.cart.services import SESSION_CART_KEY
from apps.core.seo import build_absolute_url
from apps.favorites.models import Favorite
from apps.favorites.services import SESSION_FAVORITES_KEY
from apps.orders.models import Order
from apps.orders.services import _prepare_order_items
from apps.products.models import Category, Product, ProductImage
from apps.products.tests import create_published_product


@override_settings(S3_PRODUCT_IMAGES_ENABLED=False)
class ProductImageOrderingIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._enqueue_patcher = patch("apps.products.jobs.enqueue_product_image_thumbnails")
        cls._enqueue_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls._enqueue_patcher.stop()
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Категория", slug="ordering-category")
        cls.product = create_published_product(
            title="Ordering product",
            slug="ordering-product",
            price=Decimal("10.00"),
        )
        cls.product.categories.add(cls.category)

    def setUp(self):
        self.client = Client()
        ProductImage.objects.filter(product=self.product).delete()
        self._create_three_images_same_order()

    def _create_three_images_same_order(self):
        self.image_a = self._save_image("a.png", b"image-a")
        self.image_b = self._save_image("b.png", b"image-b")
        self.image_c = self._save_image("c.png", b"image-c")
        self.assertLess(self.image_a.id, self.image_b.id)
        self.assertLess(self.image_b.id, self.image_c.id)

    def _save_image(self, filename: str, content: bytes, *, order: int = 0) -> ProductImage:
        image = ProductImage(product=self.product, order=order)
        image.image = SimpleUploadedFile(filename, content, content_type="image/png")
        image.save()
        return image

    def _primary_url(self) -> str:
        return self.image_a.image.url

    def _gallery_urls(self) -> list[str]:
        return [self.image_a.image.url, self.image_b.image.url, self.image_c.image.url]

    def test_catalog_card_uses_lowest_id_when_orders_tied(self):
        response = self.client.get(reverse("catalog"), {"category": self.category.slug})
        self.assertEqual(response.status_code, 200)
        cards = response.context["catalog_products"]
        card = next(item for item in cards if item["id"] == self.product.id)
        self.assertEqual(card["image_url"], self._primary_url())

    def test_product_detail_primary_gallery_and_seo_use_stable_order(self):
        response = self.client.get(reverse("product-detail", kwargs={"slug": self.product.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["product_image_urls"], self._gallery_urls())
        gallery_urls = [item["url"] for item in response.context["product_gallery_images"]]
        self.assertEqual(gallery_urls, self._gallery_urls())

        primary_absolute = build_absolute_url(self._primary_url())
        self.assertEqual(response.context["seo_image_url"], primary_absolute)
        self.assertContains(response, f'property="og:image" content="{primary_absolute}"')

        json_ld_blocks = json.loads(response.context["seo_json_ld"])
        product_ld = next(block for block in json_ld_blocks if block.get("@type") == "Product")
        self.assertEqual(product_ld["image"], [primary_absolute])

    def test_public_favorites_use_lowest_id_image(self):
        session = self.client.session
        session[SESSION_FAVORITES_KEY] = [self.product.id]
        session.save()

        response = self.client.get(reverse("favorites"))
        self.assertEqual(response.status_code, 200)
        cards = response.context["favorites_products"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["image_url"], self._primary_url())

    def test_account_favorites_use_lowest_id_image(self):
        user = get_user_model().objects.create_user(email="fav-order@example.com", password="test-pass-123")
        Favorite.objects.create(user=user, product=self.product)
        self.client.force_login(user)

        response = self.client.get(reverse("account"), {"tab": "favorites"})
        self.assertEqual(response.status_code, 200)
        cards = response.context["account_favorites_products"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["image_url"], self._primary_url())

    def test_cart_selected_uses_lowest_id_image(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        response = self.client.get(reverse("cart"))
        self.assertEqual(response.status_code, 200)
        items = response.context["cart_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["image_url"], self._primary_url())

    def test_order_item_snapshot_uses_lowest_id_image(self):
        products = list(Product.objects.filter(pk=self.product.pk).prefetch_related("images"))
        order = Order()
        items = _prepare_order_items(order, products)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].product_image_snapshot, self._primary_url())


@override_settings(S3_PRODUCT_IMAGES_ENABLED=False)
class ProductImageOrderFieldPrecedenceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._enqueue_patcher = patch("apps.products.jobs.enqueue_product_image_thumbnails")
        cls._enqueue_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls._enqueue_patcher.stop()
        super().tearDownClass()

    def test_lower_order_wins_over_smaller_id(self):
        product = create_published_product(title="Precedence", slug="order-precedence", price=Decimal("5.00"))
        image_order_one = ProductImage(product=product, order=1)
        image_order_one.image = SimpleUploadedFile("first.png", b"1", content_type="image/png")
        image_order_one.save()

        image_order_zero = ProductImage(product=product, order=0)
        image_order_zero.image = SimpleUploadedFile("second.png", b"2", content_type="image/png")
        image_order_zero.save()

        self.assertGreater(image_order_zero.id, image_order_one.id)

        ordered = list(product.images.all())
        self.assertEqual(ordered[0].pk, image_order_zero.pk)
        self.assertEqual(ordered[1].pk, image_order_one.pk)
