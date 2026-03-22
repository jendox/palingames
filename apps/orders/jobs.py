from .tasks import create_invoice_task


def enqueue_invoice_creation(order_id: int) -> None:
    create_invoice_task.delay(order_id)
