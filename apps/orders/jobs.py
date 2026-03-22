import logging

from .models import Order

logger = logging.getLogger(__name__)


def enqueue_invoice_creation(order_id: int) -> None:
    order = Order.objects.get(pk=order_id)
    payload = {
        "order_id": order.id,
        "payment_account_no": order.payment_account_no,
    }

    logger.info("Invoice creation job queued.", extra={"order_job_payload": payload})
    Order.objects.filter(pk=order_id, status=Order.OrderStatus.CREATED).update(status=Order.OrderStatus.PENDING)
