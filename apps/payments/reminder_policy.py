from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from django.conf import settings

from apps.orders.models import Order
from apps.payments.models import Invoice

DEFAULT_REMINDER_BEFORE_EXPIRY_HOURS = 2.5
DEFAULT_MIN_GAP_AFTER_FIRST_EMAIL_HOURS = 6.0


@dataclass(frozen=True, slots=True)
class PaymentReminderPolicy:
    reminder_before_expiry_hours: float = DEFAULT_REMINDER_BEFORE_EXPIRY_HOURS
    min_gap_after_first_email_hours: float = DEFAULT_MIN_GAP_AFTER_FIRST_EMAIL_HOURS

    def __post_init__(self) -> None:
        if self.reminder_before_expiry_hours <= 0:
            msg = "reminder_before_expiry_hours must be positive"
            raise ValueError(msg)
        if self.min_gap_after_first_email_hours < 0:
            msg = "min_gap_after_first_email_hours must be non-negative"
            raise ValueError(msg)

    @property
    def min_invoice_lifetime(self) -> timedelta:
        return timedelta(
            hours=self.reminder_before_expiry_hours + self.min_gap_after_first_email_hours,
        )

    @property
    def reminder_before_expiry(self) -> timedelta:
        return timedelta(hours=self.reminder_before_expiry_hours)


@dataclass(frozen=True, slots=True)
class PaymentReminderCandidate:
    target_kind: Literal["order", "custom_game_request"]
    checkout_type: Order.CheckoutType
    invoice_status: Invoice.InvoiceStatus
    created_at: datetime
    expires_at: datetime | None
    provider_invoice_no: str
    invoice_url: str | None
    recipient_email: str
    payment_email_sent_for_provider_invoice_no: str | None
    payment_reminder_sent_for_provider_invoice_no: str | None


@dataclass(frozen=True, slots=True)
class PaymentReminderDecision:
    should_send: bool
    reason: str | None = None


def payment_reminder_policy_from_settings() -> PaymentReminderPolicy:
    return PaymentReminderPolicy(
        reminder_before_expiry_hours=settings.INVOICE_PAYMENT_REMINDER_BEFORE_EXPIRY_HOURS,
        min_gap_after_first_email_hours=settings.INVOICE_PAYMENT_REMINDER_MIN_GAP_AFTER_FIRST_EMAIL_HOURS,
    )


def _skip_reason_for_scope(candidate: PaymentReminderCandidate) -> str | None:
    if candidate.target_kind != "order":
        return "unsupported_target"
    if candidate.checkout_type != Order.CheckoutType.GUEST:
        return "not_guest_checkout"
    return None


def _skip_reason_for_invoice_state(candidate: PaymentReminderCandidate) -> str | None:
    if candidate.invoice_status != Invoice.InvoiceStatus.PENDING:
        return "invoice_not_pending"
    if candidate.expires_at is None:
        return "missing_expires_at"

    provider_invoice_no = candidate.provider_invoice_no.strip()
    if not provider_invoice_no:
        return "missing_provider_invoice_no"
    if not (candidate.invoice_url or "").strip():
        return "missing_invoice_url"
    if not candidate.recipient_email.strip():
        return "missing_recipient_email"

    return None


def _skip_reason_for_delivery_state(candidate: PaymentReminderCandidate) -> str | None:
    provider_invoice_no = candidate.provider_invoice_no.strip()
    if candidate.payment_email_sent_for_provider_invoice_no != provider_invoice_no:
        return "first_email_not_sent"
    if candidate.payment_reminder_sent_for_provider_invoice_no == provider_invoice_no:
        return "reminder_already_sent"
    return None


def _skip_reason_for_timing(
    candidate: PaymentReminderCandidate,
    *,
    now: datetime,
    policy: PaymentReminderPolicy,
) -> str | None:
    assert candidate.expires_at is not None

    if now >= candidate.expires_at:
        return "expired"

    lifetime = candidate.expires_at - candidate.created_at
    if lifetime < policy.min_invoice_lifetime:
        return "lifetime_too_short"

    reminder_due_at = candidate.expires_at - policy.reminder_before_expiry
    if now < reminder_due_at:
        return "not_due_yet"

    return None


def evaluate_payment_reminder(
    candidate: PaymentReminderCandidate,
    *,
    now: datetime,
    policy: PaymentReminderPolicy | None = None,
) -> PaymentReminderDecision:
    policy = policy or payment_reminder_policy_from_settings()

    for skip_reason in (
        _skip_reason_for_scope(candidate),
        _skip_reason_for_invoice_state(candidate),
        _skip_reason_for_delivery_state(candidate),
        _skip_reason_for_timing(candidate, now=now, policy=policy),
    ):
        if skip_reason is not None:
            return PaymentReminderDecision(should_send=False, reason=skip_reason)

    return PaymentReminderDecision(should_send=True)
