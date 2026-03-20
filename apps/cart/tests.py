from django.test import TestCase
from django.urls import reverse

from apps.cart.models import CartItem
from apps.cart.services import SESSION_CART_KEY
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

    def test_guest_cart_toggle_add_and_remove(self):
        toggle_url = reverse("cart-toggle")

        response = self.client.post(toggle_url, {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "in_cart": True,
                "cart_count": 1,
            },
        )
        self.assertEqual(self.client.session.get(SESSION_CART_KEY), [self.product_1.id])

        response = self.client.post(toggle_url, {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "in_cart": False,
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

    def test_authenticated_cart_toggle_add_and_remove(self):
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
                "cart_count": 1,
            },
        )

        response = self.client.post(reverse("cart-toggle"), {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CartItem.objects.filter(cart__user=user, product=self.product_1).exists())
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "in_cart": False,
                "cart_count": 0,
            },
        )

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
