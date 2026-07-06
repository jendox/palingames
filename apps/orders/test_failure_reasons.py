from django.test import SimpleTestCase, override_settings

from apps.orders.failure_reasons import format_order_failure_reason_label


@override_settings(SUPPORT_EMAIL="support@palingames.by")
class OrderFailureReasonLabelTests(SimpleTestCase):
    def test_invoice_expired_uses_specific_message(self):
        label = format_order_failure_reason_label("invoice_expired")

        self.assertIn("Срок оплаты", label)
        self.assertNotIn("support@", label)

    def test_unknown_code_includes_support_email(self):
        label = format_order_failure_reason_label("some_future_code")

        self.assertIn("support@palingames.by", label)

    def test_empty_code_includes_support_email(self):
        label = format_order_failure_reason_label(None)

        self.assertIn("support@palingames.by", label)
