import logging

from django.conf import settings

from apps.core.logging import log_event
from apps.payments.tasks import create_invoice_task, create_test_invoice_task

logger = logging.getLogger("apps.payments")


def enqueue_invoice_creation(order_id: int) -> None:
    log_event(logger, logging.INFO, "invoice.creation.enqueued", order_id=order_id)
    if settings.EXPRESS_PAY_IS_TEST:
        create_test_invoice_task.delay(order_id)
        return
    create_invoice_task.delay(order_id)
