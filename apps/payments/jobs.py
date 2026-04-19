import logging

from django.conf import settings

from apps.core.logging import log_event
from apps.payments.tasks import create_invoice_task, create_test_invoice_task

logger = logging.getLogger("apps.payments")


def enqueue_invoice_creation(target_id: int, payment_target: str = "order") -> None:
    log_event(
        logger,
        logging.INFO,
        "invoice.creation.enqueued",
        target_id=target_id,
        payment_target=payment_target,
    )
    if settings.EXPRESS_PAY_IS_TEST:
        create_test_invoice_task.delay(target_id, payment_target)
        return
    create_invoice_task.delay(target_id, payment_target)
