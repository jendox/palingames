from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from django.test import SimpleTestCase

from apps.orders.models import Order
from apps.payments.models import Invoice
from apps.payments.reminder_policy import (
    PaymentReminderCandidate,
    PaymentReminderDecision,
    PaymentReminderPolicy,
    evaluate_payment_reminder,
)

BASE = datetime(2026, 3, 26, 12, 0, tzinfo=UTC)
POLICY = PaymentReminderPolicy(
    reminder_before_expiry_hours=2.5,
    min_gap_after_first_email_hours=6.0,
)


def _candidate(
    *,
    created_at: datetime = BASE,
    expires_at: datetime | None = BASE + timedelta(hours=24),
    checkout_type: Order.CheckoutType = Order.CheckoutType.GUEST,
    target_kind: Literal["order", "custom_game_request"] = "order",
    invoice_status: Invoice.InvoiceStatus = Invoice.InvoiceStatus.PENDING,
    provider_invoice_no: str = "11112222",
    invoice_url: str | None = "https://example.com/pay/11112222",
    payment_email_sent_for_provider_invoice_no: str | None = "11112222",
    payment_reminder_sent_for_provider_invoice_no: str | None = None,
) -> PaymentReminderCandidate:
    return PaymentReminderCandidate(
        target_kind=target_kind,
        checkout_type=checkout_type,
        invoice_status=invoice_status,
        created_at=created_at,
        expires_at=expires_at,
        provider_invoice_no=provider_invoice_no,
        invoice_url=invoice_url,
        recipient_email="guest@example.com",
        payment_email_sent_for_provider_invoice_no=payment_email_sent_for_provider_invoice_no,
        payment_reminder_sent_for_provider_invoice_no=payment_reminder_sent_for_provider_invoice_no,
    )


class PaymentReminderPolicyTests(SimpleTestCase):
    def test_min_invoice_lifetime_is_before_plus_gap(self):
        self.assertEqual(
            POLICY.min_invoice_lifetime,
            timedelta(hours=8.5),
        )


class EvaluatePaymentReminderTests(SimpleTestCase):
    def test_send_when_due_in_reminder_window(self):
        candidate = _candidate()
        now = BASE + timedelta(hours=21, minutes=30)  # за 2.5 ч до expiry

        decision = evaluate_payment_reminder(candidate, now=now, policy=POLICY)

        self.assertEqual(
            decision,
            PaymentReminderDecision(should_send=True),
        )

    def test_not_due_yet(self):
        candidate = _candidate()
        now = BASE + timedelta(hours=20)  # за 4 ч до expiry

        decision = evaluate_payment_reminder(candidate, now=now, policy=POLICY)

        self.assertEqual(decision.reason, "not_due_yet")

    def test_expired(self):
        candidate = _candidate()
        now = BASE + timedelta(hours=24)

        decision = evaluate_payment_reminder(candidate, now=now, policy=POLICY)

        self.assertEqual(decision.reason, "expired")

    def test_lifetime_too_short(self):
        candidate = _candidate(
            expires_at=BASE + timedelta(hours=6),
        )
        now = BASE + timedelta(hours=3, minutes=30)

        decision = evaluate_payment_reminder(candidate, now=now, policy=POLICY)

        self.assertEqual(decision.reason, "lifetime_too_short")

    def test_lifetime_exactly_at_threshold_allows_reminder(self):
        # lifetime = 8.5h, reminder due at 6h mark
        candidate = _candidate(
            expires_at=BASE + timedelta(hours=8, minutes=30),
        )
        now = BASE + timedelta(hours=6)

        decision = evaluate_payment_reminder(candidate, now=now, policy=POLICY)

        self.assertTrue(decision.should_send)

    def test_not_guest_checkout(self):
        candidate = _candidate(checkout_type=Order.CheckoutType.AUTHENTICATED)

        decision = evaluate_payment_reminder(
            candidate,
            now=BASE + timedelta(hours=21, minutes=30),
            policy=POLICY,
        )

        self.assertEqual(decision.reason, "not_guest_checkout")

    def test_unsupported_target(self):
        candidate = _candidate(target_kind="custom_game_request")

        decision = evaluate_payment_reminder(
            candidate,
            now=BASE + timedelta(hours=21, minutes=30),
            policy=POLICY,
        )

        self.assertEqual(decision.reason, "unsupported_target")

    def test_first_email_not_sent(self):
        candidate = _candidate(
            payment_email_sent_for_provider_invoice_no=None,
        )

        decision = evaluate_payment_reminder(
            candidate,
            now=BASE + timedelta(hours=21, minutes=30),
            policy=POLICY,
        )

        self.assertEqual(decision.reason, "first_email_not_sent")

    def test_reminder_already_sent(self):
        candidate = _candidate(
            payment_reminder_sent_for_provider_invoice_no="11112222",
        )

        decision = evaluate_payment_reminder(
            candidate,
            now=BASE + timedelta(hours=21, minutes=30),
            policy=POLICY,
        )

        self.assertEqual(decision.reason, "reminder_already_sent")

    def test_late_reminder_still_sent_before_expiry(self):
        # пропустили окно 2.5h, но инвойс ещё жив
        candidate = _candidate()
        now = BASE + timedelta(hours=23, minutes=30)

        decision = evaluate_payment_reminder(candidate, now=now, policy=POLICY)

        self.assertTrue(decision.should_send)

    def test_missing_invoice_url(self):
        candidate = _candidate(invoice_url="   ")

        decision = evaluate_payment_reminder(
            candidate,
            now=BASE + timedelta(hours=21, minutes=30),
            policy=POLICY,
        )

        self.assertEqual(decision.reason, "missing_invoice_url")
