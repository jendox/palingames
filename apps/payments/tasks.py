import logging
import random
from datetime import timedelta
from functools import lru_cache

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.logging import log_event
from apps.orders.models import Order
from apps.payments.models import Invoice
from libs.express_pay.client import ExpressPayClient
from libs.express_pay.models import ExpressPayConfig
from libs.payments.models import CreateInvoiceRequest

logger = logging.getLogger(__name__)
TEST_INVOICE_NO_MIN = 10_000_000
TEST_INVOICE_NO_MAX = 99_999_999


def _invoice_lifetime() -> timedelta:
    return timedelta(hours=settings.EXPRESS_PAY_INVOICE_LIFETIME_HOURS)


class InvoiceCreationSkipped(Exception):
    def __init__(self, order: Order, invoice: Invoice):
        super().__init__("Invoice creation skipped because a valid invoice already exists")
        self.order = order
        self.invoice = invoice


@lru_cache(maxsize=8)
def _build_express_pay_request_client(
    token: str,
    secret_word: str,
    use_signature: bool,
    is_test: bool,
) -> ExpressPayClient:
    return ExpressPayClient(
        ExpressPayConfig(
            token=token,
            secret_word=secret_word,
            use_signature=use_signature,
            is_test=is_test,
        ),
    )


def get_express_pay_request_client() -> ExpressPayClient:
    return _build_express_pay_request_client(
        token=settings.EXPRESS_PAY_TOKEN,
        secret_word=settings.EXPRESS_PAY_REQUEST_SECRET_WORD,
        use_signature=settings.EXPRESS_PAY_USE_SIGNATURE,
        is_test=settings.EXPRESS_PAY_IS_TEST,
    )


def _build_invoice_expiration(now) -> tuple[str, timezone.datetime]:
    expires_at = now + _invoice_lifetime()
    local_expires_at = expires_at.astimezone(timezone.get_current_timezone())
    return local_expires_at.strftime("%Y%m%d%H%M"), expires_at


def _build_invoice_info(order: Order) -> str:
    return f"Оплата заказа {order.payment_account_no}"


def _build_test_invoice_no() -> str:
    return str(random.randint(TEST_INVOICE_NO_MIN, TEST_INVOICE_NO_MAX))


def _build_test_invoice_url(provider_invoice_no: str) -> str:
    return f"https://example.com/pay/{provider_invoice_no}"


def _get_locked_order_with_invoice(order_id: int) -> Order:
    order = Order.objects.select_for_update().get(pk=order_id)
    order._prefetched_objects_cache = getattr(order, "_prefetched_objects_cache", {})
    order._state.fields_cache["invoice"] = Invoice.objects.filter(order=order).first()
    return order


def _get_locked_order_for_invoice_creation(order_id: int) -> Order:
    order = _get_locked_order_with_invoice(order_id)
    invoice = getattr(order, "invoice", None)
    should_skip = bool(invoice and invoice.provider_invoice_no and invoice.status in {
        Invoice.InvoiceStatus.PENDING,
        Invoice.InvoiceStatus.PAID,
    })
    if should_skip:
        raise InvoiceCreationSkipped(order=order, invoice=invoice)
    return order


@shared_task(bind=True, autoretry_for=(), retry_backoff=False)
def create_invoice_task(self, order_id: int) -> None:
    try:
        with transaction.atomic():
            order = _get_locked_order_for_invoice_creation(order_id)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.started",
            order_id=order.id,
            order_public_id=order.public_id,
            order_status=order.status,
        )

        expiration, expires_at = _build_invoice_expiration(timezone.now())
        response = get_express_pay_request_client().create_invoice(
            CreateInvoiceRequest(
                account_no=order.payment_account_no,
                amount=order.total_amount,
                currency=order.currency,
                info=_build_invoice_info(order),
                expiration=expiration,
                return_invoice_url=True,
                email_notification=order.email,
            ),
        )
        with transaction.atomic():
            order = _get_locked_order_for_invoice_creation(order_id)
            invoice, _ = Invoice.objects.update_or_create(
                order=order,
                defaults={
                    "provider": Invoice._meta.get_field("provider").default,
                    "provider_invoice_no": str(response.invoice_no),
                    "status": Invoice.InvoiceStatus.PENDING,
                    "invoice_url": str(response.invoice_url) if response.invoice_url else None,
                    "amount": order.total_amount,
                    "currency": order.currency,
                    "expires_at": expires_at,
                    "raw_create_response": {
                        "InvoiceNo": response.invoice_no,
                        "InvoiceUrl": str(response.invoice_url) if response.invoice_url else None,
                        "Status": Invoice.InvoiceStatus.PENDING,
                        "AccountNo": order.payment_account_no,
                        "Expiration": expiration,
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
    except InvoiceCreationSkipped as exc:
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.skipped",
            order_id=exc.order.id,
            order_public_id=exc.order.public_id,
            invoice_id=exc.invoice.id,
            provider_invoice_no=exc.invoice.provider_invoice_no,
            invoice_status=exc.invoice.status,
        )
        return
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


@shared_task(bind=True, autoretry_for=(), retry_backoff=False)
def create_test_invoice_task(self, order_id: int) -> None:
    try:
        with transaction.atomic():
            order = _get_locked_order_for_invoice_creation(order_id)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.started",
            order_id=order.id,
            order_public_id=order.public_id,
            order_status=order.status,
            mode="test",
        )

        expiration, expires_at = _build_invoice_expiration(timezone.now())
        provider_invoice_no = _build_test_invoice_no()
        invoice_url = _build_test_invoice_url(provider_invoice_no)

        with transaction.atomic():
            order = _get_locked_order_for_invoice_creation(order_id)
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
                        "Expiration": expiration,
                        "Mode": "test",
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
            mode="test",
        )
    except InvoiceCreationSkipped as exc:
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.skipped",
            order_id=exc.order.id,
            order_public_id=exc.order.public_id,
            invoice_id=exc.invoice.id,
            provider_invoice_no=exc.invoice.provider_invoice_no,
            invoice_status=exc.invoice.status,
            mode="test",
        )
        return
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "invoice.creation.failed",
            exc_info=exc,
            order_id=order_id,
            error_type=type(exc).__name__,
            mode="test",
        )
        raise


@shared_task
def sync_waiting_invoice_statuses_task() -> None:
    log_event(logger, logging.INFO, "invoice.status_sync.started")
