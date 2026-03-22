import logging
import random
import time
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.core.logging import log_event
from apps.orders.models import Order
from apps.payments.models import Invoice

logger = logging.getLogger(__name__)
TEST_INVOICE_DELAY_SECONDS = 0.35
TEST_INVOICE_NO_MIN = 10_000_000
TEST_INVOICE_NO_MAX = 99_999_999
TEST_INVOICE_LIFETIME = timedelta(hours=1)


def _generate_test_invoice_no() -> str:
    return str(random.randint(TEST_INVOICE_NO_MIN, TEST_INVOICE_NO_MAX))


def _build_test_invoice_url(provider_invoice_no: str) -> str:
    return f"https://example.com/pay/{provider_invoice_no}"


@shared_task(bind=True, autoretry_for=(), retry_backoff=False)
def create_invoice_task(self, order_id: int) -> None:
    try:
        order = Order.objects.get(pk=order_id)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.started",
            order_id=order.id,
            order_public_id=order.public_id,
            order_status=order.status,
        )
        time.sleep(TEST_INVOICE_DELAY_SECONDS)
        provider_invoice_no = _generate_test_invoice_no()
        invoice_url = _build_test_invoice_url(provider_invoice_no)
        expires_at = timezone.now() + TEST_INVOICE_LIFETIME
        invoice, _ = Invoice.objects.update_or_create(
            order=order,
            defaults={
                "provider": Invoice._meta.get_field("provider").default,
                "provider_invoice_no": provider_invoice_no,
                "status": Invoice.InvoiceStatus.PENDING,
                "invoice_url": invoice_url,
                "amount": order.total_amount,
                "currency": order.currency,
                "expires_at": expires_at,
                "raw_create_response": {
                    "InvoiceNo": provider_invoice_no,
                    "InvoiceUrl": invoice_url,
                    "Status": Invoice.InvoiceStatus.PENDING,
                    "AccountNo": order.payment_account_no,
                },
            },
        )
        Order.objects.filter(pk=order_id, status=Order.OrderStatus.CREATED).update(
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
        )
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.success",
            order_id=order.id,
            order_public_id=order.public_id,
            invoice_id=invoice.id,
            provider_invoice_no=invoice.provider_invoice_no,
            invoice_status=invoice.status,
            order_status=Order.OrderStatus.WAITING_FOR_PAYMENT,
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
