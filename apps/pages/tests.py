from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.access.models import UserProductAccess
from apps.favorites.models import Favorite
from apps.orders.guest_merge import merge_guest_orders_for_user
from apps.orders.models import Order, OrderItem
from apps.products.models import Category, Product

PASSWORD_CHANGE_RATE_LIMIT_MESSAGE = "Слишком много попыток смены пароля. Попробуйте позже."


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

    def test_account_orders_failed_status_includes_failure_hint(self):
        failed_order = Order.objects.create(
            user=self.user,
            email=self.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.FAILED,
            failure_reason="invoice_expired",
            subtotal_amount=Decimal("19.00"),
            total_amount=Decimal("19.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=failed_order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
            product_slug_snapshot=self.product.slug,
            product_image_snapshot="https://example.com/product.png",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("account"), {"tab": "orders"})

        self.assertEqual(response.status_code, 200)
        order_ctx = next(
            item for item in response.context["account_orders"] if item["number"] == failed_order.payment_account_no
        )
        self.assertIn("Срок оплаты", order_ctx["status_failure_hint"])
        self.assertContains(response, order_ctx["status_failure_hint"])

        paid_ctx = next(
            item for item in response.context["account_orders"] if item["number"] == self.order.payment_account_no
        )
        self.assertEqual(paid_ctx["status_failure_hint"], "")

    def test_account_orders_failed_unknown_reason_uses_fallback_hint(self):
        failed_order = Order.objects.create(
            user=self.user,
            email=self.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.FAILED,
            failure_reason="some_future_code",
            subtotal_amount=Decimal("19.00"),
            total_amount=Decimal("19.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=failed_order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
            product_slug_snapshot=self.product.slug,
            product_image_snapshot="https://example.com/product.png",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("account"), {"tab": "orders"})

        self.assertEqual(response.status_code, 200)
        order_ctx = next(
            item for item in response.context["account_orders"] if item["number"] == failed_order.payment_account_no
        )
        self.assertIn("Не удалось завершить оплату", order_ctx["status_failure_hint"])
        self.assertContains(response, order_ctx["status_failure_hint"])

    def test_account_orders_include_merged_guest_orders(self):
        guest_order = Order.objects.create(
            email=self.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("19.00"),
            total_amount=Decimal("19.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=guest_order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
            product_slug_snapshot=self.product.slug,
            product_image_snapshot="https://example.com/product.png",
        )

        merge_guest_orders_for_user(user=self.user, email=self.user.email)
        self.client.force_login(self.user)

        response = self.client.get(reverse("account"), {"tab": "orders"})

        self.assertEqual(response.status_code, 200)
        order_numbers = {item["number"] for item in response.context["account_orders"]}
        self.assertIn(guest_order.payment_account_no, order_numbers)


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


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "account-password-rate-limit-test-default",
        },
        "rate_limit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "account-password-rate-limit-test-rate-limit",
        },
    },
    ACCOUNT_PASSWORD_CHANGE_USER_RATE_LIMIT=1,
    ACCOUNT_PASSWORD_CHANGE_USER_RATE_LIMIT_WINDOW_SECONDS=600,
    ACCOUNT_PASSWORD_CHANGE_IP_RATE_LIMIT=100,
    ACCOUNT_PASSWORD_CHANGE_IP_RATE_LIMIT_WINDOW_SECONDS=600,
)
class AccountPasswordChangeRateLimitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="password-limit@example.com",
            password="test-pass-123",
        )

    def setUp(self):
        caches["rate_limit"].clear()
        self.client.force_login(self.user)

    def _post_password_change(self, *, old_password="wrong-pass", remote_addr="203.0.113.70"):
        return self.client.post(
            reverse("account"),
            data={
                "old_password": old_password,
                "new_password1": "new-test-pass-123",
                "new_password2": "new-test-pass-123",
            },
            query_params={"tab": "password"},
            REMOTE_ADDR=remote_addr,
        )

    def test_account_password_change_enforces_user_rate_limit(self):
        first = self._post_password_change()
        second = self._post_password_change()

        self.assertEqual(first.status_code, 200)
        self.assertNotContains(first, PASSWORD_CHANGE_RATE_LIMIT_MESSAGE)
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, PASSWORD_CHANGE_RATE_LIMIT_MESSAGE)

    @override_settings(
        ACCOUNT_PASSWORD_CHANGE_USER_RATE_LIMIT=100,
        ACCOUNT_PASSWORD_CHANGE_USER_RATE_LIMIT_WINDOW_SECONDS=600,
        ACCOUNT_PASSWORD_CHANGE_IP_RATE_LIMIT=1,
        ACCOUNT_PASSWORD_CHANGE_IP_RATE_LIMIT_WINDOW_SECONDS=600,
    )
    def test_account_password_change_enforces_ip_rate_limit(self):
        first = self._post_password_change(old_password="first-wrong-pass")
        second = self._post_password_change(old_password="second-wrong-pass")

        self.assertEqual(first.status_code, 200)
        self.assertNotContains(first, PASSWORD_CHANGE_RATE_LIMIT_MESSAGE)
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, PASSWORD_CHANGE_RATE_LIMIT_MESSAGE)
