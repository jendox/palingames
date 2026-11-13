from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.access.models import GuestAccess, UserProductAccess
from apps.access.services import grant_guest_product_accesses
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import NotificationType
from apps.orders.guest_merge import merge_guest_orders_for_user
from apps.orders.models import Order, OrderItem
from apps.orders.watchdog import check_paid_order_delivery
from apps.payments.models import Invoice
from apps.payments.services import mark_order_paid
from apps.products.models import Product

ENCRYPTION_TEST_SETTINGS = {
    "APP_DATA_ENCRYPTION_KEY": "5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
}


class GrantGuestProductAccessesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(title="Item", slug="guest-fulfillment-item", price=Decimal("10.00"))
        cls.user = get_user_model().objects.create_user(email="buyer@example.com", password="test-pass-123")

    def _guest_order_with_item(self, *, user=None) -> Order:
        order = Order.objects.create(
            email="buyer@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            user=user,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
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
            product_slug_snapshot=self.product.slug,
        )
        return order

    def test_authenticated_checkout_returns_empty_list(self):
        order = Order.objects.create(
            email="buyer@example.com",
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            user=self.user,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
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

        payloads = grant_guest_product_accesses(order)

        self.assertEqual(payloads, [])
        self.assertFalse(GuestAccess.objects.filter(order=order).exists())

    def test_guest_order_with_user_creates_guest_access_per_item(self):
        order = self._guest_order_with_item(user=self.user)

        payloads = grant_guest_product_accesses(order)

        self.assertEqual(len(payloads), 1)
        self.assertEqual(GuestAccess.objects.filter(order=order).count(), 1)


@override_settings(**ENCRYPTION_TEST_SETTINGS)
class MarkOrderPaidGuestCheckoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(title="Paid item", slug="guest-paid-item", price=Decimal("10.00"))
        cls.user = get_user_model().objects.create_user(email="guest-paid@example.com", password="test-pass-123")

    def _paid_setup(self, *, user=None) -> tuple[Order, Invoice]:
        order = Order.objects.create(
            email="guest-paid@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            user=user,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
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
            product_slug_snapshot=self.product.slug,
        )
        invoice = Invoice.objects.create(
            order=order,
            provider_invoice_no="11223344",
            status=Invoice.InvoiceStatus.PENDING,
            amount=order.total_amount,
            currency=933,
        )
        return order, invoice

    @patch("apps.payments.services.enqueue_notification_outbox")
    def test_mark_order_paid_guest_with_user_grants_guest_and_user_access(self, enqueue_mock):
        order, invoice = self._paid_setup(user=self.user)

        mark_order_paid(order, invoice, source="test", persist=True)

        self.assertEqual(GuestAccess.objects.filter(order=order).count(), 1)
        self.assertTrue(UserProductAccess.objects.filter(user=self.user, product=self.product, order=order).exists())
        outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            object_id=order.id,
        )
        self.assertEqual(outbox.status, NotificationOutbox.Status.PENDING)
        enqueue_mock.assert_called_once_with(outbox)

    @patch("apps.payments.services.enqueue_notification_outbox")
    def test_mark_order_paid_guest_without_user_works_as_before(self, enqueue_mock):
        order, invoice = self._paid_setup(user=None)

        mark_order_paid(order, invoice, source="test", persist=True)

        self.assertEqual(GuestAccess.objects.filter(order=order).count(), 1)
        self.assertFalse(UserProductAccess.objects.exists())
        enqueue_mock.assert_called_once()

    @patch("apps.payments.services.enqueue_notification_outbox")
    def test_duplicate_mark_order_paid_does_not_enqueue_second_outbox(self, enqueue_mock):
        order, invoice = self._paid_setup(user=self.user)

        mark_order_paid(order, invoice, source="test", persist=True)
        enqueue_mock.reset_mock()
        order.refresh_from_db()
        invoice.refresh_from_db()

        mark_order_paid(order, invoice, source="test", persist=True)

        enqueue_mock.assert_not_called()
        self.assertEqual(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
                object_id=order.id,
            ).count(),
            1,
        )


@override_settings(**ENCRYPTION_TEST_SETTINGS)
class GuestMergeThenPayRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(title="Merge item", slug="guest-merge-item", price=Decimal("10.00"))
        cls.user = get_user_model().objects.create_user(email="merge-pay@example.com", password="test-pass-123")

    @patch("apps.payments.services.enqueue_notification_outbox")
    def test_merge_before_pay_grants_guest_access_and_cabinet_access(self, enqueue_mock):
        order = Order.objects.create(
            email="merge-pay@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            user=None,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
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
            product_slug_snapshot=self.product.slug,
        )
        merge_guest_orders_for_user(user=self.user, email=self.user.email)
        order.refresh_from_db()
        self.assertEqual(order.user_id, self.user.id)
        self.assertEqual(order.checkout_type, Order.CheckoutType.GUEST)

        invoice = Invoice.objects.create(
            order=order,
            provider_invoice_no="99887766",
            status=Invoice.InvoiceStatus.PENDING,
            amount=order.total_amount,
            currency=933,
        )
        mark_order_paid(order, invoice, paid_at=timezone.now(), source="test", persist=True)

        self.assertEqual(GuestAccess.objects.filter(order=order).count(), 1)
        self.assertTrue(UserProductAccess.objects.filter(user=self.user, product=self.product).exists())
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
                object_id=order.id,
            ).exists(),
        )
        order.status = Order.OrderStatus.PAID
        order.paid_at = timezone.now()
        order.save(update_fields=["status", "paid_at"])
        invoice.status = Invoice.InvoiceStatus.PAID
        invoice.paid_at = order.paid_at
        invoice.invoice_url = "https://example.com/pay"
        invoice.save(update_fields=["status", "paid_at", "invoice_url"])

        problems = check_paid_order_delivery(order)
        self.assertEqual(problems, [])
