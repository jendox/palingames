from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.access.models import UserProductAccess
from apps.cart.models import Cart, CartItem
from apps.cart.services import SESSION_CART_KEY
from apps.orders.models import Order, OrderItem
from apps.orders.services import CHECKOUT_IDEMPOTENCY_KEY_SESSION_KEY
from apps.payments.models import Invoice
from apps.payments.tasks import create_invoice_task
from apps.products.models import Category, Product
from apps.promocodes.models import PromoCode, PromoCodeRedemption
from libs.payments.models import CreateInvoiceResult


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    EXPRESS_PAY_IS_TEST=False,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "orders-test-default",
        },
        "rate_limit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "orders-test-rate-limit",
        },
    },
)
class CheckoutTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Дидактические игры", slug="didactic-games")
        cls.other_category = Category.objects.create(title="Методические материалы", slug="method-materials")
        cls.product = Product.objects.create(title="Альфа", slug="alpha-checkout", price=Decimal("25.00"))
        cls.product.categories.add(cls.category)
        cls.other_product = Product.objects.create(title="Методичка", slug="method-checkout", price=Decimal("10.00"))
        cls.other_product.categories.add(cls.other_category)
        cls.user = get_user_model().objects.create_user(email="test@example.com", password="test-pass-123")

    def setUp(self):
        caches["rate_limit"].clear()
        self.express_pay_client_patcher = patch("apps.payments.tasks.get_express_pay_request_client")
        self.mock_get_express_pay_request_client = self.express_pay_client_patcher.start()
        self.addCleanup(self.express_pay_client_patcher.stop)

        mock_client = Mock()
        mock_client.create_invoice.return_value = CreateInvoiceResult(
            invoice_no=12345678,
            invoice_url="https://example.com/pay/12345678",
        )
        self.mock_get_express_pay_request_client.return_value = mock_client


class CheckoutPageViewTests(CheckoutTestBase):
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
        self.assertContains(response, 'name="checkout_idempotency_key"')
        self.assertTrue(response.context["checkout_idempotency_key"])

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
        self.assertEqual(invoice.provider_invoice_no, "12345678")
        self.assertEqual(invoice.status, Invoice.InvoiceStatus.PENDING)
        self.assertEqual(invoice.invoice_url, "https://example.com/pay/12345678")
        self.assertEqual(invoice.amount, order.total_amount)
        self.assertIsNotNone(invoice.expires_at)
        self.assertIn(f"created={order.public_id}", response["Location"])
        self.assertEqual(self.client.session.get(SESSION_CART_KEY), [])
        self.assertNotIn(CHECKOUT_IDEMPOTENCY_KEY_SESSION_KEY, self.client.session)

        success_response = self.client.get(response["Location"])

        self.assertEqual(success_response.status_code, 200)
        self.assertEqual(success_response.context["checkout_created_order"], order)

    def test_authenticated_checkout_post_creates_order_for_user(self):
        self.client.force_login(self.user)
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.product)

        self.client.post(reverse("checkout"), {"email": self.user.email})

        order = Order.objects.get()
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.checkout_type, Order.CheckoutType.AUTHENTICATED)
        self.assertEqual(order.source, Order.Source.PALINGAMES)
        self.assertFalse(CartItem.objects.filter(cart=cart).exists())

    def test_authenticated_checkout_blocks_already_purchased_products(self):
        self.client.force_login(self.user)
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.product)
        paid_order = Order.objects.create(
            user=self.user,
            email=self.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        UserProductAccess.objects.create(user=self.user, product=self.product, order=paid_order)

        response = self.client.post(reverse("checkout"), {"email": self.user.email})

        self.assertRedirects(response, reverse("cart"))
        self.assertEqual(Order.objects.exclude(pk=paid_order.pk).count(), 0)

    def test_checkout_post_with_invalid_email_returns_error(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        response = self.client.post(reverse("checkout"), {"email": "not-an-email"})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Введите корректный Email.", status_code=400)
        self.assertFalse(Order.objects.exists())

    def test_checkout_promo_apply_updates_summary_without_creating_order(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()
        PromoCode.objects.create(code="review10", discount_percent=10, max_redemptions_per_email=1)

        response = self.client.post(
            reverse("checkout-promo-apply"),
            {
                "email": "guest@example.com",
                "promo_code": "review10",
                "checkout_variant": "desktop",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Промокод применен.")
        self.assertContains(response, "22,50 BYN")
        self.assertFalse(Order.objects.exists())

    def test_checkout_post_applies_category_limited_percent_promo(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id, self.other_product.id]
        session["checkout_promo_code"] = "EDU10"
        session.save()
        promo_code = PromoCode.objects.create(code="EDU10", discount_percent=10, max_redemptions_per_email=1)
        promo_code.categories.add(self.category)

        response = self.client.post(reverse("checkout"), {"email": "guest@example.com"})

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.subtotal_amount, Decimal("35.00"))
        self.assertEqual(order.promo_code, promo_code)
        self.assertEqual(order.promo_code_snapshot, "EDU10")
        self.assertEqual(order.discount_percent_snapshot, 10)
        self.assertEqual(order.promo_eligible_amount, Decimal("25.00"))
        self.assertEqual(order.discount_amount, Decimal("2.50"))
        self.assertEqual(order.total_amount, Decimal("32.50"))
        self.assertEqual(Invoice.objects.get(order=order).amount, Decimal("32.50"))
        redemption = PromoCodeRedemption.objects.get(order=order)
        self.assertEqual(redemption.discount_amount, Decimal("2.50"))
        discounted_item = OrderItem.objects.get(order=order, product=self.product)
        regular_item = OrderItem.objects.get(order=order, product=self.other_product)
        self.assertTrue(discounted_item.promo_eligible)
        self.assertEqual(discounted_item.discount_amount, Decimal("2.50"))
        self.assertEqual(discounted_item.discounted_line_total_amount, Decimal("22.50"))
        self.assertFalse(regular_item.promo_eligible)
        self.assertEqual(regular_item.discount_amount, Decimal("0.00"))
        self.assertIsNone(regular_item.discounted_line_total_amount)

    def test_checkout_post_rejects_invalid_promo_code(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        response = self.client.post(
            reverse("checkout"),
            {
                "email": "guest@example.com",
                "promo_code": "missing",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Промокод недействителен.", status_code=400)
        self.assertFalse(Order.objects.exists())

    def test_checkout_post_enforces_promo_email_limit(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()
        promo_code = PromoCode.objects.create(code="ONCE10", discount_percent=10, max_redemptions_per_email=1)
        previous_order = Order.objects.create(
            email="guest@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount=Decimal("25.00"),
            discount_amount=Decimal("2.50"),
            total_amount=Decimal("22.50"),
            items_count=1,
        )
        PromoCodeRedemption.objects.create(
            promo_code=promo_code,
            order=previous_order,
            email="guest@example.com",
            subtotal_amount=Decimal("25.00"),
            eligible_amount=Decimal("25.00"),
            discount_amount=Decimal("2.50"),
        )

        response = self.client.post(
            reverse("checkout"),
            {
                "email": "guest@example.com",
                "promo_code": "ONCE10",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Лимит использования промокода исчерпан.", status_code=400)
        self.assertEqual(Order.objects.count(), 1)

    @override_settings(
        CHECKOUT_CREATE_EMAIL_RATE_LIMIT=1,
        CHECKOUT_CREATE_EMAIL_RATE_LIMIT_WINDOW_SECONDS=600,
        CHECKOUT_CREATE_IP_RATE_LIMIT=100,
        CHECKOUT_CREATE_IP_RATE_LIMIT_WINDOW_SECONDS=3600,
    )
    def test_checkout_post_enforces_email_rate_limit(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        first_response = self.client.post(reverse("checkout"), {"email": "guest@example.com"})

        self.assertEqual(first_response.status_code, 302)

        session = self.client.session
        session[SESSION_CART_KEY] = [self.other_product.id]
        session.save()

        second_response = self.client.post(reverse("checkout"), {"email": "guest@example.com"})

        self.assertEqual(second_response.status_code, 429)
        self.assertContains(
            second_response,
            "Слишком много попыток оформления заказа. Попробуйте позже.",
            status_code=429,
        )
        self.assertEqual(second_response["Retry-After"], "600")
        self.assertEqual(Order.objects.count(), 1)

    @override_settings(
        CHECKOUT_CREATE_EMAIL_RATE_LIMIT=100,
        CHECKOUT_CREATE_EMAIL_RATE_LIMIT_WINDOW_SECONDS=600,
        CHECKOUT_CREATE_IP_RATE_LIMIT=1,
        CHECKOUT_CREATE_IP_RATE_LIMIT_WINDOW_SECONDS=3600,
    )
    def test_checkout_post_enforces_ip_rate_limit(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        first_response = self.client.post(
            reverse("checkout"),
            {"email": "first@example.com"},
            REMOTE_ADDR="203.0.113.10",
        )

        self.assertEqual(first_response.status_code, 302)

        session = self.client.session
        session[SESSION_CART_KEY] = [self.other_product.id]
        session.save()

        second_response = self.client.post(
            reverse("checkout"),
            {"email": "second@example.com"},
            REMOTE_ADDR="203.0.113.10",
        )

        self.assertEqual(second_response.status_code, 429)
        self.assertContains(
            second_response,
            "Слишком много попыток оформления заказа. Попробуйте позже.",
            status_code=429,
        )
        self.assertEqual(second_response["Retry-After"], "3600")
        self.assertEqual(Order.objects.count(), 1)

    @override_settings(
        CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT=1,
        CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT_WINDOW_SECONDS=600,
        CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT=100,
        CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT_WINDOW_SECONDS=600,
    )
    def test_checkout_promo_apply_enforces_email_rate_limit(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        first_response = self.client.post(
            reverse("checkout-promo-apply"),
            {
                "email": "guest@example.com",
                "promo_code": "missing-one",
                "checkout_variant": "desktop",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.post(
            reverse("checkout-promo-apply"),
            {
                "email": "guest@example.com",
                "promo_code": "missing-two",
                "checkout_variant": "desktop",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(second_response.status_code, 429)
        self.assertContains(
            second_response,
            "Слишком много попыток применения промокода. Попробуйте позже.",
            status_code=429,
        )
        self.assertEqual(second_response["Retry-After"], "600")
        self.assertNotEqual(self.client.session.get("checkout_promo_code"), "MISSING-TWO")

    @override_settings(
        CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT=100,
        CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT_WINDOW_SECONDS=600,
        CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT=1,
        CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT_WINDOW_SECONDS=600,
    )
    def test_checkout_promo_apply_enforces_ip_rate_limit(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        first_response = self.client.post(
            reverse("checkout-promo-apply"),
            {
                "email": "first@example.com",
                "promo_code": "missing-one",
                "checkout_variant": "mobile",
            },
            HTTP_HX_REQUEST="true",
            REMOTE_ADDR="203.0.113.20",
        )

        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.post(
            reverse("checkout-promo-apply"),
            {
                "email": "second@example.com",
                "promo_code": "missing-two",
                "checkout_variant": "mobile",
            },
            HTTP_HX_REQUEST="true",
            REMOTE_ADDR="203.0.113.20",
        )

        self.assertEqual(second_response.status_code, 429)
        self.assertContains(
            second_response,
            "Слишком много попыток применения промокода. Попробуйте позже.",
            status_code=429,
        )
        self.assertEqual(second_response["Retry-After"], "600")

    def test_create_invoice_task_is_idempotent_for_existing_pending_invoice(self):
        order = Order.objects.create(
            email="guest@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        invoice = Invoice.objects.create(
            order=order,
            provider_invoice_no="76543210",
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url="https://example.com/pay/76543210",
            amount=order.total_amount,
            currency=order.currency,
        )

        create_invoice_task(order.id)

        self.assertEqual(Invoice.objects.count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.provider_invoice_no, "76543210")
        self.mock_get_express_pay_request_client.return_value.create_invoice.assert_not_called()


class CheckoutIdempotencyTests(CheckoutTestBase):
    def test_checkout_idempotency_key_reuses_existing_order_after_cart_is_cleared(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()
        checkout_response = self.client.get(reverse("checkout"))
        checkout_idempotency_key = checkout_response.context["checkout_idempotency_key"]

        first_response = self.client.post(
            reverse("checkout"),
            {
                "email": "guest@example.com",
                "checkout_idempotency_key": checkout_idempotency_key,
            },
        )
        second_response = self.client.post(
            reverse("checkout"),
            {
                "email": "guest@example.com",
                "checkout_idempotency_key": checkout_idempotency_key,
            },
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(first_response["Location"], second_response["Location"])
        order = Order.objects.get()
        self.assertEqual(str(order.checkout_idempotency_key), checkout_idempotency_key)
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 1)
        self.assertEqual(Invoice.objects.filter(order=order).count(), 1)
        self.mock_get_express_pay_request_client.return_value.create_invoice.assert_called_once()

    @override_settings(
        CHECKOUT_CREATE_EMAIL_RATE_LIMIT=1,
        CHECKOUT_CREATE_EMAIL_RATE_LIMIT_WINDOW_SECONDS=600,
        CHECKOUT_CREATE_IP_RATE_LIMIT=1,
        CHECKOUT_CREATE_IP_RATE_LIMIT_WINDOW_SECONDS=3600,
    )
    def test_checkout_idempotency_key_bypasses_rate_limit_for_existing_order(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()
        checkout_response = self.client.get(reverse("checkout"))
        checkout_idempotency_key = checkout_response.context["checkout_idempotency_key"]

        first_response = self.client.post(
            reverse("checkout"),
            {
                "email": "guest@example.com",
                "checkout_idempotency_key": checkout_idempotency_key,
            },
        )
        second_response = self.client.post(
            reverse("checkout"),
            {
                "email": "guest@example.com",
                "checkout_idempotency_key": checkout_idempotency_key,
            },
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)

    def test_checkout_idempotency_key_changes_when_cart_changes(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.product.id]
        session.save()

        first_response = self.client.get(reverse("checkout"))
        second_response = self.client.get(reverse("checkout"))

        self.assertEqual(
            first_response.context["checkout_idempotency_key"],
            second_response.context["checkout_idempotency_key"],
        )

        session = self.client.session
        session[SESSION_CART_KEY] = [self.other_product.id]
        session.save()

        third_response = self.client.get(reverse("checkout"))

        self.assertNotEqual(
            first_response.context["checkout_idempotency_key"],
            third_response.context["checkout_idempotency_key"],
        )


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
