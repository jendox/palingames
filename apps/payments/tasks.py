from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from functools import lru_cache

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.logging import log_event
from apps.core.metrics import inc_invoice_created, record_invoice_status_sync_summary
from apps.custom_games.models import CustomGameRequest
from apps.orders.models import Order
from apps.payments.alerts import record_payment_status_sync_failure_incident
from apps.payments.models import Invoice
from apps.payments.services import apply_invoice_status_update
from libs.express_pay.client import ExpressPayClient
from libs.express_pay.models import ExpressPayConfig
from libs.payments.models import CreateInvoiceRequest, InvoiceStatusRequest

logger = logging.getLogger(__name__)
TEST_INVOICE_NO_MIN = 10_000_000
TEST_INVOICE_NO_MAX = 99_999_999
INVOICE_STATUS_SYNC_DEFAULT_BATCH_SIZE = 50
INVOICE_STATUS_SYNC_DEFAULT_MIN_INTERVAL_SECONDS = 300
PAYMENT_TARGET_ORDER = "order"
PAYMENT_TARGET_CUSTOM_GAME_REQUEST = "custom_game_request"

InvoiceTarget = Order | CustomGameRequest


def _invoice_lifetime() -> timedelta:
    return timedelta(hours=settings.EXPRESS_PAY_INVOICE_LIFETIME_HOURS)


def _invoice_status_sync_batch_size() -> int:
    configured = getattr(settings, "PAYMENTS_STATUS_SYNC_BATCH_SIZE", INVOICE_STATUS_SYNC_DEFAULT_BATCH_SIZE)
    return max(int(configured), 1)


def _invoice_status_sync_min_interval() -> timedelta:
    configured = getattr(
        settings,
        "PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS",
        INVOICE_STATUS_SYNC_DEFAULT_MIN_INTERVAL_SECONDS,
    )
    return timedelta(seconds=max(int(configured), 0))


class InvoiceCreationSkipped(Exception):
    def __init__(self, target: InvoiceTarget, invoice: Invoice):
        super().__init__("Invoice creation skipped because a valid invoice already exists")
        self.target = target
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


def _build_invoice_expiration(now) -> tuple[str, datetime]:
    expires_at = now + _invoice_lifetime()
    local_expires_at = expires_at.astimezone(timezone.get_current_timezone())
    return local_expires_at.strftime("%Y%m%d%H%M"), expires_at


def _build_invoice_info(target: InvoiceTarget) -> str:
    if isinstance(target, CustomGameRequest):
        return f"Оплата игры на заказ {target.payment_account_no}"
    return f"Оплата заказа {target.payment_account_no}"


def _build_test_invoice_no() -> str:
    return str(random.randint(TEST_INVOICE_NO_MIN, TEST_INVOICE_NO_MAX))


def _build_test_invoice_url(provider_invoice_no: str) -> str:
    return f"https://example.com/pay/{provider_invoice_no}"


def _get_invoice_ids_for_status_sync(*, now, limit: int) -> list[int]:
    min_checked_at = now - _invoice_status_sync_min_interval()
    return list(
        Invoice.objects.filter(
            status=Invoice.InvoiceStatus.PENDING,
            provider_invoice_no__isnull=False,
        )
        .exclude(provider_invoice_no="")
        .filter(
            Q(last_status_check_at__isnull=True) | Q(last_status_check_at__lte=min_checked_at),
        )
        .filter(
            Q(
                order__status__in=[
                    Order.OrderStatus.CREATED,
                    Order.OrderStatus.WAITING_FOR_PAYMENT,
                ],
            )
            | Q(custom_game_request__status=CustomGameRequest.Status.WAITING_FOR_PAYMENT),
        )
        .order_by("last_status_check_at", "created_at", "id")
        .values_list("id", flat=True)[:limit],
    )


def _get_target_log_context(target: InvoiceTarget) -> dict:
    if isinstance(target, CustomGameRequest):
        return {
            "target_type": PAYMENT_TARGET_CUSTOM_GAME_REQUEST,
            "custom_game_request_id": target.id,
            "custom_game_request_public_id": str(target.public_id),
            "target_status": target.status,
        }
    return {
        "target_type": PAYMENT_TARGET_ORDER,
        "order_id": target.id,
        "order_public_id": str(target.public_id),
        "target_status": target.status,
    }


def _get_locked_target_with_invoice(target_id: int, payment_target: str) -> InvoiceTarget:
    if payment_target == PAYMENT_TARGET_CUSTOM_GAME_REQUEST:
        target = CustomGameRequest.objects.select_for_update().get(pk=target_id)
        invoice = Invoice.objects.filter(custom_game_request=target).first()
    elif payment_target == PAYMENT_TARGET_ORDER:
        target = Order.objects.select_for_update().get(pk=target_id)
        invoice = Invoice.objects.filter(order=target).first()
    else:
        raise ValueError(f"Unsupported invoice payment target: {payment_target}")

    target._prefetched_objects_cache = getattr(target, "_prefetched_objects_cache", {})
    target._state.fields_cache["invoice"] = invoice
    return target


def _get_locked_target_for_invoice_creation(target_id: int, payment_target: str) -> InvoiceTarget:
    target = _get_locked_target_with_invoice(target_id, payment_target)
    invoice: Invoice | None = getattr(target, "invoice", None)
    should_skip = bool(
        invoice
        and invoice.provider_invoice_no
        and invoice.status in {
            Invoice.InvoiceStatus.PENDING,
            Invoice.InvoiceStatus.PAID,
        },
    )
    if should_skip:
        raise InvoiceCreationSkipped(target=target, invoice=invoice)

    if isinstance(target, CustomGameRequest) and target.quoted_price is None:
        raise ValueError("Custom game request quoted_price is required to create an invoice")
    return target


def _get_target_amount(target: InvoiceTarget):
    if isinstance(target, CustomGameRequest):
        return target.quoted_price
    return target.total_amount


def _get_target_email(target: InvoiceTarget) -> str:
    if isinstance(target, CustomGameRequest):
        return target.contact_email
    return target.email


def _build_invoice_lookup(target: InvoiceTarget) -> dict:
    if isinstance(target, CustomGameRequest):
        return {"custom_game_request": target}
    return {"order": target}


def _build_invoice_defaults(target: InvoiceTarget, *, provider_invoice_no, invoice_url, expires_at, raw_response):
    return {
        "provider": Invoice._meta.get_field("provider").default,
        "provider_invoice_no": str(provider_invoice_no),
        "status": Invoice.InvoiceStatus.PENDING,
        "invoice_url": str(invoice_url) if invoice_url else None,
        "amount": _get_target_amount(target),
        "currency": target.currency,
        "expires_at": expires_at,
        "raw_create_response": raw_response,
    }


def _mark_target_waiting_for_payment(target: InvoiceTarget) -> None:
    if isinstance(target, CustomGameRequest):
        CustomGameRequest.objects.filter(
            pk=target.id,
            status__in=[
                CustomGameRequest.Status.IN_PROGRESS,
                CustomGameRequest.Status.READY,
                CustomGameRequest.Status.PAYMENT_EXPIRED,
            ],
        ).update(status=CustomGameRequest.Status.WAITING_FOR_PAYMENT)
        return

    Order.objects.filter(pk=target.id, status=Order.OrderStatus.CREATED).update(
        status=Order.OrderStatus.WAITING_FOR_PAYMENT,
    )


def _is_invoice_sync_candidate(invoice: Invoice) -> bool:
    if not invoice.provider_invoice_no or invoice.status != Invoice.InvoiceStatus.PENDING:
        return False
    if invoice.order_id:
        return invoice.order.status in {
            Order.OrderStatus.CREATED,
            Order.OrderStatus.WAITING_FOR_PAYMENT,
        }
    if invoice.custom_game_request_id:
        return invoice.custom_game_request.status == CustomGameRequest.Status.WAITING_FOR_PAYMENT
    return False


def _sync_single_waiting_invoice(invoice_id: int) -> str:
    invoice = Invoice.objects.select_related("order", "custom_game_request").get(pk=invoice_id)
    if not _is_invoice_sync_candidate(invoice):
        return "skipped"

    response = get_express_pay_request_client().get_invoice_status(
        InvoiceStatusRequest(invoice_no=int(invoice.provider_invoice_no)),
    )

    with transaction.atomic():
        invoice = (
            Invoice.objects.select_for_update(of=("self",))
            .select_related("order", "custom_game_request")
            .get(pk=invoice_id)
        )
        if not _is_invoice_sync_candidate(invoice):
            return "skipped"

        previous_invoice_status = invoice.status
        previous_target_status = invoice.target.status
        normalized_status = apply_invoice_status_update(
            invoice,
            provider_status=int(response.status),
            status_payload={
                "raw_response": {
                    "InvoiceNo": invoice.provider_invoice_no,
                    "Status": int(response.status),
                    "CheckedAt": timezone.now().isoformat(),
                    "Source": "periodic_sync",
                },
            },
            source="periodic_sync",
            persist=True,
        )

    if normalized_status is None:
        log_event(
            logger,
            logging.WARNING,
            "invoice.status_sync.unknown_status",
            invoice_id=invoice.id,
            target_type=invoice.target_kind,
            target_id=invoice.target.id,
            provider_invoice_no=invoice.provider_invoice_no,
            provider_status=int(response.status),
            previous_invoice_status=previous_invoice_status,
            previous_target_status=previous_target_status,
        )
        return "unknown"

    log_event(
        logger,
        logging.INFO,
        "invoice.status_sync.invoice_processed",
        invoice_id=invoice.id,
        target_type=invoice.target_kind,
        target_id=invoice.target.id,
        provider_invoice_no=invoice.provider_invoice_no,
        provider_status=int(response.status),
        previous_invoice_status=previous_invoice_status,
        new_invoice_status=invoice.status,
        previous_target_status=previous_target_status,
        new_target_status=invoice.target.status,
    )
    return normalized_status.lower()


@shared_task(bind=True, autoretry_for=(), retry_backoff=False)
def create_invoice_task(self, target_id: int, payment_target: str = PAYMENT_TARGET_ORDER) -> None:
    try:
        with transaction.atomic():
            target = _get_locked_target_for_invoice_creation(target_id, payment_target)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.started",
            **_get_target_log_context(target),
        )

        expiration, expires_at = _build_invoice_expiration(timezone.now())
        response = get_express_pay_request_client().create_invoice(
            CreateInvoiceRequest(
                account_no=target.payment_account_no,
                amount=_get_target_amount(target),
                currency=target.currency,
                info=_build_invoice_info(target),
                expiration=expiration,
                return_invoice_url=True,
                email_notification=_get_target_email(target),
            ),
        )
        with transaction.atomic():
            target = _get_locked_target_for_invoice_creation(target_id, payment_target)
            invoice, _ = Invoice.objects.update_or_create(
                **_build_invoice_lookup(target),
                defaults=_build_invoice_defaults(
                    target,
                    provider_invoice_no=response.invoice_no,
                    invoice_url=response.invoice_url,
                    expires_at=expires_at,
                    raw_response={
                        "InvoiceNo": response.invoice_no,
                        "InvoiceUrl": str(response.invoice_url) if response.invoice_url else None,
                        "Status": Invoice.InvoiceStatus.PENDING,
                        "AccountNo": target.payment_account_no,
                        "Expiration": expiration,
                    },
                ),
            )
            _mark_target_waiting_for_payment(target)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.success",
            invoice_id=invoice.id,
            provider_invoice_no=invoice.provider_invoice_no,
            invoice_status=invoice.status,
            **_get_target_log_context(target),
        )
        inc_invoice_created(provider=invoice.provider)
    except InvoiceCreationSkipped as exc:
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.skipped",
            invoice_id=exc.invoice.id,
            provider_invoice_no=exc.invoice.provider_invoice_no,
            invoice_status=exc.invoice.status,
            **_get_target_log_context(exc.target),
        )
        return
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "invoice.creation.failed",
            exc_info=exc,
            target_id=target_id,
            payment_target=payment_target,
            error_type=type(exc).__name__,
        )
        raise


@shared_task(bind=True, autoretry_for=(), retry_backoff=False)
def create_test_invoice_task(self, target_id: int, payment_target: str = PAYMENT_TARGET_ORDER) -> None:
    try:
        with transaction.atomic():
            target = _get_locked_target_for_invoice_creation(target_id, payment_target)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.started",
            mode="test",
            **_get_target_log_context(target),
        )

        expiration, expires_at = _build_invoice_expiration(timezone.now())
        provider_invoice_no = _build_test_invoice_no()
        invoice_url = _build_test_invoice_url(provider_invoice_no)

        with transaction.atomic():
            target = _get_locked_target_for_invoice_creation(target_id, payment_target)
            invoice, _ = Invoice.objects.update_or_create(
                **_build_invoice_lookup(target),
                defaults=_build_invoice_defaults(
                    target,
                    provider_invoice_no=provider_invoice_no,
                    invoice_url=invoice_url,
                    expires_at=expires_at,
                    raw_response={
                        "InvoiceNo": provider_invoice_no,
                        "InvoiceUrl": invoice_url,
                        "Status": Invoice.InvoiceStatus.PENDING,
                        "AccountNo": target.payment_account_no,
                        "Expiration": expiration,
                        "Mode": "test",
                    },
                ),
            )
            _mark_target_waiting_for_payment(target)
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.success",
            invoice_id=invoice.id,
            provider_invoice_no=invoice.provider_invoice_no,
            invoice_status=invoice.status,
            mode="test",
            **_get_target_log_context(target),
        )
        inc_invoice_created(provider=invoice.provider)
    except InvoiceCreationSkipped as exc:
        log_event(
            logger,
            logging.INFO,
            "invoice.creation.skipped",
            invoice_id=exc.invoice.id,
            provider_invoice_no=exc.invoice.provider_invoice_no,
            invoice_status=exc.invoice.status,
            mode="test",
            **_get_target_log_context(exc.target),
        )
        return
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "invoice.creation.failed",
            exc_info=exc,
            target_id=target_id,
            payment_target=payment_target,
            error_type=type(exc).__name__,
            mode="test",
        )
        raise


@shared_task
def sync_waiting_invoice_statuses_task() -> dict[str, int]:
    now = timezone.now()
    batch_size = _invoice_status_sync_batch_size()
    invoice_ids = _get_invoice_ids_for_status_sync(now=now, limit=batch_size)
    summary = {
        "selected": len(invoice_ids),
        "processed": 0,
        "paid": 0,
        "expired": 0,
        "canceled": 0,
        "pending": 0,
        "refunded": 0,
        "unknown": 0,
        "skipped": 0,
        "failed": 0,
    }
    log_event(
        logger,
        logging.INFO,
        "invoice.status_sync.started",
        batch_size=batch_size,
        selected=summary["selected"],
    )

    for invoice_id in invoice_ids:
        try:
            result = _sync_single_waiting_invoice(invoice_id)
        except Exception as exc:
            summary["failed"] += 1
            log_event(
                logger,
                logging.ERROR,
                "invoice.status_sync.invoice_failed",
                exc_info=exc,
                invoice_id=invoice_id,
                error_type=type(exc).__name__,
            )
            record_payment_status_sync_failure_incident(
                provider=Invoice._meta.get_field("provider").default,
                error_type=type(exc).__name__,
            )
            continue

        summary["processed"] += 1
        summary[result] += 1

    log_event(
        logger,
        logging.INFO,
        "invoice.status_sync.completed",
        **summary,
    )
    record_invoice_status_sync_summary(summary)
    return summary
