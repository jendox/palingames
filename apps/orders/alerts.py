from __future__ import annotations

from django.conf import settings

from apps.core.alerts import send_incident_alert
from apps.orders.watchdog import OrderDeliveryProblem

ORDER_DELIVERY_INCIDENT_KEY = "orders.delivery.invariant"

_FINGERPRINT_DETAIL_KEYS = (
    "product_id",
    "invoice_id",
    "outbox_id",
)


def build_order_delivery_fingerprint(problem: OrderDeliveryProblem) -> str:
    parts = [
        ORDER_DELIVERY_INCIDENT_KEY,
        str(problem.order_id),
        problem.code,
    ]
    for key in _FINGERPRINT_DETAIL_KEYS:
        value = problem.details.get(key)
        if value is not None:
            parts.append(str(value))
    return ":".join(parts)


def alert_order_delivery_problem(problem: OrderDeliveryProblem) -> bool:
    return send_incident_alert(
        key=ORDER_DELIVERY_INCIDENT_KEY,
        title="Paid order delivery problem",
        severity=problem.severity,
        fingerprint=build_order_delivery_fingerprint(problem),
        details={
            "order_id": problem.order_id,
            "problem": problem.code,
            **problem.details,
        },
        dedupe_ttl_seconds=settings.ORDER_DELIVERY_ALERT_DEDUPE_TTL_SECONDS,
    )
