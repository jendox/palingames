"""Human-readable labels for Order.failure_reason (internal codes)."""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _

_ORDER_FAILURE_REASON_MESSAGES: dict[str, str] = {
    "invoice_expired": _(
        "Срок оплаты истёк. Оформите заказ заново.",
    ),
}

_FALLBACK_MESSAGE = _(
    "Не удалось завершить оплату. Если средства списались, напишите в поддержку: %(email)s.",
)


def format_order_failure_reason_label(code: str | None) -> str:
    if not code:
        return str(_FALLBACK_MESSAGE) % {"email": settings.SUPPORT_EMAIL}
    mapped = _ORDER_FAILURE_REASON_MESSAGES.get(code)
    if mapped is not None:
        return str(mapped)
    return str(_FALLBACK_MESSAGE) % {"email": settings.SUPPORT_EMAIL}
