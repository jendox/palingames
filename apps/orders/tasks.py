import logging

from celery import shared_task

from apps.core.logging import log_event

from .models import Order

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(), retry_backoff=False)
def create_invoice_task(self, order_id: int) -> None:
    try:
        order = Order.objects.get(pk=order_id)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.started",
            order_public_id=order.public_id,
            order_status=order.status,
        )
        Order.objects.filter(pk=order_id, status=Order.OrderStatus.CREATED).update(status=Order.OrderStatus.PENDING)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.deferred",
            order_public_id=order.public_id,
            order_status=Order.OrderStatus.PENDING,
            reason="provider_integration_not_implemented",
        )
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "invoice.creation.failed",
            exc_info=exc,
            order_id=order_id,
            error_type=type(exc).__name__,
        )
        raise


@shared_task
def sync_waiting_invoice_statuses_task() -> None:
    log_event(logger, logging.INFO, "invoice.status_sync.started")
