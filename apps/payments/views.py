import json
import logging
from hashlib import sha1
from http import HTTPStatus

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError

from apps.core.logging import log_event
from apps.orders.models import Order
from apps.payments.models import Invoice, PaymentEvent, PaymentProvider
from libs.express_pay import ExpressPayClient, ExpressPaySignatureError
from libs.express_pay.models import (
    ExpressPayCommandType,
    ExpressPayConfig,
    ExpressPayEPOSSettlementNotification,
    ExpressPayERIPSettlementNotification,
    ExpressPayWebhookNotification,
)
from libs.payments import WebhookSignatureVerification
from libs.payments.models import InvoiceStatus

logger = logging.getLogger("apps.payments")


def _get_express_pay_client() -> ExpressPayClient:
    return ExpressPayClient(
        ExpressPayConfig(
            token=settings.EXPRESS_PAY_TOKEN,
            secret_word=settings.EXPRESS_PAY_SECRET_WORD,
            use_signature=settings.EXPRESS_PAY_USE_SIGNATURE,
            is_test=settings.EXPRESS_PAY_IS_TEST,
        ),
    )


def _success_response() -> HttpResponse:
    return HttpResponse("SUCCESS", content_type="text/plain", status=HTTPStatus.OK)


def _normalize_raw_payload_data(data) -> str:
    if isinstance(data, str):
        return data

    if data is None:
        return "{}"

    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _get_raw_webhook_payload(request: HttpRequest) -> tuple[str, str | None]:
    if request.POST:
        signature = request.POST.get("Signature")
        data = request.POST.get("Data")
        if data is not None:
            return data, signature

        direct_payload = {key: value for key, value in request.POST.items() if key != "Signature"}
        return _normalize_raw_payload_data(direct_payload), signature

    body = request.body.decode("utf-8") if request.body else "{}"
    payload = json.loads(body)
    if isinstance(payload, dict):
        signature = payload.get("Signature")
        if "Data" in payload:
            return _normalize_raw_payload_data(payload.get("Data")), signature

        direct_payload = {key: value for key, value in payload.items() if key != "Signature"}
        return _normalize_raw_payload_data(direct_payload), signature

    return _normalize_raw_payload_data(payload), None


def _get_parsed_webhook_payload(request: HttpRequest) -> tuple[dict, str | None]:
    data, signature = _get_raw_webhook_payload(request)
    payload = json.loads(data)
    return payload, signature


def _parse_express_pay_payload(request: HttpRequest, model_class):
    data, signature = _get_raw_webhook_payload(request)
    client = _get_express_pay_client()

    if settings.EXPRESS_PAY_USE_SIGNATURE:
        if not signature:
            raise ExpressPaySignatureError("Missing Express Pay webhook signature")
        is_valid = client.verify_webhook_signature(
            WebhookSignatureVerification(payload=data, signature=signature),
        )
        if not is_valid:
            raise ExpressPaySignatureError("Invalid Express Pay webhook signature")

    payload = json.loads(data)
    return model_class.model_validate(payload)


def _build_payment_event_key(notification) -> str:
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


def _build_settlement_event_key(notification) -> str:
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


def _map_invoice_status(provider_status: int | None) -> str | None:
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


def _normalize_currency_code(value, fallback: int) -> int:
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


def _normalize_notification_datetime(value):
    if value and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _apply_order_status_from_invoice_status(invoice: Invoice, normalized_status: str | None, event_at) -> None:
    if normalized_status == Invoice.InvoiceStatus.PAID:
        invoice.paid_at = event_at or timezone.now()
        invoice.cancelled_at = None
        invoice.order.status = Order.OrderStatus.PAID
        invoice.order.paid_at = invoice.paid_at
        invoice.order.cancelled_at = None
        invoice.order.failure_reason = None
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


def _apply_invoice_notification(invoice: Invoice, notification) -> None:
    normalized_status = _map_invoice_status(notification.status)
    notification_created_at = _normalize_notification_datetime(notification.created_at)
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
    _apply_order_status_from_invoice_status(invoice, normalized_status, notification_created_at)


@method_decorator(csrf_exempt, name="dispatch")
class ExpressPayNotificationView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        log_event(
            logger,
            logging.INFO,
            "payment.notification.received",
            provider=PaymentProvider.EXPRESS_PAY.value,
        )

        try:
            notification = _parse_express_pay_payload(request, ExpressPayWebhookNotification)
        except ExpressPaySignatureError as exc:
            log_event(
                logger,
                logging.WARNING,
                "payment.notification.rejected",
                exc_info=exc,
                provider=PaymentProvider.EXPRESS_PAY.value,
                reason="invalid_signature",
            )
            return HttpResponse("FAILED | Incorrect digital signature", status=HTTPStatus.FORBIDDEN)
        except ValidationError as exc:
            log_event(
                logger,
                logging.WARNING,
                "payment.notification.rejected",
                exc_info=exc,
                provider=PaymentProvider.EXPRESS_PAY.value,
                reason="invalid_payload",
            )
            return HttpResponse("FAILED | Invalid payload", status=HTTPStatus.BAD_REQUEST)

        try:
            invoice = self._process_notification(notification)
        except Invoice.DoesNotExist as exc:
            log_event(
                logger,
                logging.WARNING,
                "payment.notification.failed",
                exc_info=exc,
                provider=PaymentProvider.EXPRESS_PAY.value,
                reason="invoice_not_found",
                provider_invoice_no=str(notification.invoice_no) if notification.invoice_no is not None else None,
            )
            return HttpResponse("FAILED | Invoice not found", status=HTTPStatus.NOT_FOUND)

        log_event(
            logger,
            logging.INFO,
            "payment.notification.processed",
            provider=PaymentProvider.EXPRESS_PAY.value,
            order_id=invoice.order_id,
            invoice_id=invoice.id,
            provider_invoice_no=invoice.provider_invoice_no,
            invoice_status=invoice.status,
            order_status=invoice.order.status,
        )
        return _success_response()

    @transaction.atomic
    def _process_notification(self, notification) -> Invoice:
        invoice = Invoice.objects.select_related("order").get(
            provider_invoice_no=str(notification.invoice_no),
            order__payment_account_no=notification.account_no,
        )

        provider_event_key = _build_payment_event_key(notification)
        payment_event, created = PaymentEvent.objects.get_or_create(
            provider_event_key=provider_event_key,
            defaults={
                "invoice": invoice,
                "provider": invoice.provider,
                "cmd_type": notification.cmd_type,
                "provider_payment_no": str(notification.payment_no) if notification.payment_no is not None else None,
                "provider_invoice_no": str(notification.invoice_no) if notification.invoice_no is not None else None,
                "provider_status_code": notification.status,
                "invoice_status": _map_invoice_status(notification.status),
                "amount": notification.amount,
                "currency": (
                    int(notification.currency)
                    if notification.currency and str(notification.currency).isdigit()
                    else invoice.currency
                ),
                "payload": {
                    "CmdType": notification.cmd_type,
                    "Status": notification.status,
                    "AccountNo": notification.account_no,
                    "InvoiceNo": notification.invoice_no,
                    "PaymentNo": notification.payment_no,
                },
                "is_processed": False,
            },
        )

        if created:
            _apply_invoice_notification(invoice, notification)
            invoice.save(
                update_fields=[
                    "provider_invoice_no",
                    "provider",
                    "status",
                    "invoice_url",
                    "amount",
                    "currency",
                    "paid_at",
                    "cancelled_at",
                    "last_status_check_at",
                    "raw_last_status_response",
                    "updated_at",
                ],
            )
            invoice.order.save(
                update_fields=[
                    "status",
                    "paid_at",
                    "cancelled_at",
                    "failure_reason",
                    "updated_at",
                ],
            )
            payment_event.is_processed = True
            payment_event.processed_at = timezone.now()
            payment_event.save(update_fields=["is_processed", "processed_at", "updated_at"])

        return invoice


@method_decorator(csrf_exempt, name="dispatch")
class ExpressPaySettlementNotificationView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        log_event(
            logger,
            logging.INFO,
            "payment.settlement_notification.received",
            provider=PaymentProvider.EXPRESS_PAY.value,
        )

        try:
            raw_payload, _ = _get_parsed_webhook_payload(request)
            cmd_type = int(raw_payload.get("CmdType"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            log_event(
                logger,
                logging.WARNING,
                "payment.settlement_notification.rejected",
                exc_info=exc,
                provider=PaymentProvider.EXPRESS_PAY.value,
                reason="invalid_payload",
            )
            return HttpResponse("FAILED | Invalid payload", status=HTTPStatus.BAD_REQUEST)

        try:
            return self._handle_cmd_type(request, cmd_type)
        except ExpressPaySignatureError as exc:
            log_event(
                logger,
                logging.WARNING,
                "payment.settlement_notification.rejected",
                exc_info=exc,
                provider=PaymentProvider.EXPRESS_PAY.value,
                reason="invalid_signature",
                cmd_type=cmd_type,
            )
            return HttpResponse("FAILED | Incorrect digital signature", status=HTTPStatus.FORBIDDEN)
        except ValidationError as exc:
            log_event(
                logger,
                logging.WARNING,
                "payment.settlement_notification.rejected",
                exc_info=exc,
                provider=PaymentProvider.EXPRESS_PAY.value,
                reason="invalid_payload",
                cmd_type=cmd_type,
            )
            return HttpResponse("FAILED | Invalid payload", status=HTTPStatus.BAD_REQUEST)
        except Order.DoesNotExist as exc:
            log_event(
                logger,
                logging.WARNING,
                "payment.settlement_notification.failed",
                exc_info=exc,
                provider=PaymentProvider.EXPRESS_PAY.value,
                reason="order_not_found",
                cmd_type=cmd_type,
            )
            return HttpResponse("FAILED | Order not found", status=HTTPStatus.NOT_FOUND)

    def _handle_cmd_type(self, request: HttpRequest, cmd_type: int) -> HttpResponse:
        if cmd_type == ExpressPayCommandType.EPOS_SETTLEMENT:
            notification = _parse_express_pay_payload(request, ExpressPayEPOSSettlementNotification)
            invoice = self._process_epos_notification(notification)
            log_event(
                logger,
                logging.INFO,
                "payment.settlement_notification.processed",
                provider=PaymentProvider.EXPRESS_PAY.value,
                cmd_type=cmd_type,
                order_id=invoice.order_id,
                invoice_id=invoice.id,
                provider_invoice_no=invoice.provider_invoice_no,
                invoice_status=invoice.status,
                order_status=invoice.order.status,
            )
            return _success_response()

        if cmd_type == ExpressPayCommandType.ERIP_SETTLEMENT:
            _parse_express_pay_payload(request, ExpressPayERIPSettlementNotification)
            log_event(
                logger,
                logging.INFO,
                "payment.settlement_notification.ignored",
                provider=PaymentProvider.EXPRESS_PAY.value,
                cmd_type=cmd_type,
                reason="erip_not_implemented",
            )
            return _success_response()

        log_event(
            logger,
            logging.INFO,
            "payment.settlement_notification.ignored",
            provider=PaymentProvider.EXPRESS_PAY.value,
            cmd_type=cmd_type,
            reason="unsupported_cmd_type",
        )
        return _success_response()

    @transaction.atomic
    def _process_epos_notification(self, notification: ExpressPayEPOSSettlementNotification) -> Invoice:
        order = Order.objects.select_related("invoice").get(payment_account_no=notification.account_number)
        invoice = order.invoice
        event_at = _normalize_notification_datetime(notification.date_result_utc or notification.created_at)
        provider_event_key = _build_settlement_event_key(notification)

        payment_event, created = PaymentEvent.objects.get_or_create(
            provider_event_key=provider_event_key,
            defaults={
                "invoice": invoice,
                "provider": invoice.provider,
                "cmd_type": notification.cmd_type,
                "provider_payment_no": str(notification.payment_no) if notification.payment_no is not None else None,
                "provider_invoice_no": invoice.provider_invoice_no,
                "provider_status_code": None,
                "invoice_status": Invoice.InvoiceStatus.PAID,
                "amount": notification.amount,
                "currency": _normalize_currency_code(notification.currency, invoice.currency),
                "payload": {
                    "CmdType": notification.cmd_type,
                    "ServiceId": notification.service_id,
                    "AccountNumber": notification.account_number,
                    "PaymentNo": notification.payment_no,
                    "Amount": str(notification.amount) if notification.amount is not None else None,
                    "TransferAmount": (
                        str(notification.transfer_amount) if notification.transfer_amount is not None else None
                    ),
                    "Currency": notification.currency,
                    "TransactionId": notification.transaction_id,
                    "DateResultUtc": notification.date_result_utc.isoformat() if notification.date_result_utc else None,
                    "PaymentDateTime": notification.created_at.isoformat() if notification.created_at else None,
                },
                "is_processed": False,
            },
        )

        if created:
            invoice.status = Invoice.InvoiceStatus.PAID
            if notification.amount is not None:
                invoice.amount = notification.amount
            invoice.currency = _normalize_currency_code(notification.currency, invoice.currency)
            invoice.last_status_check_at = timezone.now()
            invoice.raw_last_status_response = payment_event.payload
            invoice.paid_at = event_at or timezone.now()
            invoice.cancelled_at = None
            invoice.save(
                update_fields=[
                    "status",
                    "amount",
                    "currency",
                    "paid_at",
                    "cancelled_at",
                    "last_status_check_at",
                    "raw_last_status_response",
                    "updated_at",
                ],
            )

            order.status = Order.OrderStatus.PAID
            order.paid_at = invoice.paid_at
            order.cancelled_at = None
            order.failure_reason = None
            order.save(
                update_fields=[
                    "status",
                    "paid_at",
                    "cancelled_at",
                    "failure_reason",
                    "updated_at",
                ],
            )

            payment_event.is_processed = True
            payment_event.processed_at = timezone.now()
            payment_event.save(update_fields=["is_processed", "processed_at", "updated_at"])

        return invoice
