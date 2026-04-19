import json
from decimal import Decimal
from functools import lru_cache
from hashlib import sha1
from http import HTTPStatus

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from apps.payments.models import Invoice
from apps.payments.services import apply_invoice_status_update
from libs.express_pay import ExpressPayClient, ExpressPaySignatureError
from libs.express_pay.models import ExpressPayConfig
from libs.payments import WebhookSignatureVerification
from libs.payments.models import InvoiceStatus

MONEY_QUANT = Decimal("0.01")


class PaymentNotificationMismatch(Exception):
    pass


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


def normalize_payment_amount(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value).quantize(MONEY_QUANT)


def get_invoice_payment_account_no(invoice: Invoice) -> str | None:
    if invoice.order_id:
        return invoice.order.payment_account_no
    if invoice.custom_game_request_id:
        return invoice.custom_game_request.payment_account_no
    return None


def get_invoice_target_amount(invoice: Invoice) -> Decimal | None:
    if invoice.order_id:
        return invoice.order.total_amount
    if invoice.custom_game_request_id:
        return invoice.custom_game_request.quoted_price
    return None


def validate_invoice_payment_account_no(invoice: Invoice, account_no: str | None) -> None:
    expected_account_no = get_invoice_payment_account_no(invoice)
    if expected_account_no and account_no != expected_account_no:
        raise PaymentNotificationMismatch("account_no")


def validate_invoice_provider_invoice_no(invoice: Invoice, invoice_no) -> None:
    if invoice_no is not None and invoice.provider_invoice_no and str(invoice_no) != str(invoice.provider_invoice_no):
        raise PaymentNotificationMismatch("invoice_no")


def validate_invoice_payment_amount(invoice: Invoice, amount, *, is_paid: bool) -> None:
    if amount is None:
        if is_paid:
            raise PaymentNotificationMismatch("amount_missing")
        return

    normalized_amount = normalize_payment_amount(amount)
    invoice_amount = normalize_payment_amount(invoice.amount)
    if normalized_amount != invoice_amount:
        raise PaymentNotificationMismatch("amount")

    target_amount = get_invoice_target_amount(invoice)
    if target_amount is not None and invoice_amount != normalize_payment_amount(target_amount):
        raise PaymentNotificationMismatch("target_amount")


def validate_invoice_payment_currency(invoice: Invoice, currency) -> None:
    if currency is not None and normalize_currency_code(currency, invoice.currency) != invoice.currency:
        raise PaymentNotificationMismatch("currency")


def validate_invoice_payment_payload(
    invoice: Invoice,
    *,
    account_no: str | None,
    invoice_no=None,
    amount=None,
    currency=None,
    provider_status: int | None = None,
) -> None:
    validate_invoice_payment_account_no(invoice, account_no)
    validate_invoice_provider_invoice_no(invoice, invoice_no)
    validate_invoice_payment_amount(invoice, amount, is_paid=provider_status == InvoiceStatus.PAID)
    validate_invoice_payment_currency(invoice, currency)


def apply_invoice_notification(invoice: Invoice, notification) -> None:
    invoice.provider_invoice_no = (
        str(notification.invoice_no) if notification.invoice_no is not None else invoice.provider_invoice_no
    )
    apply_invoice_status_update(
        invoice,
        provider_status=notification.status,
        status_payload={
            "event_at": notification.created_at,
            "amount": notification.amount,
            "raw_response": {
                "CmdType": notification.cmd_type,
                "Status": notification.status,
                "AccountNo": notification.account_no,
                "InvoiceNo": notification.invoice_no,
                "PaymentNo": notification.payment_no,
                "Amount": str(notification.amount) if notification.amount is not None else None,
                "Currency": notification.currency,
                "Created": notification.created_at.isoformat() if notification.created_at else None,
            },
        },
        source="webhook",
        persist=False,
    )
