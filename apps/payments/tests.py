import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.orders.models import Order
from apps.payments.models import Invoice, PaymentEvent
from libs.express_pay.client import ExpressPayClient
from libs.express_pay.models import ExpressPayConfig


class ExpressPayNotificationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
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
        cls.notification_url = reverse("express-pay-notification")
        cls.client_helper = ExpressPayClient(
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
        response = self.client.post(self.notification_url, data=self._build_request_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "SUCCESS")
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.InvoiceStatus.PAID)
        self.assertEqual(self.order.status, Order.OrderStatus.PAID)
        self.assertTrue(PaymentEvent.objects.filter(invoice=self.invoice, is_processed=True).exists())

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


class ExpressPaySettlementNotificationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
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
        cls.settlement_url = reverse("express-pay-settlement-notification")
        cls.client_helper = ExpressPayClient(
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
