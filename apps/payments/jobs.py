import logging

from apps.core.logging import log_event
from apps.payments.tasks import create_invoice_task

logger = logging.getLogger("apps.payments")


def enqueue_invoice_creation(order_id: int) -> None:
    log_event(logger, logging.INFO, "invoice.creation.enqueued", order_id=order_id)
    create_invoice_task.delay(order_id)
