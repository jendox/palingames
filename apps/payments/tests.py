import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.access.models import GuestAccess, GuestAccessEmailOutbox, UserProductAccess
from apps.orders.models import Order, OrderItem
from apps.payments.models import Invoice, PaymentEvent
from apps.products.models import Product
from libs.express_pay.client import ExpressPayClient
from libs.express_pay.models import ExpressPayConfig


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

    def _build_request_payload(self, *, signature=None, invoice_no=12345678, account_no=None, status=3):
        data = json.dumps(
            {
                "CmdType": 3,
                "Status": status,
                "AccountNo": account_no or self.order.payment_account_no,
                "InvoiceNo": invoice_no,
                "PaymentNo": 555001,
                "Amount": "25,00",
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
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.order.payment_account_no, mail.outbox[0].subject)
        outbox = GuestAccessEmailOutbox.objects.get(order=self.order)
        self.assertEqual(outbox.status, GuestAccessEmailOutbox.GuestAccessEmailStatus.SENT)

    def test_notification_is_idempotent_for_same_notification(self):
        payload = self._build_request_payload()

        first_response = self.client.post(self.notification_url, data=payload)
        second_response = self.client.post(self.notification_url, data=payload)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(PaymentEvent.objects.count(), 1)

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

    def _build_epos_payload(self, *, signature=None, account_number=None):
        data = json.dumps(
            {
                "CmdType": 5,
                "ServiceId": 12345,
                "AccountNumber": account_number or self.order.payment_account_no,
                "PaymentNo": 770011,
                "Amount": "25,00",
                "TransferAmount": "24,50",
                "Currency": 933,
                "TransactionId": "txn-100500",
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
