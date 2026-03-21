from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.cart.models import Cart, CartItem
from apps.cart.services import SESSION_CART_KEY
from apps.products.models import Category, Product


class CheckoutPageViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Дидактические игры", slug="didactic-games")
        cls.product = Product.objects.create(title="Альфа", slug="alpha-checkout", price=Decimal("25.00"))
        cls.product.categories.add(cls.category)
        cls.user = get_user_model().objects.create_user(email="test@example.com", password="test-pass-123")

    def test_checkout_redirects_to_cart_when_empty(self):
        response = self.client.get(reverse("checkout"))

        self.assertRedirects(response, reverse("cart"))

    def test_guest_checkout_uses_first_step(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        response = self.client.get(reverse("checkout"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["checkout_step"], 1)
        self.assertEqual(response.context["checkout_email"], "")

    def test_authenticated_checkout_prefills_email_and_uses_second_step(self):
        self.client.force_login(self.user)
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.product)

        response = self.client.get(reverse("checkout"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["checkout_step"], 2)
        self.assertEqual(response.context["checkout_email"], self.user.email)
