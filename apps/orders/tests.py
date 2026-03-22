from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.cart.models import Cart, CartItem
from apps.cart.services import SESSION_CART_KEY
from apps.orders.models import Order, OrderItem
from apps.payments.models import Invoice
from apps.products.models import Category, Product


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
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

    def test_guest_checkout_post_creates_order_and_redirects(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        response = self.client.post(reverse("checkout"), {"email": "guest@example.com"})

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.email, "guest@example.com")
        self.assertEqual(order.checkout_type, Order.CheckoutType.GUEST)
        self.assertEqual(order.source, Order.Source.PALINGAMES)
        self.assertEqual(order.status, Order.OrderStatus.WAITING_FOR_PAYMENT)
        self.assertEqual(order.payment_account_no[:2], Order.Source.PALINGAMES)
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 1)
        invoice = Invoice.objects.get(order=order)
        self.assertRegex(invoice.provider_invoice_no or "", r"^\d{8}$")
        self.assertEqual(invoice.status, Invoice.InvoiceStatus.PENDING)
        self.assertEqual(invoice.invoice_url, f"https://example.com/pay/{invoice.provider_invoice_no}")
        self.assertEqual(invoice.amount, order.total_amount)
        self.assertIn(f"created={order.public_id}", response["Location"])

    def test_authenticated_checkout_post_creates_order_for_user(self):
        self.client.force_login(self.user)
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.product)

        self.client.post(reverse("checkout"), {"email": self.user.email})

        order = Order.objects.get()
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.checkout_type, Order.CheckoutType.AUTHENTICATED)
        self.assertEqual(order.source, Order.Source.PALINGAMES)

    def test_checkout_post_with_invalid_email_returns_error(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        response = self.client.post(reverse("checkout"), {"email": "not-an-email"})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Введите корректный Email.", status_code=400)
        self.assertFalse(Order.objects.exists())


class OrderModelTests(TestCase):
    def test_order_generates_payment_account_no_after_first_save(self):
        order = Order.objects.create(
            email="guest@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )

        self.assertIsNotNone(order.payment_account_no)
        self.assertEqual(len(order.payment_account_no), 16)
        self.assertTrue(order.payment_account_no.startswith(Order.Source.PALINGAMES))
        self.assertEqual(order.payment_account_no[2:8], order.created_at.strftime("%d%m%y"))
        self.assertRegex(order.payment_account_no[8:], r"^[0-9A-Z]{8}$")

    def test_payment_account_no_does_not_expose_plain_order_id(self):
        order = Order.objects.create(
            email="guest@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )

        self.assertNotEqual(order.payment_account_no[-8:], str(order.id).rjust(8, "0"))

    def test_payment_account_no_uses_order_source_prefix(self):
        order = Order.objects.create(
            email="guest@example.com",
            source=Order.Source.TELEGRAM,
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )

        self.assertTrue(order.payment_account_no.startswith(Order.Source.TELEGRAM))
