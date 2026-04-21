import json
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.access.models import GuestAccess, UserProductAccess
from apps.custom_games.models import CustomGameRequest
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import GUEST_ORDER_DOWNLOAD
from apps.orders.models import Order, OrderItem
from apps.payments.models import Invoice, PaymentEvent
from apps.payments.tasks import create_invoice_task, sync_waiting_invoice_statuses_task
from apps.products.models import Product
from apps.promocodes.models import PromoCode
from libs.express_pay.client import ExpressPayClient
from libs.express_pay.models import ExpressPayConfig
from libs.payments.models import CreateInvoiceResult, InvoiceStatus, InvoiceStatusResult


@override_settings(EXPRESS_PAY_USE_SIGNATURE=True, EXPRESS_PAY_WEBHOOK_SECRET_WORD="secret")
class ExpressPayNotificationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(title="Товар для оплаты", slug="paid-product", price=Decimal("25.00"))
        cls.order = Order.objects.create(
            email="guest@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        cls.invoice = Invoice.objects.create(
            order=cls.order,
            provider_invoice_no="12345678",
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url="https://example.com/pay/12345678",
            amount=Decimal("25.00"),
            currency=933,
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
        cls.notification_url = reverse("express-pay-notification")

    def setUp(self):
        self.client_helper = ExpressPayClient(
            ExpressPayConfig(token="test-token", secret_word="secret", use_signature=True, is_test=True),
        )

    def _build_request_payload(
        self,
        *,
        signature=None,
        invoice_no=12345678,
        account_no=None,
        status=3,
        payment_no=555001,
        amount="25,00",
    ):
        data = json.dumps(
            {
                "CmdType": 3,
                "Status": status,
                "AccountNo": account_no or self.order.payment_account_no,
                "InvoiceNo": invoice_no,
                "PaymentNo": payment_no,
                "Amount": amount,
                "Currency": "933",
                "Created": "20260322153000",
            },
            separators=(",", ":"),
        )
        return {
            "Data": data,
            "Signature": signature or self.client_helper._compute_raw_signature(data),
        }

    def test_notification_marks_invoice_and_order_as_paid(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.notification_url, data=self._build_request_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.InvoiceStatus.PAID)
        self.assertEqual(self.order.status, Order.OrderStatus.PAID)
        self.assertTrue(PaymentEvent.objects.filter(invoice=self.invoice, is_processed=True).exists())
        self.assertFalse(UserProductAccess.objects.exists())
        self.assertEqual(GuestAccess.objects.filter(order=self.order, product=self.product).count(), 1)

    def test_notification_creates_access_for_authenticated_user(self):
        user = get_user_model().objects.create_user(email="paid@example.com", password="test-pass-123")
        order = Order.objects.create(
            user=user,
            email=user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        invoice = Invoice.objects.create(
            order=order,
            provider_invoice_no="88887777",
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url="https://example.com/pay/88887777",
            amount=Decimal("25.00"),
            currency=933,
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
            product_image_snapshot="https://example.com/product.png",
        )

        response = self.client.post(
            self.notification_url,
            data=self._build_request_payload(
                invoice_no=int(invoice.provider_invoice_no),
                account_no=order.payment_account_no,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserProductAccess.objects.filter(user=user, product=self.product, order=order).exists())

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="http://127.0.0.1:8000",
        ORDER_REWARD_MIN_TOTAL_AMOUNT="25.00",
        ORDER_REWARD_DISCOUNT_PERCENT=10,
        ORDER_REWARD_VALID_DAYS=14,
    )
    @patch("apps.orders.reward_services.inc_order_reward_issued")
    def test_notification_issues_reward_promo_for_authenticated_order(self, inc_order_reward_issued_mock):
        user = get_user_model().objects.create_user(email="reward-auth@example.com", password="test-pass-123")
        order = Order.objects.create(
            user=user,
            email=user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        invoice = Invoice.objects.create(
            order=order,
            provider_invoice_no="88880001",
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url="https://example.com/pay/88880001",
            amount=Decimal("25.00"),
            currency=933,
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
            product_image_snapshot="https://example.com/product.png",
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.notification_url,
                data=self._build_request_payload(
                    invoice_no=int(invoice.provider_invoice_no),
                    account_no=order.payment_account_no,
                    payment_no=777701,
                ),
            )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        inc_order_reward_issued_mock.assert_called_once()
        self.assertIsNotNone(order.reward_promo_code)
        self.assertIsNotNone(order.reward_issued_at)
        self.assertIsNotNone(order.reward_email_sent_at)
        promo_code = order.reward_promo_code
        self.assertTrue(promo_code.is_reward)
        self.assertEqual(promo_code.assigned_user, user)
        self.assertEqual(promo_code.assigned_email, user.email)
        self.assertEqual(promo_code.max_redemptions_per_user, 1)
        self.assertEqual(promo_code.max_redemptions_per_email, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(promo_code.code, mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="http://127.0.0.1:8000",
        GUEST_ACCESS_EXPIRE_HOURS=24,
        GUEST_ACCESS_MAX_DOWNLOADS=3,
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
        ORDER_REWARD_MIN_TOTAL_AMOUNT="25.00",
        ORDER_REWARD_DISCOUNT_PERCENT=10,
        ORDER_REWARD_VALID_DAYS=14,
    )
    def test_notification_issues_reward_promo_for_guest_order(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.notification_url, data=self._build_request_payload(payment_no=777702))

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.reward_promo_code)
        self.assertIsNone(self.order.reward_promo_code.assigned_user)
        self.assertEqual(self.order.reward_promo_code.assigned_email, self.order.email)
        self.assertEqual(self.order.reward_promo_code.max_redemptions_per_user, None)
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="http://127.0.0.1:8000",
        ORDER_REWARD_MIN_TOTAL_AMOUNT="25.00",
        ORDER_REWARD_DISCOUNT_PERCENT=10,
        ORDER_REWARD_VALID_DAYS=14,
    )
    @patch("apps.orders.reward_services.inc_order_reward_skipped")
    def test_notification_does_not_issue_reward_below_total_amount_threshold(self, inc_order_reward_skipped_mock):
        low_product = Product.objects.create(title="Cheap", slug="cheap-paid-product", price=Decimal("24.99"))
        order = Order.objects.create(
            email="cheap@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("24.99"),
            total_amount=Decimal("24.99"),
            items_count=1,
        )
        invoice = Invoice.objects.create(
            order=order,
            provider_invoice_no="88880002",
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url="https://example.com/pay/88880002",
            amount=Decimal("24.99"),
            currency=933,
        )
        OrderItem.objects.create(
            order=order,
            product=low_product,
            title_snapshot=low_product.title,
            category_snapshot="",
            unit_price_amount=low_product.price,
            quantity=1,
            line_total_amount=low_product.price,
            product_slug_snapshot=low_product.slug,
            product_image_snapshot="https://example.com/product.png",
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.notification_url,
                data=self._build_request_payload(
                    invoice_no=int(invoice.provider_invoice_no),
                    account_no=order.payment_account_no,
                    payment_no=777703,
                    amount="24,99",
                ),
            )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        inc_order_reward_skipped_mock.assert_called_once_with(reason="below_threshold")
        self.assertIsNone(order.reward_promo_code)
        self.assertEqual(PromoCode.objects.count(), 0)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="http://127.0.0.1:8000",
        ORDER_REWARD_MIN_TOTAL_AMOUNT="25.00",
        ORDER_REWARD_DISCOUNT_PERCENT=10,
        ORDER_REWARD_VALID_DAYS=14,
    )
    @patch("apps.orders.reward_services.inc_order_reward_skipped")
    def test_notification_does_not_issue_reward_when_paid_with_reward_promo(self, inc_order_reward_skipped_mock):
        reward_promo = PromoCode.objects.create(
            code="RWDPROMO",
            discount_percent=10,
            is_reward=True,
            assigned_email="used-reward@example.com",
        )
        order = Order.objects.create(
            email="used-reward@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("30.00"),
            total_amount=Decimal("27.00"),
            items_count=1,
            promo_code=reward_promo,
            promo_code_snapshot=reward_promo.code,
            discount_percent_snapshot=10,
            promo_eligible_amount=Decimal("30.00"),
            discount_amount=Decimal("3.00"),
        )
        invoice = Invoice.objects.create(
            order=order,
            provider_invoice_no="88880003",
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url="https://example.com/pay/88880003",
            amount=Decimal("27.00"),
            currency=933,
        )
        rewarded_product = Product.objects.create(
            title="Reward paid",
            slug="reward-paid-product",
            price=Decimal("30.00"),
        )
        OrderItem.objects.create(
            order=order,
            product=rewarded_product,
            title_snapshot=rewarded_product.title,
            category_snapshot="",
            unit_price_amount=rewarded_product.price,
            quantity=1,
            line_total_amount=rewarded_product.price,
            promo_eligible=True,
            discount_amount=Decimal("3.00"),
            discounted_line_total_amount=Decimal("27.00"),
            product_slug_snapshot=rewarded_product.slug,
            product_image_snapshot="https://example.com/product.png",
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.notification_url,
                data=self._build_request_payload(
                    invoice_no=int(invoice.provider_invoice_no),
                    account_no=order.payment_account_no,
                    payment_no=777704,
                    amount="27,00",
                ),
            )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        inc_order_reward_skipped_mock.assert_called_once_with(reason="reward_promo_used")
        self.assertIsNone(order.reward_promo_code)
        self.assertEqual(PromoCode.objects.filter(is_reward=True).count(), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="http://127.0.0.1:8000",
        GUEST_ACCESS_EXPIRE_HOURS=24,
        GUEST_ACCESS_MAX_DOWNLOADS=3,
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_notification_sends_guest_download_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.notification_url, data=self._build_request_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 2)
        subjects = {message.subject for message in mail.outbox}
        self.assertTrue(any(self.order.payment_account_no in subject for subject in subjects))
        self.assertTrue(any("промокод" in subject.lower() for subject in subjects))
        outbox = NotificationOutbox.objects.get(notification_type=GUEST_ORDER_DOWNLOAD, object_id=self.order.id)
        self.assertEqual(outbox.status, NotificationOutbox.Status.SENT)

    def test_notification_is_idempotent_for_same_notification(self):
        payload = self._build_request_payload()

        first_response = self.client.post(self.notification_url, data=payload)
        second_response = self.client.post(self.notification_url, data=payload)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(PaymentEvent.objects.count(), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="http://127.0.0.1:8000",
        GUEST_ACCESS_EXPIRE_HOURS=24,
        GUEST_ACCESS_MAX_DOWNLOADS=3,
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_notification_different_paid_event_does_not_duplicate_guest_side_effects(self):
        with self.captureOnCommitCallbacks(execute=True):
            first_response = self.client.post(self.notification_url, data=self._build_request_payload())
        guest_access = GuestAccess.objects.get(order=self.order, product=self.product)
        first_token_hash = guest_access.token_hash

        with self.captureOnCommitCallbacks(execute=True):
            second_response = self.client.post(
                self.notification_url,
                data=self._build_request_payload(payment_no=555002),
            )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(PaymentEvent.objects.count(), 2)
        self.assertEqual(GuestAccess.objects.filter(order=self.order, product=self.product).count(), 1)
        guest_access.refresh_from_db()
        self.assertEqual(guest_access.token_hash, first_token_hash)
        self.assertEqual(
            NotificationOutbox.objects.filter(notification_type=GUEST_ORDER_DOWNLOAD, object_id=self.order.id).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 2)
        subjects = [message.subject for message in mail.outbox]
        self.assertEqual(sum(self.order.payment_account_no in subject for subject in subjects), 1)
        self.assertEqual(sum("промокод" in subject.lower() for subject in subjects), 1)

    def test_notification_rejects_invalid_signature(self):
        response = self.client.post(self.notification_url, data=self._build_request_payload(signature="INVALID"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), "FAILED | Incorrect digital signature")

    def test_notification_returns_not_found_for_unknown_invoice(self):
        response = self.client.post(
            self.notification_url,
            data=self._build_request_payload(invoice_no=87654321),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content.decode(), "FAILED | Invoice not found")

    def test_notification_rejects_amount_mismatch(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.notification_url,
                data=self._build_request_payload(amount="1,00"),
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), "FAILED | Payment data mismatch")
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.InvoiceStatus.PENDING)
        self.assertEqual(self.invoice.amount, Decimal("25.00"))
        self.assertEqual(self.order.status, Order.OrderStatus.WAITING_FOR_PAYMENT)
        self.assertEqual(PaymentEvent.objects.count(), 0)
        self.assertFalse(GuestAccess.objects.exists())

    def test_notification_accepts_direct_json_payload_without_data_wrapper(self):
        payload = {
            "CmdType": 3,
            "Status": 3,
            "AccountNo": self.order.payment_account_no,
            "InvoiceNo": 12345678,
            "PaymentNo": 555001,
            "Amount": "25,00",
            "Currency": "933",
            "Created": "20260322153000",
        }

        with override_settings(EXPRESS_PAY_USE_SIGNATURE=False):
            response = self.client.post(
                self.notification_url,
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")

    def test_notification_ignores_non_status_change_cmd_type(self):
        payload = {
            "CmdType": 1,
            "AccountNo": self.order.payment_account_no,
            "InvoiceNo": 12345678,
            "PaymentNo": 555001,
            "Amount": "25,00",
            "Currency": "933",
            "Created": "20260322153000",
        }
        data = json.dumps(payload, separators=(",", ":"))

        response = self.client.post(
            self.notification_url,
            data={
                "Data": data,
                "Signature": self.client_helper._compute_raw_signature(data),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.OrderStatus.WAITING_FOR_PAYMENT)
        self.assertEqual(PaymentEvent.objects.count(), 0)

    def test_notification_ignores_unknown_cmd_type(self):
        payload = {
            "CmdType": 11,
            "AccountNo": self.order.payment_account_no,
            "InvoiceNo": 12345678,
            "PaymentNo": 555001,
            "Amount": "25,00",
            "Currency": "933",
            "Created": "20260322153000",
        }
        data = json.dumps(payload, separators=(",", ":"))

        response = self.client.post(
            self.notification_url,
            data={
                "Data": data,
                "Signature": self.client_helper._compute_raw_signature(data),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.OrderStatus.WAITING_FOR_PAYMENT)


@override_settings(EXPRESS_PAY_USE_SIGNATURE=True, EXPRESS_PAY_WEBHOOK_SECRET_WORD="secret")
class ExpressPaySettlementNotificationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(
            title="Товар для settlement",
            slug="settlement-product",
            price=Decimal("25.00"),
        )
        cls.order = Order.objects.create(
            email="guest@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        cls.invoice = Invoice.objects.create(
            order=cls.order,
            provider_invoice_no="22334455",
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url="https://example.com/pay/22334455",
            amount=Decimal("25.00"),
            currency=933,
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
        cls.settlement_url = reverse("express-pay-settlement-notification")

    def setUp(self):
        self.client_helper = ExpressPayClient(
            ExpressPayConfig(token="test-token", secret_word="secret", use_signature=True, is_test=True),
        )

    def _build_epos_payload(
        self,
        *,
        signature=None,
        account_number=None,
        payment_no=770011,
        transaction_id="txn-100500",
        amount="25,00",
    ):
        data = json.dumps(
            {
                "CmdType": 5,
                "ServiceId": 12345,
                "AccountNumber": account_number or self.order.payment_account_no,
                "PaymentNo": payment_no,
                "Amount": amount,
                "TransferAmount": "24,50",
                "Currency": 933,
                "TransactionId": transaction_id,
                "DateResultUtc": "2026-03-22T15:30:00",
                "PaymentDateTime": "20260322153000",
            },
            separators=(",", ":"),
        )
        return {
            "Data": data,
            "Signature": signature or self.client_helper._compute_raw_signature(data),
        }

    def test_settlement_notification_marks_order_and_invoice_paid(self):
        response = self.client.post(self.settlement_url, data=self._build_epos_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.InvoiceStatus.PAID)
        self.assertEqual(self.order.status, Order.OrderStatus.PAID)
        self.assertEqual(PaymentEvent.objects.filter(invoice=self.invoice, is_processed=True).count(), 1)
        self.assertFalse(UserProductAccess.objects.exists())

    def test_settlement_notification_creates_access_for_authenticated_user(self):
        user = get_user_model().objects.create_user(email="settlement@example.com", password="test-pass-123")
        order = Order.objects.create(
            user=user,
            email=user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        Invoice.objects.create(
            order=order,
            provider_invoice_no="33445566",
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url="https://example.com/pay/33445566",
            amount=Decimal("25.00"),
            currency=933,
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
            product_image_snapshot="https://example.com/product.png",
        )

        response = self.client.post(
            self.settlement_url,
            data=self._build_epos_payload(account_number=order.payment_account_no),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserProductAccess.objects.filter(user=user, product=self.product, order=order).exists())

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="http://127.0.0.1:8000",
        GUEST_ACCESS_EXPIRE_HOURS=24,
        GUEST_ACCESS_MAX_DOWNLOADS=3,
        APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_settlement_different_paid_event_does_not_duplicate_guest_side_effects(self):
        with self.captureOnCommitCallbacks(execute=True):
            first_response = self.client.post(self.settlement_url, data=self._build_epos_payload())
        guest_access = GuestAccess.objects.get(order=self.order, product=self.product)
        first_token_hash = guest_access.token_hash

        with self.captureOnCommitCallbacks(execute=True):
            second_response = self.client.post(
                self.settlement_url,
                data=self._build_epos_payload(payment_no=770012, transaction_id="txn-100501"),
            )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(PaymentEvent.objects.count(), 2)
        self.assertEqual(GuestAccess.objects.filter(order=self.order, product=self.product).count(), 1)
        guest_access.refresh_from_db()
        self.assertEqual(guest_access.token_hash, first_token_hash)
        self.assertEqual(
            NotificationOutbox.objects.filter(notification_type=GUEST_ORDER_DOWNLOAD, object_id=self.order.id).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 2)
        subjects = [message.subject for message in mail.outbox]
        self.assertEqual(sum(self.order.payment_account_no in subject for subject in subjects), 1)
        self.assertEqual(sum("промокод" in subject.lower() for subject in subjects), 1)

    def test_settlement_notification_normalizes_provider_account_prefix(self):
        prefixed_account_number = f"36586-1-{self.order.payment_account_no}"

        response = self.client.post(
            self.settlement_url,
            data=self._build_epos_payload(account_number=prefixed_account_number),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.OrderStatus.PAID)

    def test_settlement_notification_accepts_request_without_signature_when_disabled(self):
        payload = self._build_epos_payload()
        payload.pop("Signature")

        with override_settings(EXPRESS_PAY_USE_SIGNATURE=False):
            response = self.client.post(self.settlement_url, data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")

    def test_settlement_notification_rejects_invalid_signature(self):
        response = self.client.post(self.settlement_url, data=self._build_epos_payload(signature="INVALID"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), "FAILED | Incorrect digital signature")

    def test_settlement_notification_returns_not_found_for_unknown_order(self):
        response = self.client.post(
            self.settlement_url,
            data=self._build_epos_payload(account_number="UNKNOWN-ACCOUNT"),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content.decode(), "FAILED | Order not found")

    def test_settlement_notification_rejects_amount_mismatch(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.settlement_url,
                data=self._build_epos_payload(amount="1,00"),
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), "FAILED | Payment data mismatch")
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.InvoiceStatus.PENDING)
        self.assertEqual(self.invoice.amount, Decimal("25.00"))
        self.assertEqual(self.order.status, Order.OrderStatus.WAITING_FOR_PAYMENT)
        self.assertEqual(PaymentEvent.objects.count(), 0)
        self.assertFalse(GuestAccess.objects.exists())

    def test_settlement_notification_ignores_erip_cmd_type_for_now(self):
        data = json.dumps(
            {
                "CmdType": 4,
                "ServiceId": 12345,
                "AccountNumber": self.order.payment_account_no,
                "InvoiceNumber": 778899,
                "PaymentNo": 770011,
                "Amount": "25,00",
                "MoneyAmmount": "25,00",
                "TransferredMoneyAmount": "24,50",
                "Currency": 933,
                "PaymentDateTime": "20260322153000",
            },
            separators=(",", ":"),
        )
        response = self.client.post(
            self.settlement_url,
            data={
                "Data": data,
                "Signature": self.client_helper._compute_raw_signature(data),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")

    def test_settlement_notification_accepts_json_wrapper_with_object_data(self):
        payload = {
            "Data": {
                "CmdType": 5,
                "ServiceId": 12345,
                "AccountNumber": self.order.payment_account_no,
                "PaymentNo": 770011,
                "Amount": "25,00",
                "TransferAmount": "24,50",
                "Currency": 933,
                "TransactionId": "txn-100500",
                "DateResultUtc": "2026-03-22T15:30:00",
                "PaymentDateTime": "20260322153000",
            },
        }

        with override_settings(EXPRESS_PAY_USE_SIGNATURE=False):
            response = self.client.post(
                self.settlement_url,
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")

    def test_settlement_notification_accepts_official_example_payload_shape(self):
        payload = {
            "CmdType": 5,
            "ServiceId": 1111,
            "Currency": "BYN",
            "Amount": "25,00",
            "TransferAmount": "24,50",
            "BankComission": "0",
            "EripComission": "0",
            "AggregatorComission": "0",
            "Rate": "1",
            "TransactionId": 3349077861,
            "EripTransactionId": 1654138,
            "DateResultUtc": "20210304132422",
            "DateVerifiedUtc": "20210305033250",
            "BankCode": 749,
            "AuthType": "MS",
            "MemOrderDate": "20210305100501",
            "MemOrderNum": 6616,
            "AccountNumber": self.order.payment_account_no,
            "PaymentNo": 770011,
            "PaymentDateTime": "20260322153000",
        }

        with override_settings(EXPRESS_PAY_USE_SIGNATURE=False):
            response = self.client.post(
                self.settlement_url,
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")


class InvoiceStatusSyncTaskTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(title="Sync product", slug="sync-product", price=Decimal("25.00"))
        self.user = get_user_model().objects.create_user(email="sync@example.com", password="test-pass-123")

    def _create_waiting_invoice(self, *, account_suffix: str, user=None) -> tuple[Order, Invoice]:
        order = Order.objects.create(
            user=user,
            email=user.email if user else "guest@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.AUTHENTICATED if user else Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
            payment_account_no=f"PG250326{account_suffix}",
        )
        invoice = Invoice.objects.create(
            order=order,
            provider_invoice_no=account_suffix,
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url=f"https://example.com/pay/{account_suffix}",
            amount=Decimal("25.00"),
            currency=933,
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
            product_image_snapshot="https://example.com/product.png",
        )
        return order, invoice

    def _create_waiting_custom_game_invoice(self, *, account_suffix: str) -> tuple[CustomGameRequest, Invoice]:
        custom_game_request = CustomGameRequest.objects.create(
            contact_name="Анна",
            contact_email="custom@example.com",
            subject="Космос",
            idea="Нужна игра про космос для детей с заданиями на внимание.",
            audience="Дети 6-8 лет",
            page_count="8",
            quoted_price=Decimal("80.00"),
            status=CustomGameRequest.Status.WAITING_FOR_PAYMENT,
            payment_account_no=f"PG250326{account_suffix}",
        )
        invoice = Invoice.objects.create(
            custom_game_request=custom_game_request,
            provider_invoice_no=account_suffix,
            status=Invoice.InvoiceStatus.PENDING,
            invoice_url=f"https://example.com/pay/{account_suffix}",
            amount=Decimal("80.00"),
            currency=933,
        )
        return custom_game_request, invoice

    @patch("apps.payments.tasks.get_express_pay_request_client")
    def test_sync_waiting_invoice_statuses_marks_paid_order_and_grants_access(self, mock_get_client):
        order, invoice = self._create_waiting_invoice(account_suffix="12345678", user=self.user)
        mock_client = Mock()
        mock_client.get_invoice_status.return_value = InvoiceStatusResult(status=InvoiceStatus.PAID)
        mock_get_client.return_value = mock_client

        with self.captureOnCommitCallbacks(execute=True):
            summary = sync_waiting_invoice_statuses_task()

        invoice.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(summary["selected"], 1)
        self.assertEqual(summary["paid"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(invoice.status, Invoice.InvoiceStatus.PAID)
        self.assertEqual(order.status, Order.OrderStatus.PAID)
        self.assertIsNotNone(invoice.last_status_check_at)
        self.assertTrue(UserProductAccess.objects.filter(user=self.user, product=self.product, order=order).exists())

    @patch("apps.payments.tasks.get_express_pay_request_client")
    def test_sync_waiting_invoice_statuses_marks_custom_game_request_delivered(self, mock_get_client):
        custom_game_request, invoice = self._create_waiting_custom_game_invoice(account_suffix="56789012")
        mock_client = Mock()
        mock_client.get_invoice_status.return_value = InvoiceStatusResult(status=InvoiceStatus.PAID)
        mock_get_client.return_value = mock_client

        summary = sync_waiting_invoice_statuses_task()

        invoice.refresh_from_db()
        custom_game_request.refresh_from_db()

        self.assertEqual(summary["selected"], 1)
        self.assertEqual(summary["paid"], 1)
        self.assertEqual(invoice.status, Invoice.InvoiceStatus.PAID)
        self.assertEqual(custom_game_request.status, CustomGameRequest.Status.DELIVERED)
        self.assertIsNotNone(custom_game_request.delivered_at)

    @patch("apps.payments.tasks.get_express_pay_request_client")
    def test_create_invoice_task_creates_custom_game_request_invoice(self, mock_get_client):
        custom_game_request = CustomGameRequest.objects.create(
            contact_name="Анна",
            contact_email="custom@example.com",
            subject="Космос",
            idea="Нужна игра про космос для детей с заданиями на внимание.",
            audience="Дети 6-8 лет",
            page_count="8",
            quoted_price=Decimal("80.00"),
            status=CustomGameRequest.Status.READY,
        )
        mock_client = Mock()
        mock_client.create_invoice.return_value = CreateInvoiceResult(
            invoice_no=87654321,
            invoice_url="https://example.com/pay/87654321",
        )
        mock_get_client.return_value = mock_client

        create_invoice_task(custom_game_request.id, "custom_game_request")

        invoice = Invoice.objects.get(custom_game_request=custom_game_request)
        custom_game_request.refresh_from_db()

        self.assertEqual(invoice.provider_invoice_no, "87654321")
        self.assertEqual(invoice.amount, Decimal("80.00"))
        self.assertEqual(custom_game_request.status, CustomGameRequest.Status.WAITING_FOR_PAYMENT)
        mock_client.create_invoice.assert_called_once()

    @patch("apps.payments.tasks.get_express_pay_request_client")
    def test_sync_waiting_invoice_statuses_marks_expired_invoice(self, mock_get_client):
        order, invoice = self._create_waiting_invoice(account_suffix="23456789")
        mock_client = Mock()
        mock_client.get_invoice_status.return_value = InvoiceStatusResult(status=InvoiceStatus.EXPIRED)
        mock_get_client.return_value = mock_client

        summary = sync_waiting_invoice_statuses_task()

        invoice.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(summary["expired"], 1)
        self.assertEqual(invoice.status, Invoice.InvoiceStatus.EXPIRED)
        self.assertEqual(order.status, Order.OrderStatus.FAILED)
        self.assertEqual(order.failure_reason, "invoice_expired")

    @patch("apps.payments.tasks.get_express_pay_request_client")
    def test_sync_waiting_invoice_statuses_continues_when_one_invoice_fails(self, mock_get_client):
        first_order, first_invoice = self._create_waiting_invoice(account_suffix="34567890")
        second_order, second_invoice = self._create_waiting_invoice(account_suffix="45678901", user=self.user)
        mock_client = Mock()
        mock_client.get_invoice_status.side_effect = [
            RuntimeError("provider unavailable"),
            InvoiceStatusResult(status=InvoiceStatus.PAID),
        ]
        mock_get_client.return_value = mock_client

        with self.captureOnCommitCallbacks(execute=True):
            summary = sync_waiting_invoice_statuses_task()

        first_invoice.refresh_from_db()
        first_order.refresh_from_db()
        second_invoice.refresh_from_db()
        second_order.refresh_from_db()

        self.assertEqual(summary["selected"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["paid"], 1)
        self.assertEqual(first_invoice.status, Invoice.InvoiceStatus.PENDING)
        self.assertEqual(first_order.status, Order.OrderStatus.WAITING_FOR_PAYMENT)
        self.assertEqual(second_invoice.status, Invoice.InvoiceStatus.PAID)
        self.assertEqual(second_order.status, Order.OrderStatus.PAID)
