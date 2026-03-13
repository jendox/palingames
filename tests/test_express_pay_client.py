from __future__ import annotations

import hashlib
import hmac
import unittest
from decimal import Decimal

from pydantic import ValidationError

from libs.express_pay.client import ExpressPayClient
from libs.express_pay.models import ExpressPayConfig, ExpressPayWebhookRequest
from libs.payments.models import CreateInvoiceRequest, WebhookSignatureVerification


class ExpressPayClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ExpressPayClient(
            ExpressPayConfig(token="test-token", secret_word="secret", use_signature=True, is_test=True),
        )

    def test_format_amount_uses_comma_separator(self) -> None:
        self.assertEqual(self.client._format_amount(Decimal("12.30")), "12,30")

    def test_create_invoice_signature_mapping_matches_docs_subset(self) -> None:
        payload = {
            "Token": "test-token",
            "AccountNo": "0113032600000000012345",
            "Amount": "12,30",
            "Currency": "933",
            "Expiration": "20260315",
            "Info": "Order payment",
            "IsNameEditable": "0",
            "IsAddressEditable": "0",
            "IsAmountEditable": "0",
            "EmailNotification": "test@example.com",
            "ReturnInvoiceUrl": "1",
            "SmsPhone": "+375291234567",
        }
        expected_payload = (
            "test-token"
            "0113032600000000012345"
            "12,30"
            "933"
            "20260315"
            "Order payment"
            "000"
            "test@example.com"
            "1"
        )
        expected_signature = hmac.new(
            b"secret",
            expected_payload.encode("utf-8"),
            hashlib.sha1,
        ).hexdigest().upper()
        signature = self.client._compute_signature(
            payload,
            mapping=[
                "token",
                "accountno",
                "amount",
                "currency",
                "expiration",
                "info",
                "surname",
                "firstname",
                "patronymic",
                "city",
                "street",
                "house",
                "building",
                "apartment",
                "isnameeditable",
                "isaddresseditable",
                "isamounteditable",
                "emailnotification",
                "returninvoiceurl",
            ],
        )
        self.assertEqual(signature, expected_signature)

    def test_verify_webhook_signature(self) -> None:
        payload = '{"CmdType":3,"Status":3,"AccountNo":"0113032600000000012345","InvoiceNo":123}'
        signature = self.client._compute_raw_signature(payload)
        result = self.client.verify_webhook_signature(
            WebhookSignatureVerification(payload=payload, signature=signature),
        )
        self.assertTrue(result)

    def test_parse_webhook_validates_signature_and_payload(self) -> None:
        payload = (
            '{"CmdType":3,"Status":3,"AccountNo":"0113032600000000012345",'
            '"InvoiceNo":123,"Amount":"12,30","Created":"20260313120000"}'
        )
        request = ExpressPayWebhookRequest(
            Data=payload,
            Signature=self.client._compute_raw_signature(payload),
        )
        notification = self.client.parse_webhook(request)
        self.assertEqual(notification.invoice_no, 123)
        self.assertEqual(notification.status, 3)

    def test_create_invoice_request_rejects_invalid_amount(self) -> None:
        with self.assertRaises(ValidationError):
            CreateInvoiceRequest(
                account_no="0113032600000000012345",
                amount=Decimal("0"),
                info="Invalid",
            )


if __name__ == "__main__":
    unittest.main()
