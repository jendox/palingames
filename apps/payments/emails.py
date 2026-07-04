from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.template.loader import render_to_string

from apps.access.emails import build_absolute_url
from apps.core.logging import log_event
from apps.custom_games.models import CustomGameRequest
from apps.emails.senders import OutboundEmail, send_outbound_email
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import NotificationType
from apps.orders.models import Order
from apps.payments.models import Invoice
from apps.products.pricing import format_price

logger = logging.getLogger("apps.payments.emails")


def _build_email_context(*, invoice: Invoice, target: Order | CustomGameRequest) -> dict[str, Any]:
    context = {
        "invoice": invoice,
        "target_kind": invoice.target_kind,
        "payment_url": invoice.invoice_url,
        "amount": format_price(invoice.amount, invoice.currency),
        "expires_at": invoice.expires_at,
        "invoice_lifetime_hours": settings.EXPRESS_PAY_INVOICE_LIFETIME_HOURS,
        "site_base_url": settings.SITE_BASE_URL.rstrip("/"),
        "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
    }

    if isinstance(target, Order):
        context["order"] = target
        context["items"] = [
            {
                "title": item.title_snapshot,
                "category": item.category_snapshot,
                "price": format_price(item.line_total_amount, target.currency),
            }
            for item in target.items.all()
        ]
    else:
        context["request"] = target

    return context


def send_invoice_created_user_email(
    *,
    invoice: Invoice,
    notification_outbox: NotificationOutbox | None = None,
) -> None:
    invoice = (
        Invoice.objects.select_related("order", "custom_game_request")
        .prefetch_related("order__items")
        .get(pk=invoice.pk)
    )
    target = invoice.target
    if target is None:
        log_event(
            logger,
            logging.WARNING,
            "invoice.created_user_email.skipped",
            invoice_id=invoice.id,
            reason="missing_target",
        )
        return

    if isinstance(target, Order):
        recipient = target.email
        subject = f"Оплата заказа {target.payment_account_no}"
    else:
        recipient = target.contact_email
        subject = f"Оплата игры на заказ {target.payment_account_no}"

    if not recipient:
        log_event(
            logger,
            logging.WARNING,
            "invoice.created_user_email.skipped",
            invoice_id=invoice.id,
            reason="empty_recipient",
        )
        return

    if not invoice.invoice_url:
        log_event(
            logger,
            logging.WARNING,
            "invoice.created_user_email.skipped",
            invoice_id=invoice.id,
            reason="missing_invoice_url",
        )
        return

    context = _build_email_context(invoice=invoice, target=target)
    text_body = render_to_string("payments/email/invoice_created_user.txt", context)
    html_body = render_to_string("payments/email/invoice_created_user.html", context)

    send_outbound_email(
        OutboundEmail(
            recipient=recipient,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            notification_type=NotificationType.INVOICE_CREATED_USER,
            template_key="payments/email/invoice_created_user",
            notification_outbox=notification_outbox,
            metadata={
                "invoice_id": invoice.id,
                "provider_invoice_no": invoice.provider_invoice_no,
                "payment_url": invoice.invoice_url,
                "target_kind": invoice.target_kind,
            },
        ),
    )

    Invoice.objects.filter(pk=invoice.pk).update(
        payment_email_sent_for_provider_invoice_no=invoice.provider_invoice_no,
    )

    log_event(
        logger,
        logging.INFO,
        "invoice.created_user_email.sent",
        invoice_id=invoice.id,
        provider_invoice_no=invoice.provider_invoice_no,
        recipient=recipient,
        target_kind=invoice.target_kind,
    )
