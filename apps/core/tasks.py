import logging

from celery import Task, shared_task
from django.core.management import call_command

from apps.core.analytics import send_ga4_purchase_event_for_order
from apps.core.logging import log_event
from apps.core.yandex_metrica import send_yandex_purchase_event_for_order

logger = logging.getLogger("apps.core.tasks")


@shared_task(bind=True)
def clear_expired_sessions_task(self) -> None:
    log_event(
        logger,
        logging.INFO,
        "sessions.cleanup.started",
        task_id=self.request.id,
    )
    call_command("clearsessions")
    log_event(
        logger,
        logging.INFO,
        "sessions.cleanup.completed",
        task_id=self.request.id,
    )


@shared_task(bind=True)
def send_order_purchase_analytics_task(self: Task, *, order_id: int, source: str) -> None:
    ctx = {"order_id": order_id, "source": source, "task_id": self.request.id}
    log_event(
        logger,
        logging.INFO,
        "analytics.purchase.started",
        **ctx,
    )
    send_ga4_purchase_event_for_order(order_id=order_id, source=source)
    send_yandex_purchase_event_for_order(order_id=order_id, source=source)
    log_event(
        logger,
        logging.INFO,
        "analytics.purchase.completed",
        **ctx,
    )
