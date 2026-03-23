import json
from functools import lru_cache
from hashlib import sha1
from http import HTTPStatus

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from apps.orders.models import Order
from apps.payments.models import Invoice
from apps.payments.services import mark_order_paid
from libs.express_pay import ExpressPayClient, ExpressPaySignatureError
from libs.express_pay.models import ExpressPayConfig
from libs.payments import WebhookSignatureVerification
from libs.payments.models import InvoiceStatus


@lru_cache(maxsize=8)
def _build_express_pay_webhook_client(
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


def get_express_pay_client() -> ExpressPayClient:
    return _build_express_pay_webhook_client(
        token=settings.EXPRESS_PAY_TOKEN,
        secret_word=settings.EXPRESS_PAY_WEBHOOK_SECRET_WORD,
        use_signature=settings.EXPRESS_PAY_USE_SIGNATURE,
        is_test=settings.EXPRESS_PAY_IS_TEST,
    )


def success_response() -> HttpResponse:
    return HttpResponse("SUCCESS", content_type="text/plain", status=HTTPStatus.OK)


def normalize_raw_payload_data(data) -> str:
    if isinstance(data, str):
        return data
    if data is None:
        return "{}"
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def get_raw_webhook_payload(request: HttpRequest) -> tuple[str, str | None]:
    if request.POST:
        signature = request.POST.get("Signature")
        data = request.POST.get("Data")
        if data is not None:
            return data, signature

        direct_payload = {key: value for key, value in request.POST.items() if key != "Signature"}
        return normalize_raw_payload_data(direct_payload), signature

    body = request.body.decode("utf-8") if request.body else "{}"
    payload = json.loads(body)
    if isinstance(payload, dict):
        signature = payload.get("Signature")
        if "Data" in payload:
            return normalize_raw_payload_data(payload.get("Data")), signature

        direct_payload = {key: value for key, value in payload.items() if key != "Signature"}
        return normalize_raw_payload_data(direct_payload), signature

    return normalize_raw_payload_data(payload), None


def get_parsed_webhook_payload(request: HttpRequest) -> tuple[dict, str | None]:
    data, signature = get_raw_webhook_payload(request)
    payload = json.loads(data)
    return payload, signature


def verify_webhook_signature(request: HttpRequest) -> tuple[str, dict]:
    data, signature = get_raw_webhook_payload(request)
    if settings.EXPRESS_PAY_USE_SIGNATURE:
        if not signature:
            raise ExpressPaySignatureError("Missing Express Pay webhook signature")
        is_valid = get_express_pay_client().verify_webhook_signature(
            WebhookSignatureVerification(payload=data, signature=signature),
        )
        if not is_valid:
            raise ExpressPaySignatureError("Invalid Express Pay webhook signature")
    return data, json.loads(data)


def parse_express_pay_payload(request: HttpRequest, model_class):
    _, payload = verify_webhook_signature(request)
    return model_class.model_validate(payload)


def build_payment_event_key(notification) -> str:
    raw_key = "|".join(
        [
            str(notification.cmd_type or ""),
            str(notification.invoice_no or ""),
            str(notification.payment_no or ""),
            str(notification.status or ""),
            str(notification.account_no or ""),
            notification.created_at.isoformat() if notification.created_at else "",
        ],
    )
    return sha1(raw_key.encode("utf-8")).hexdigest()


def build_settlement_event_key(notification) -> str:
    raw_key = "|".join(
        [
            str(notification.cmd_type or ""),
            str(notification.account_number or ""),
            str(notification.payment_no or ""),
            str(getattr(notification, "transaction_id", None) or ""),
            notification.created_at.isoformat() if notification.created_at else "",
            str(notification.amount or ""),
        ],
    )
    return sha1(raw_key.encode("utf-8")).hexdigest()


def map_invoice_status(provider_status: int | None) -> str | None:
    if provider_status is None:
        return None

    mapping = {
        InvoiceStatus.PENDING: Invoice.InvoiceStatus.PENDING,
        InvoiceStatus.EXPIRED: Invoice.InvoiceStatus.EXPIRED,
        InvoiceStatus.PAID: Invoice.InvoiceStatus.PAID,
        InvoiceStatus.CANCELED: Invoice.InvoiceStatus.CANCELED,
        InvoiceStatus.REFUNDED: Invoice.InvoiceStatus.REFUNDED,
    }
    try:
        return mapping.get(InvoiceStatus(provider_status))
    except ValueError:
        return None


def normalize_currency_code(value, fallback: int) -> int:
    if value in {None, ""}:
        return fallback
    if isinstance(value, int):
        return value

    normalized = str(value).strip().upper()
    currency_mapping = {
        "BYN": 933,
        "EUR": 978,
        "USD": 840,
        "RUB": 643,
    }
    if normalized in currency_mapping:
        return currency_mapping[normalized]
    if normalized.isdigit():
        return int(normalized)
    return fallback


def normalize_notification_datetime(value):
    if value and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def apply_order_status_from_invoice_status(invoice: Invoice, normalized_status: str | None, event_at) -> None:
    if normalized_status == Invoice.InvoiceStatus.PAID:
        mark_order_paid(
            invoice.order,
            invoice,
            paid_at=event_at,
            source="webhook",
            persist=False,
        )
        return

    if normalized_status == Invoice.InvoiceStatus.CANCELED:
        invoice.cancelled_at = event_at or timezone.now()
        invoice.order.status = Order.OrderStatus.CANCELED
        invoice.order.cancelled_at = invoice.cancelled_at
        return

    if normalized_status == Invoice.InvoiceStatus.EXPIRED:
        invoice.order.status = Order.OrderStatus.FAILED
        invoice.order.failure_reason = "invoice_expired"
        return

    if normalized_status == Invoice.InvoiceStatus.PENDING:
        invoice.order.status = Order.OrderStatus.WAITING_FOR_PAYMENT


def apply_invoice_notification(invoice: Invoice, notification) -> None:
    normalized_status = map_invoice_status(notification.status)
    notification_created_at = normalize_notification_datetime(notification.created_at)
    invoice.provider_invoice_no = (
        str(notification.invoice_no) if notification.invoice_no is not None else invoice.provider_invoice_no
    )
    invoice.provider = invoice.provider or Invoice._meta.get_field("provider").default
    invoice.raw_last_status_response = {
        "CmdType": notification.cmd_type,
        "Status": notification.status,
        "AccountNo": notification.account_no,
        "InvoiceNo": notification.invoice_no,
        "PaymentNo": notification.payment_no,
        "Amount": str(notification.amount) if notification.amount is not None else None,
        "Currency": notification.currency,
        "Created": notification.created_at.isoformat() if notification.created_at else None,
    }
    invoice.last_status_check_at = timezone.now()

    if notification.amount is not None:
        invoice.amount = notification.amount
    if normalized_status is not None:
        invoice.status = normalized_status
    apply_order_status_from_invoice_status(invoice, normalized_status, notification_created_at)
