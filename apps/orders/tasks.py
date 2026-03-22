import logging

from celery import shared_task

from .models import Order

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(), retry_backoff=False)
def create_invoice_task(self, order_id: int) -> None:
    order = Order.objects.get(pk=order_id)
    payload = {
        "order_id": order.id,
        "payment_account_no": order.payment_account_no,
    }

    logger.info("Invoice creation task started.", extra={"order_task_payload": payload})
    Order.objects.filter(pk=order_id, status=Order.OrderStatus.CREATED).update(status=Order.OrderStatus.PENDING)


@shared_task
def sync_waiting_invoice_statuses_task() -> None:
    logger.info("Invoice status sync task started.")
