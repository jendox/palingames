from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.access.models import UserProductAccess
from apps.favorites.models import Favorite
from apps.orders.models import Order, OrderItem
from apps.products.models import Category, Product


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

    def test_account_orders_include_promo_snapshot_prices(self):
        discounted_order = Order.objects.create(
            user=self.user,
            email=self.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("19.00"),
            promo_code_snapshot="EDU10",
            discount_percent_snapshot=10,
            promo_eligible_amount=Decimal("19.00"),
            discount_amount=Decimal("1.90"),
            total_amount=Decimal("17.10"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=discounted_order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
            promo_eligible=True,
            discount_amount=Decimal("1.90"),
            discounted_line_total_amount=Decimal("17.10"),
            product_slug_snapshot=self.product.slug,
            product_image_snapshot="https://example.com/product.png",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("account"), {"tab": "orders"})

        self.assertEqual(response.status_code, 200)
        order = next(
            item
            for item in response.context["account_orders"]
            if item["number"] == discounted_order.payment_account_no
        )
        self.assertTrue(order["has_discount"])
        self.assertEqual(order["promo_code"], "EDU10")
        self.assertEqual(order["discount"], "1,90 BYN")
        order_item = order["items"][0]
        self.assertTrue(order_item["has_discount"])
        self.assertEqual(order_item["price"], "19,00 BYN")
        self.assertEqual(order_item["discounted_price"], "17,10 BYN")
        self.assertContains(response, "Промокод EDU10 · Скидка 1,90 BYN")


class AccountFavoritesTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="fav-tab@example.com", password="test-pass-123")
        cls.category = Category.objects.create(title="Дидактические игры", slug="didakticheskie-igry-fav")
        cls.product = Product.objects.create(title="Избранный товар", slug="fav-tab-product", price=Decimal("19.00"))
        cls.product.categories.add(cls.category)

    def test_favorites_tab_lists_saved_products(self):
        Favorite.objects.create(user=self.user, product=self.product)
        self.client.force_login(self.user)

        response = self.client.get(reverse("account"), {"tab": "favorites"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["account_favorites_count"], 1)
        cards = response.context["account_favorites_products"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], self.product.id)

    def test_favorites_tab_htmx_target_results_returns_fragment(self):
        Favorite.objects.create(user=self.user, product=self.product)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("account"),
            {"tab": "favorites"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="account-favorites-desktop-results",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/account/desktop/_account_favorites_desktop_results.html")
