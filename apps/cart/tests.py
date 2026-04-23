from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.access.models import UserProductAccess
from apps.cart.models import CartItem
from apps.cart.services import SESSION_CART_KEY
from apps.orders.models import Order
from apps.products.models import Category, Product
from apps.users.models import CustomUser


class CartViewsTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(title="Дидактические игры", slug="didakticheskie-igry")
        self.product_1 = Product.objects.create(
            title="Игра 1",
            slug="igra-1",
            price="1.50",
        )
        self.product_2 = Product.objects.create(
            title="Игра 2",
            slug="igra-2",
            price="2.00",
        )
        self.product_1.categories.add(self.category)
        self.product_2.categories.add(self.category)

    @patch("apps.cart.views.inc_product_added_to_cart")
    def test_guest_cart_toggle_add_and_remove(self, inc_product_added_to_cart_mock):
        toggle_url = reverse("cart-toggle")

        response = self.client.post(toggle_url, {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "in_cart": True,
                "already_purchased": False,
                "already_pending_purchase": False,
                "message": "",
                "cart_count": 1,
            },
        )
        self.assertEqual(self.client.session.get(SESSION_CART_KEY), [self.product_1.id])
        inc_product_added_to_cart_mock.assert_called_once_with(user_type="guest")

        response = self.client.post(toggle_url, {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "in_cart": False,
                "already_purchased": False,
                "already_pending_purchase": False,
                "message": "",
                "cart_count": 0,
            },
        )
        self.assertEqual(self.client.session.get(SESSION_CART_KEY), [])

    def test_guest_remove_item_via_htmx_returns_cart_partial(self):
        self.client.post(reverse("cart-toggle"), {"product_id": self.product_1.id})

        response = self.client.post(
            reverse("cart-remove", kwargs={"product_id": self.product_1.id}),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="cart-page-content"')
        self.assertEqual(self.client.session.get(SESSION_CART_KEY), [])

    def test_guest_clear_cart(self):
        self.client.post(reverse("cart-toggle"), {"product_id": self.product_1.id})
        self.client.post(reverse("cart-toggle"), {"product_id": self.product_2.id})

        response = self.client.post(reverse("cart-clear"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY), [])

    @patch("apps.cart.views.inc_product_added_to_cart")
    def test_authenticated_cart_toggle_add_and_remove(self, inc_product_added_to_cart_mock):
        user = CustomUser.objects.create_user(email="cart@example.com", password="test-password-123")
        self.client.force_login(user)

        response = self.client.post(reverse("cart-toggle"), {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CartItem.objects.filter(cart__user=user, product=self.product_1).exists())
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "in_cart": True,
                "already_purchased": False,
                "already_pending_purchase": False,
                "message": "",
                "cart_count": 1,
            },
        )
        inc_product_added_to_cart_mock.assert_called_once_with(user_type="authenticated")

        response = self.client.post(reverse("cart-toggle"), {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CartItem.objects.filter(cart__user=user, product=self.product_1).exists())
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "in_cart": False,
                "already_purchased": False,
                "already_pending_purchase": False,
                "message": "",
                "cart_count": 0,
            },
        )

    def test_authenticated_cart_toggle_blocks_already_purchased_product(self):
        user = CustomUser.objects.create_user(email="access@example.com", password="test-password-123")
        order = Order.objects.create(
            user=user,
            email=user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            subtotal_amount="1.50",
            total_amount="1.50",
            items_count=1,
        )
        UserProductAccess.objects.create(user=user, product=self.product_1, order=order)
        self.client.force_login(user)

        response = self.client.post(reverse("cart-toggle"), {"product_id": self.product_1.id})

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "in_cart": False,
                "already_purchased": True,
                "already_pending_purchase": False,
                "message": "",
                "cart_count": 0,
            },
        )
        self.assertFalse(CartItem.objects.filter(cart__user=user, product=self.product_1).exists())

    def test_authenticated_cart_toggle_blocks_product_with_pending_order(self):
        user = CustomUser.objects.create_user(email="pending@example.com", password="test-password-123")
        pending_order = Order.objects.create(
            user=user,
            email=user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount="1.50",
            total_amount="1.50",
            items_count=1,
        )
        pending_order.items.create(
            product=self.product_1,
            title_snapshot=self.product_1.title,
            category_snapshot=self.category.title,
            unit_price_amount="1.50",
            quantity=1,
            line_total_amount="1.50",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("cart-toggle"), {"product_id": self.product_1.id})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["in_cart"])
        self.assertFalse(payload["already_purchased"])
        self.assertTrue(payload["already_pending_purchase"])
        self.assertIn("неоплаченный заказ", payload["message"])
        self.assertFalse(CartItem.objects.filter(cart__user=user, product=self.product_1).exists())

    def test_guest_cart_merges_to_user_after_login(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product_1.id, self.product_2.id]
        session.save()

        user = CustomUser.objects.create_user(email="merge@example.com", password="test-password-123")
        logged_in = self.client.login(email="merge@example.com", password="test-password-123")
        self.assertTrue(logged_in)

        cart_product_ids = set(CartItem.objects.filter(cart__user=user).values_list("product_id", flat=True))
        self.assertEqual(cart_product_ids, {self.product_1.id, self.product_2.id})
        self.assertEqual(self.client.session.get(SESSION_CART_KEY), [])

    def test_guest_cart_merge_skips_already_purchased_products(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product_1.id, self.product_2.id]
        session.save()

        user = CustomUser.objects.create_user(email="merge-access@example.com", password="test-password-123")
        order = Order.objects.create(
            user=user,
            email=user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            subtotal_amount="1.50",
            total_amount="1.50",
            items_count=1,
        )
        UserProductAccess.objects.create(user=user, product=self.product_1, order=order)

        logged_in = self.client.login(email="merge-access@example.com", password="test-password-123")
        self.assertTrue(logged_in)

        cart_product_ids = set(CartItem.objects.filter(cart__user=user).values_list("product_id", flat=True))
        self.assertEqual(cart_product_ids, {self.product_2.id})
        self.assertEqual(self.client.session.get(SESSION_CART_KEY), [])
