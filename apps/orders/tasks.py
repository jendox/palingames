from __future__ import annotations

import logging

from celery import shared_task

from apps.core.logging import log_event
from apps.orders.alerts import alert_order_delivery_problem
from apps.orders.watchdog import run_order_delivery_watchdog

logger = logging.getLogger("apps.orders")


@shared_task
def check_paid_order_delivery_watchdog_task() -> dict[str, int]:
    result = run_order_delivery_watchdog()

    alerts_sent = 0

    for problem in result.problems:
        if alert_order_delivery_problem(problem):
            alerts_sent += 1

    summary = {
        "checked_orders": result.checked_orders,
        "problems": len(result.problems),
        "alerts_sent": alerts_sent,
    }

    log_event(
        logger,
        logging.WARNING if result.problems else logging.INFO,
        "order_delivery_watchdog.completed",
        **summary,
    )

    return summary
