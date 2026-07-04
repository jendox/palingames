from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from apps.core.logging import log_event
from apps.emails.exceptions import EmailSuppressedError
from apps.emails.models import EmailLog
from apps.emails.suppression import get_active_suppression, normalize_email
from apps.notifications.models import NotificationOutbox

logger = logging.getLogger("apps.emails.sender")


@dataclass(slots=True)
class OutboundEmail:
    recipient: str
    subject: str
    text_body: str
    html_body: str | None = None
    from_email: str | None = None
    notification_type: str = ""
    template_key: str = ""
    notification_outbox: NotificationOutbox | None = None
    metadata: dict[str, Any] | None = None
    provider: str = EmailLog.Provider.SMTP


def send_outbound_email(email: OutboundEmail) -> EmailLog:
    recipient = normalize_email(email.recipient)
    if not recipient:
        raise ValueError("Outbound email recipient is required")

    suppression = get_active_suppression(recipient)
    if suppression is not None:
        email_log = EmailLog.objects.create(
            notification_outbox=email.notification_outbox,
            recipient=recipient,
            subject=email.subject,
            template_key=email.template_key,
            notification_type=email.notification_type,
            status=EmailLog.Status.SUPPRESSED,
            provider=email.provider,
            error=f"suppressed:{suppression.reason}",
            metadata=email.metadata,
        )
        log_event(
            logger,
            logging.WARNING,
            "email.send.suppressed",
            email_log_id=email_log.id,
            recipient=recipient,
            reason=suppression.reason,
            notification_type=email.notification_type,
        )
        raise EmailSuppressedError(email=recipient, reason=suppression.reason)

    email_log = EmailLog.objects.create(
        notification_outbox=email.notification_outbox,
        recipient=recipient,
        subject=email.subject,
        template_key=email.template_key,
        notification_type=email.notification_type,
        status=EmailLog.Status.QUEUED,
        provider=email.provider,
        metadata=email.metadata,
    )

    message = EmailMultiAlternatives(
        subject=email.subject,
        body=email.text_body,
        from_email=email.from_email or settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    if email.html_body:
        message.attach_alternative(email.html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception as exc:
        email_log.status = EmailLog.Status.FAILED
        email_log.error = str(exc)[:2000]
        email_log.save(update_fields=["status", "error", "updated_at"])
        log_event(
            logger,
            logging.ERROR,
            "email.send.failed",
            exc_info=exc,
            email_log_id=email_log.id,
            recipient=recipient,
            notification_type=email.notification_type,
        )
        raise

    message_id = ""
    if message.message() is not None:
        message_id = message.message().get("Message-ID", "") or ""

    email_log.status = EmailLog.Status.SENT
    email_log.message_id = message_id[:255] if message_id else None
    email_log.sent_at = timezone.now()
    email_log.save(update_fields=["status", "message_id", "sent_at", "updated_at"])

    log_event(
        logger,
        logging.INFO,
        "email.send.sent",
        email_log_id=email_log.id,
        recipient=recipient,
        notification_type=email.notification_type,
        message_id=message_id or None,
    )
    return email_log
