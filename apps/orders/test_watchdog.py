from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.access.email_outbox import encrypt_outbox_payload
from apps.access.models import GuestAccess, UserProductAccess
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import NotificationType
from apps.orders.alerts import build_order_delivery_fingerprint
from apps.orders.models import Order, OrderItem
from apps.orders.tasks import check_paid_order_delivery_watchdog_task
from apps.orders.watchdog import (
    WATCHDOG_GRACE_PERIOD,
    WATCHDOG_LOOKBACK,
    check_paid_order_delivery,
    run_order_delivery_watchdog,
)
from apps.payments.models import Invoice
from apps.products.models import Product

WATCHDOG_READY_PAID_AT = timezone.now() - WATCHDOG_GRACE_PERIOD - timedelta(minutes=1)
ENCRYPTION_TEST_SETTINGS = {
    "APP_DATA_ENCRYPTION_KEY": "5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
}


class OrderDeliveryWatchdogTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="buyer@example.com", password="test-pass-123")
        cls.product = Product.objects.create(title="Watchdog product", slug="watchdog-product", price=Decimal("10.00"))
        cls.other_product = Product.objects.create(
            title="Watchdog product 2",
            slug="watchdog-product-2",
            price=Decimal("15.00"),
        )

    def _create_order(
        self,
        *,
        checkout_type: str,
        status: str = Order.OrderStatus.PAID,
        paid_at=WATCHDOG_READY_PAID_AT,
        user=None,
        email: str = "guest@example.com",
    ) -> Order:
        return Order.objects.create(
            email=email,
            checkout_type=checkout_type,
            status=status,
            paid_at=paid_at,
            user=user,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
            items_count=1,
        )

    def _add_order_item(self, order: Order, product: Product, *, quantity: int = 1) -> OrderItem:
        return OrderItem.objects.create(
            order=order,
            product=product,
            title_snapshot=product.title,
            category_snapshot="",
            unit_price_amount=product.price,
            quantity=quantity,
            line_total_amount=product.price * quantity,
            product_slug_snapshot=product.slug,
        )

    def _create_invoice(
        self,
        order: Order,
        *,
        status: str = Invoice.InvoiceStatus.PAID,
        paid_at=WATCHDOG_READY_PAID_AT,
    ) -> Invoice:
        return Invoice.objects.create(
            order=order,
            provider_invoice_no=f"{order.id:08d}",
            status=status,
            invoice_url=f"https://example.com/pay/{order.id:08d}",
            amount=order.total_amount,
            currency=933,
            paid_at=paid_at if status == Invoice.InvoiceStatus.PAID else None,
        )

    def _create_guest_access(self, order: Order, product: Product) -> GuestAccess:
        return GuestAccess.objects.create(
            order=order,
            product=product,
            token_hash=f"{order.id}-{product.id}".ljust(64, "0")[:64],
            email=order.email,
            expires_at=timezone.now() + timedelta(hours=24),
        )

    @override_settings(**ENCRYPTION_TEST_SETTINGS)
    def _create_guest_download_outbox(
        self,
        order: Order,
        *,
        status: str = NotificationOutbox.Status.SENT,
        attempts: int = 0,
        last_error: str | None = None,
    ) -> NotificationOutbox:
        return NotificationOutbox.objects.create(
            channel=NotificationOutbox.Channel.EMAIL,
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            recipient=order.email,
            payload_encrypted=encrypt_outbox_payload([]),
            status=status,
            attempts=attempts,
            last_error=last_error,
            content_type=ContentType.objects.get_for_model(Order),
            object_id=order.id,
        )


class CheckPaidOrderDeliveryTests(OrderDeliveryWatchdogTestBase):
    def test_healthy_authenticated_order_has_no_problems(self):
        order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(order, self.product)
        self._create_invoice(order)
        UserProductAccess.objects.create(user=self.user, product=self.product, order=order)

        problems = check_paid_order_delivery(order)

        self.assertEqual(problems, [])

    def test_authenticated_missing_access_reports_problem(self):
        order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(order, self.product)
        self._create_invoice(order)

        problems = check_paid_order_delivery(order)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].code, "missing_user_product_access")
        self.assertEqual(problems[0].details["product_id"], self.product.id)
        self.assertEqual(problems[0].details["user_id"], self.user.id)

    def test_existing_access_from_old_order_is_valid(self):
        old_order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(old_order, self.product)
        UserProductAccess.objects.create(user=self.user, product=self.product, order=old_order)

        order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(order, self.product)
        self._create_invoice(order)

        problems = check_paid_order_delivery(order)

        self.assertEqual(problems, [])

    @override_settings(**ENCRYPTION_TEST_SETTINGS)
    def test_healthy_guest_order_has_no_problems(self):
        order = self._create_order(checkout_type=Order.CheckoutType.GUEST)
        self._add_order_item(order, self.product)
        self._create_invoice(order)
        self._create_guest_access(order, self.product)
        self._create_guest_download_outbox(order)

        problems = check_paid_order_delivery(order)

        self.assertEqual(problems, [])

    def test_guest_missing_access_reports_problem(self):
        order = self._create_order(checkout_type=Order.CheckoutType.GUEST)
        self._add_order_item(order, self.product)
        self._create_invoice(order)

        problems = check_paid_order_delivery(order)

        codes = {problem.code for problem in problems}
        self.assertIn("missing_guest_access", codes)

    @override_settings(**ENCRYPTION_TEST_SETTINGS)
    def test_guest_notification_missing_reports_problem(self):
        order = self._create_order(checkout_type=Order.CheckoutType.GUEST)
        self._add_order_item(order, self.product)
        self._create_invoice(order)
        self._create_guest_access(order, self.product)

        problems = check_paid_order_delivery(order)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].code, "guest_download_notification_missing")
        self.assertEqual(problems[0].details["email"], order.email)

    @override_settings(**ENCRYPTION_TEST_SETTINGS)
    def test_guest_notification_failed_reports_problem_details(self):
        order = self._create_order(checkout_type=Order.CheckoutType.GUEST)
        self._add_order_item(order, self.product)
        self._create_invoice(order)
        self._create_guest_access(order, self.product)
        outbox = self._create_guest_download_outbox(
            order,
            status=NotificationOutbox.Status.FAILED,
            attempts=3,
            last_error="smtp timeout",
        )

        problems = check_paid_order_delivery(order)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].code, "guest_download_notification_failed")
        self.assertEqual(problems[0].details["outbox_id"], outbox.id)
        self.assertEqual(problems[0].details["attempts"], 3)
        self.assertEqual(problems[0].details["last_error"], "smtp timeout")

    def test_invoice_missing_reports_problem(self):
        order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(order, self.product)

        problems = check_paid_order_delivery(order)

        invoice_missing = next(problem for problem in problems if problem.code == "invoice_missing")
        self.assertEqual(invoice_missing.severity, "critical")

    def test_invoice_status_mismatch_reports_problem(self):
        order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(order, self.product)
        invoice = self._create_invoice(order, status=Invoice.InvoiceStatus.PENDING, paid_at=None)

        problems = check_paid_order_delivery(order)

        mismatch = next(problem for problem in problems if problem.code == "invoice_status_mismatch")
        self.assertEqual(mismatch.details["invoice_id"], invoice.id)
        self.assertEqual(mismatch.details["invoice_status"], Invoice.InvoiceStatus.PENDING)
        self.assertEqual(mismatch.details["order_status"], Order.OrderStatus.PAID)

    def test_invoice_paid_at_missing_reports_warning(self):
        order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(order, self.product)
        invoice = self._create_invoice(order, paid_at=None)

        problems = check_paid_order_delivery(order)

        warning = next(problem for problem in problems if problem.code == "invoice_paid_at_missing")
        self.assertEqual(warning.severity, "warning")
        self.assertEqual(warning.details["invoice_id"], invoice.id)


class RunOrderDeliveryWatchdogTests(OrderDeliveryWatchdogTestBase):
    def test_paid_order_with_missing_paid_at_is_checked_by_runner(self):
        order = self._create_order(
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            user=self.user,
            paid_at=None,
        )
        self._add_order_item(order, self.product)

        result = run_order_delivery_watchdog()

        self.assertGreaterEqual(result.checked_orders, 1)
        codes = {problem.code for problem in result.problems if problem.order_id == order.id}
        self.assertIn("order_paid_at_missing", codes)

    def test_grace_period_excludes_recently_paid_orders(self):
        order = self._create_order(
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            user=self.user,
            paid_at=timezone.now() - timedelta(minutes=5),
        )
        self._add_order_item(order, self.product)
        self._create_invoice(order)
        UserProductAccess.objects.create(user=self.user, product=self.product, order=order)

        result = run_order_delivery_watchdog()

        self.assertEqual(result.checked_orders, 0)
        self.assertEqual(result.problems, [])

    def test_lookback_excludes_old_paid_orders(self):
        order = self._create_order(
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            user=self.user,
            paid_at=timezone.now() - WATCHDOG_LOOKBACK - timedelta(hours=1),
        )
        self._add_order_item(order, self.product)
        self._create_invoice(order)
        UserProductAccess.objects.create(user=self.user, product=self.product, order=order)

        result = run_order_delivery_watchdog()

        self.assertEqual(result.checked_orders, 0)
        self.assertEqual(result.problems, [])


class OrderDeliveryAlertTests(TestCase):
    def test_fingerprint_distinguishes_different_product_ids(self):
        from apps.orders.watchdog import OrderDeliveryProblem

        problem_a = OrderDeliveryProblem(
            order_id=817,
            code="missing_guest_access",
            severity="critical",
            details={"product_id": 10},
        )
        problem_b = OrderDeliveryProblem(
            order_id=817,
            code="missing_guest_access",
            severity="critical",
            details={"product_id": 20},
        )

        self.assertNotEqual(
            build_order_delivery_fingerprint(problem_a),
            build_order_delivery_fingerprint(problem_b),
        )
        self.assertEqual(
            build_order_delivery_fingerprint(problem_a),
            "orders.delivery.invariant:817:missing_guest_access:10",
        )


class OrderDeliveryWatchdogTaskTests(OrderDeliveryWatchdogTestBase):
    @patch("apps.orders.tasks.alert_order_delivery_problem")
    def test_task_returns_zero_summary_for_healthy_orders(self, alert_mock):
        order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(order, self.product)
        self._create_invoice(order)
        UserProductAccess.objects.create(user=self.user, product=self.product, order=order)

        summary = check_paid_order_delivery_watchdog_task()

        self.assertEqual(summary["problems"], 0)
        self.assertEqual(summary["alerts_sent"], 0)
        alert_mock.assert_not_called()

    @patch("apps.orders.tasks.alert_order_delivery_problem", return_value=True)
    def test_task_alerts_on_detected_problems(self, alert_mock):
        order = self._create_order(checkout_type=Order.CheckoutType.AUTHENTICATED, user=self.user)
        self._add_order_item(order, self.product)

        summary = check_paid_order_delivery_watchdog_task()

        self.assertGreaterEqual(summary["problems"], 1)
        self.assertGreaterEqual(summary["alerts_sent"], 1)
        alert_mock.assert_called()
        alerted_order_ids = {call.args[0].order_id for call in alert_mock.call_args_list}
        self.assertIn(order.id, alerted_order_ids)
