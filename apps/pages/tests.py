from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.access.models import UserProductAccess
from apps.orders.models import Order, OrderItem
from apps.products.models import Product


class AccountOrdersDownloadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="account@example.com", password="test-pass-123")
        cls.product = Product.objects.create(title="Архив", slug="account-archive", price=Decimal("19.00"))
        cls.order = Order.objects.create(
            user=cls.user,
            email=cls.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("19.00"),
            total_amount=Decimal("19.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=cls.order,
            product=cls.product,
            title_snapshot=cls.product.title,
            category_snapshot="",
            unit_price_amount=cls.product.price,
            quantity=1,
            line_total_amount=cls.product.price,
            product_slug_snapshot=cls.product.slug,
            product_image_snapshot="https://example.com/product.png",
        )
        UserProductAccess.objects.create(user=cls.user, product=cls.product, order=cls.order)

    def test_account_orders_include_download_url_for_accessible_items(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("account"), {"tab": "orders"})

        self.assertEqual(response.status_code, 200)
        order_item = response.context["account_orders"][0]["items"][0]
        self.assertEqual(
            order_item["download_url"],
            reverse("product-download", kwargs={"product_id": self.product.id}),
        )
