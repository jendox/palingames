from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import OrderSource
from apps.orders.admin import OrderAdmin
from apps.orders.models import Order, OrderItem
from apps.orders.services import recalculate_manual_order_from_items
from apps.orders.watchdog import WATCHDOG_GRACE_PERIOD
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

    def _create_site_order(
        self,
        *,
        status=Order.OrderStatus.CREATED,
        total=Decimal("15.00"),
        items_count=1,
        created_age: timedelta | None = None,
    ) -> Order:
        order = Order.objects.create(
            email="buyer@example.com",
            source=OrderSource.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=status,
            subtotal_amount=total,
            total_amount=total,
            items_count=items_count,
        )
        if items_count > 0:
            OrderItem.objects.create(
                order=order,
                product=self.product,
                title_snapshot=self.product.title,
                category_snapshot="",
                unit_price_amount=self.product.price,
                quantity=1,
                line_total_amount=self.product.price,
            )
        if created_age is not None:
            Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - created_age)
            order.refresh_from_db()
        return order

    def _site_order_ready_age(self) -> timedelta:
        return WATCHDOG_GRACE_PERIOD + timedelta(minutes=1)

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
        order = self._create_site_order()

        self.assertFalse(OrderAdmin._can_create_invoice_from_admin(order))

        self.client.force_login(self.staff)
        with patch("apps.orders.admin.enqueue_invoice_creation") as enqueue:
            response = self.client.get(reverse("admin:orders_order_create_invoice", args=[order.pk]), follow=True)

        enqueue.assert_not_called()
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("недоступно" in m.lower() for m in messages))

    def test_create_invoice_allowed_for_aged_site_order_without_invoice(self):
        order = self._create_site_order(created_age=self._site_order_ready_age())

        self.assertTrue(OrderAdmin._can_create_invoice_from_admin(order))

        self.client.force_login(self.staff)
        with patch("apps.orders.admin.enqueue_invoice_creation") as enqueue:
            response = self.client.get(reverse("admin:orders_order_create_invoice", args=[order.pk]))

        enqueue.assert_called_once_with(order.id, payment_target="order")
        self.assertEqual(response.status_code, 302)
        change = self.client.get(reverse("admin:orders_order_change", args=[order.pk]))
        self.assertContains(change, "Создать инвойс")

    def test_create_invoice_blocked_for_site_order_with_pending_invoice(self):
        order = self._create_site_order(created_age=self._site_order_ready_age())
        Invoice.objects.create(
            order=order,
            provider_invoice_no="87654321",
            status=Invoice.InvoiceStatus.PENDING,
            amount=order.total_amount,
            currency=order.currency,
        )

        self.assertFalse(OrderAdmin._can_create_invoice_from_admin(order))

        self.client.force_login(self.staff)
        with patch("apps.orders.admin.enqueue_invoice_creation") as enqueue:
            self.client.get(reverse("admin:orders_order_create_invoice", args=[order.pk]))

        enqueue.assert_not_called()

    def test_create_invoice_allowed_for_site_order_with_expired_invoice(self):
        order = self._create_site_order(created_age=self._site_order_ready_age())
        Invoice.objects.create(
            order=order,
            provider_invoice_no="87654322",
            status=Invoice.InvoiceStatus.EXPIRED,
            amount=order.total_amount,
            currency=order.currency,
        )

        self.assertTrue(OrderAdmin._can_create_invoice_from_admin(order))

    def test_create_invoice_blocked_for_site_order_with_paid_invoice(self):
        order = self._create_site_order(created_age=self._site_order_ready_age())
        Invoice.objects.create(
            order=order,
            provider_invoice_no="87654323",
            status=Invoice.InvoiceStatus.PAID,
            amount=order.total_amount,
            currency=order.currency,
            paid_at=timezone.now(),
        )

        self.assertFalse(OrderAdmin._can_create_invoice_from_admin(order))

    def test_create_invoice_blocked_for_site_order_waiting_for_payment(self):
        order = self._create_site_order(
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            created_age=self._site_order_ready_age(),
        )

        self.assertFalse(OrderAdmin._can_create_invoice_from_admin(order))

    def test_create_invoice_blocked_for_site_order_zero_total_and_no_items(self):
        order = self._create_site_order(total=Decimal("0.00"), items_count=0, created_age=self._site_order_ready_age())

        self.assertFalse(OrderAdmin._can_create_invoice_from_admin(order))

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
