from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import OrderSource
from apps.orders.models import Order, OrderItem
from apps.orders.services import recalculate_manual_order_from_items
from apps.payments.models import Invoice
from apps.products.models import Category, Product
from apps.promocodes.models import PromoCode


class RecalculateManualOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Категория", slug="cat-manual-order")
        cls.product_a = Product.objects.create(title="Товар A", slug="prod-a-manual", price=Decimal("10.00"))
        cls.product_b = Product.objects.create(title="Товар B", slug="prod-b-manual", price=Decimal("25.00"))
        cls.product_a.categories.add(cls.category)

    def test_order_item_save_hydrates_from_product_like_admin_inline(self):
        """Read-only inline fields are omitted on POST; save() must fill NOT NULL columns."""
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.INSTAGRAM,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
        )
        item = OrderItem(order=order, product=self.product_a, quantity=3)
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.title_snapshot, "Товар A")
        self.assertEqual(item.category_snapshot, "Категория")
        self.assertEqual(item.unit_price_amount, Decimal("10.00"))
        self.assertEqual(item.line_total_amount, Decimal("30.00"))

    def test_recalculate_updates_line_snapshots_and_totals(self):
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.INSTAGRAM,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            items_count=0,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product_a,
            title_snapshot="",
            category_snapshot="",
            unit_price_amount=Decimal("0.00"),
            quantity=2,
            line_total_amount=Decimal("0.00"),
        )

        recalculate_manual_order_from_items(order_id=order.pk)

        order.refresh_from_db()
        self.assertEqual(order.subtotal_amount, Decimal("20.00"))
        self.assertEqual(order.total_amount, Decimal("20.00"))
        self.assertEqual(order.items_count, 2)
        item = order.items.get()
        self.assertEqual(item.title_snapshot, "Товар A")
        self.assertEqual(item.unit_price_amount, Decimal("10.00"))
        self.assertEqual(item.line_total_amount, Decimal("20.00"))

    def test_recalculate_applies_promo_and_redemption(self):
        PromoCode.objects.create(code="MAN10", discount_percent=10)
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.TELEGRAM,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            items_count=0,
        )
        promo = PromoCode.objects.get(code="MAN10")
        order.promo_code = promo
        order.save(update_fields=["promo_code"])
        OrderItem.objects.create(
            order=order,
            product=self.product_a,
            title_snapshot="",
            category_snapshot="",
            unit_price_amount=Decimal("0.00"),
            quantity=1,
            line_total_amount=Decimal("0.00"),
        )

        recalculate_manual_order_from_items(order_id=order.pk)

        order.refresh_from_db()
        self.assertEqual(order.subtotal_amount, Decimal("10.00"))
        self.assertEqual(order.discount_amount, Decimal("1.00"))
        self.assertEqual(order.total_amount, Decimal("9.00"))
        self.assertTrue(hasattr(order, "promo_redemption"))
        self.assertEqual(order.promo_redemption.discount_amount, Decimal("1.00"))

    def test_recalculate_skips_non_created_orders(self):
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product_a,
            title_snapshot="x",
            category_snapshot="",
            unit_price_amount=Decimal("10.00"),
            quantity=3,
            line_total_amount=Decimal("30.00"),
        )

        recalculate_manual_order_from_items(order_id=order.pk)

        order.refresh_from_db()
        self.assertEqual(order.subtotal_amount, Decimal("10.00"))
        item = order.items.get()
        self.assertEqual(item.line_total_amount, Decimal("30.00"))

    def test_recalculate_raises_on_invalid_promo_combination(self):
        PromoCode.objects.create(code="CATONLY", discount_percent=10)
        promo = PromoCode.objects.get(code="CATONLY")
        promo.categories.add(self.category)
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.INSTAGRAM,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            items_count=0,
            promo_code=promo,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product_b,
            title_snapshot="",
            category_snapshot="",
            unit_price_amount=Decimal("0.00"),
            quantity=1,
            line_total_amount=Decimal("0.00"),
        )

        with self.assertRaises(ValidationError):
            recalculate_manual_order_from_items(order_id=order.pk)


@override_settings(CUSTOM_GAME_ADMIN_EMAILS=["ops@example.com"])
class OrderAdminCreateInvoiceViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            email="staff@example.com",
            password="staff-pass-123",
            is_staff=True,
            is_superuser=True,
        )
        cls.product = Product.objects.create(title="IG item", slug="ig-item", price=Decimal("15.00"))

    def test_create_invoice_enqueues_task(self):
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.INSTAGRAM,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=Decimal("15.00"),
            total_amount=Decimal("15.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
        )

        self.client.force_login(self.staff)
        with patch("apps.orders.admin.enqueue_invoice_creation") as enqueue:
            response = self.client.get(reverse("admin:orders_order_create_invoice", args=[order.pk]))

        enqueue.assert_called_once_with(order.id, payment_target="order")
        self.assertEqual(response.status_code, 302)

    def test_create_invoice_forbidden_for_site_source(self):
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=Decimal("15.00"),
            total_amount=Decimal("15.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
        )

        self.client.force_login(self.staff)
        with patch("apps.orders.admin.enqueue_invoice_creation") as enqueue:
            self.client.get(reverse("admin:orders_order_create_invoice", args=[order.pk]))

        enqueue.assert_not_called()

    def test_create_invoice_blocked_when_pending_invoice_exists(self):
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.TELEGRAM,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=Decimal("15.00"),
            total_amount=Decimal("15.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
        )
        Invoice.objects.create(
            order=order,
            provider_invoice_no="12345678",
            status=Invoice.InvoiceStatus.PENDING,
            amount=Decimal("15.00"),
            currency=order.currency,
        )

        self.client.force_login(self.staff)
        with patch("apps.orders.admin.enqueue_invoice_creation") as enqueue:
            self.client.get(reverse("admin:orders_order_create_invoice", args=[order.pk]))

        enqueue.assert_not_called()
