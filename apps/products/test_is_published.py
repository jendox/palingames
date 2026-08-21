from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.cart.services import SESSION_CART_KEY
from apps.orders.services import UNAVAILABLE_CART_PRODUCTS_MESSAGE, OrderCreationBlockedError, create_order_from_cart
from apps.products.models import Category, Product


def create_storefront_product(**kwargs) -> Product:
    defaults = {
        "title": "Storefront product",
        "slug": "storefront-product",
        "price": Decimal("10.00"),
        "is_published": True,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class ProductPublishedVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Каталог", slug="katalog")
        cls.published_product = create_storefront_product(
            title="Published game",
            slug="published-game",
        )
        cls.unpublished_product = Product.objects.create(
            title="Draft game",
            slug="draft-game",
            price=Decimal("12.00"),
            is_published=False,
        )
        cls.published_product.categories.add(cls.category)
        cls.unpublished_product.categories.add(cls.category)
        cls.staff_user = get_user_model().objects.create_user(
            email="staff@example.com",
            password="test-pass-123",
            is_staff=True,
        )

    def test_unpublished_product_not_in_catalog(self):
        response = self.client.get(
            reverse("catalog"),
            {"category": self.category.slug},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published_product.title)
        self.assertNotContains(response, self.unpublished_product.title)

    def test_unpublished_product_detail_returns_404_for_guest(self):
        response = self.client.get(
            reverse("product-detail", kwargs={"slug": self.unpublished_product.slug}),
        )

        self.assertEqual(response.status_code, 404)

    def test_published_product_detail_returns_200(self):
        response = self.client.get(
            reverse("product-detail", kwargs={"slug": self.published_product.slug}),
        )

        self.assertEqual(response.status_code, 200)

    def test_staff_can_preview_unpublished_product(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse("product-detail", kwargs={"slug": self.unpublished_product.slug}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["product_is_staff_preview"])
        self.assertContains(response, "Предпросмотр: товар не опубликован")

    def test_unpublished_product_missing_from_sitemap(self):
        response = self.client.get(reverse("sitemap-xml"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published_product.get_absolute_url())
        self.assertNotContains(response, self.unpublished_product.get_absolute_url())

    def test_cart_toggle_rejects_unpublished_product(self):
        response = self.client.post(
            reverse("cart-toggle"),
            {"product_id": self.unpublished_product.id},
        )

        self.assertEqual(response.status_code, 404)

    def test_checkout_blocks_unpublished_product_in_session_cart(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.unpublished_product.id]
        session.save()

        request = RequestFactory().post("/checkout/")
        request.session = self.client.session
        request.user = AnonymousUser()

        with self.assertRaises(OrderCreationBlockedError) as exc_info:
            create_order_from_cart(
                request=request,
                email="guest@example.com",
            )

        self.assertEqual(exc_info.exception.reason, "unavailable_products")

    def test_checkout_post_redirects_when_product_becomes_unpublished(self):
        self.client.post(reverse("cart-toggle"), {"product_id": self.published_product.id})
        self.published_product.is_published = False
        self.published_product.save(update_fields=["is_published"])

        response = self.client.post(
            reverse("checkout"),
            {
                "email": "guest@example.com",
                "personal_data_consent": "on",
                "checkout_idempotency_key": str(uuid.uuid4()),
            },
        )

        self.assertRedirects(response, reverse("cart"))
        flash_messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(UNAVAILABLE_CART_PRODUCTS_MESSAGE, flash_messages)

    def test_search_suggest_excludes_unpublished_product(self):
        response = self.client.get(
            reverse("catalog-search-suggest"),
            {"q": "Draft"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        urls = [item["url"] for item in payload["results"]]
        self.assertNotIn(self.unpublished_product.get_absolute_url(), urls)
