import json
import logging
from http import HTTPStatus

from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError

from apps.core.logging import log_event
from apps.core.metrics import (
    inc_payment_webhook_failed,
    inc_payment_webhook_received,
    inc_payment_webhook_rejected,
)
from apps.orders.models import Order
from apps.payments.models import Invoice, PaymentEvent, PaymentProvider
from apps.payments.services import map_invoice_status, mark_order_paid, normalize_notification_datetime
from libs.express_pay import ExpressPaySignatureError
from libs.express_pay.models import (
    ExpressPayCommandType,
    ExpressPayEPOSSettlementNotification,
    ExpressPayERIPSettlementNotification,
    ExpressPayWebhookNotification,
)

from .helpers import (
    apply_invoice_notification,
    build_payment_event_key,
    build_settlement_event_key,
    get_parsed_webhook_payload,
    normalize_currency_code,
    parse_express_pay_payload,
    success_response,
    verify_webhook_signature,
)

logger = logging.getLogger("apps.payments")


@method_decorator(csrf_exempt, name="dispatch")
class ExpressPayNotificationView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        provider = PaymentProvider.EXPRESS_PAY.value
        log_event(
            logger,
            logging.INFO,
            "payment.notification.received",
            provider=provider,
        )

        try:
            _, payload = verify_webhook_signature(request)
            cmd_type = int(payload.get("CmdType"))
            inc_payment_webhook_received(provider=provider, cmd_type=cmd_type)
        except ExpressPaySignatureError as exc:
            inc_payment_webhook_rejected(provider=provider, reason="invalid_signature")
            log_event(
                logger,
                logging.WARNING,
                "payment.notification.rejected",
                exc_info=exc,
                provider=provider,
                reason="invalid_signature",
            )
            return HttpResponse("FAILED | Incorrect digital signature", status=HTTPStatus.FORBIDDEN)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            inc_payment_webhook_rejected(provider=provider, reason="invalid_payload")
            log_event(
                logger,
                logging.WARNING,
                "payment.notification.rejected",
                exc_info=exc,
                provider=provider,
                reason="invalid_payload",
            )
            return HttpResponse("FAILED | Invalid payload", status=HTTPStatus.BAD_REQUEST)

        if cmd_type != ExpressPayCommandType.INVOICE_STATUS_CHANGED:
            log_event(
                logger,
                logging.INFO,
                "payment.notification.ignored",
                provider=provider,
                cmd_type=cmd_type,
                reason="unsupported_cmd_type",
            )
            return success_response()

        try:
            notification = ExpressPayWebhookNotification.model_validate(payload)
            invoice = self._process_status_change(notification)
        except ValidationError as exc:
            inc_payment_webhook_rejected(provider=provider, reason="invalid_payload")
            log_event(
                logger,
                logging.WARNING,
                "payment.notification.rejected",
                exc_info=exc,
                provider=provider,
                reason="invalid_payload",
                cmd_type=cmd_type,
            )
            return HttpResponse("FAILED | Invalid payload", status=HTTPStatus.BAD_REQUEST)
        except Invoice.DoesNotExist as exc:
            inc_payment_webhook_failed(provider=provider, reason="invoice_not_found")
            log_event(
                logger,
                logging.WARNING,
                "payment.notification.failed",
                exc_info=exc,
                provider=provider,
                reason="invoice_not_found",
                cmd_type=cmd_type,
                provider_invoice_no=str(notification.invoice_no) if notification.invoice_no is not None else None,
            )
            return HttpResponse("FAILED | Invoice not found", status=HTTPStatus.NOT_FOUND)

        log_event(
            logger,
            logging.INFO,
            "payment.notification.processed",
            provider=provider,
            cmd_type=cmd_type,
            order_id=invoice.order_id,
            invoice_id=invoice.id,
            provider_invoice_no=invoice.provider_invoice_no,
            invoice_status=invoice.status,
            order_status=invoice.order.status,
        )
        return success_response()

    @transaction.atomic
    def _process_status_change(self, notification: ExpressPayWebhookNotification) -> Invoice:
        invoice = Invoice.objects.select_for_update().get(
            provider_invoice_no=str(notification.invoice_no),
            order__payment_account_no=notification.account_no,
        )
        invoice.order = Order.objects.select_for_update().get(pk=invoice.order_id)

        provider_event_key = build_payment_event_key(notification)
        payment_event, created = PaymentEvent.objects.get_or_create(
            provider_event_key=provider_event_key,
            defaults={
                "invoice": invoice,
                "provider": invoice.provider,
                "cmd_type": notification.cmd_type,
                "provider_payment_no": str(notification.payment_no) if notification.payment_no is not None else None,
                "provider_invoice_no": str(notification.invoice_no) if notification.invoice_no is not None else None,
                "provider_status_code": notification.status,
                "invoice_status": map_invoice_status(notification.status),
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
            apply_invoice_notification(invoice, notification)
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
        provider = PaymentProvider.EXPRESS_PAY.value
        log_event(
            logger,
            logging.INFO,
            "payment.settlement_notification.received",
            provider=provider,
        )

        try:
            raw_payload, _ = get_parsed_webhook_payload(request)
            cmd_type = int(raw_payload.get("CmdType"))
            inc_payment_webhook_received(provider=provider, cmd_type=cmd_type)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            inc_payment_webhook_rejected(provider=provider, reason="invalid_payload")
            log_event(
                logger,
                logging.WARNING,
                "payment.settlement_notification.rejected",
                exc_info=exc,
                provider=provider,
                reason="invalid_payload",
            )
            return HttpResponse("FAILED | Invalid payload", status=HTTPStatus.BAD_REQUEST)

        try:
            return self._handle_cmd_type(request, cmd_type)
        except ExpressPaySignatureError as exc:
            inc_payment_webhook_rejected(provider=provider, reason="invalid_signature")
            log_event(
                logger,
                logging.WARNING,
                "payment.settlement_notification.rejected",
                exc_info=exc,
                provider=provider,
                reason="invalid_signature",
                cmd_type=cmd_type,
            )
            return HttpResponse("FAILED | Incorrect digital signature", status=HTTPStatus.FORBIDDEN)
        except ValidationError as exc:
            inc_payment_webhook_rejected(provider=provider, reason="invalid_payload")
            log_event(
                logger,
                logging.WARNING,
                "payment.settlement_notification.rejected",
                exc_info=exc,
                provider=provider,
                reason="invalid_payload",
                cmd_type=cmd_type,
            )
            return HttpResponse("FAILED | Invalid payload", status=HTTPStatus.BAD_REQUEST)
        except Order.DoesNotExist as exc:
            inc_payment_webhook_failed(provider=provider, reason="order_not_found")
            log_event(
                logger,
                logging.WARNING,
                "payment.settlement_notification.failed",
                exc_info=exc,
                provider=provider,
                reason="order_not_found",
                cmd_type=cmd_type,
            )
            return HttpResponse("FAILED | Order not found", status=HTTPStatus.NOT_FOUND)

    def _handle_cmd_type(self, request: HttpRequest, cmd_type: int) -> HttpResponse:
        if cmd_type == ExpressPayCommandType.EPOS_SETTLEMENT:
            notification = parse_express_pay_payload(request, ExpressPayEPOSSettlementNotification)
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
            return success_response()

        if cmd_type == ExpressPayCommandType.ERIP_SETTLEMENT:
            parse_express_pay_payload(request, ExpressPayERIPSettlementNotification)
            log_event(
                logger,
                logging.INFO,
                "payment.settlement_notification.ignored",
                provider=PaymentProvider.EXPRESS_PAY.value,
                cmd_type=cmd_type,
                reason="erip_not_implemented",
            )
            return success_response()

        log_event(
            logger,
            logging.INFO,
            "payment.settlement_notification.ignored",
            provider=PaymentProvider.EXPRESS_PAY.value,
            cmd_type=cmd_type,
            reason="unsupported_cmd_type",
        )
        return success_response()

    @transaction.atomic
    def _process_epos_notification(self, notification: ExpressPayEPOSSettlementNotification) -> Invoice:
        order = Order.objects.select_for_update().get(
            payment_account_no=notification.account_number,
        )
        invoice = Invoice.objects.select_for_update().get(order=order)
        order.invoice = invoice
        event_at = normalize_notification_datetime(notification.date_result_utc or notification.created_at)
        provider_event_key = build_settlement_event_key(notification)

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
                "currency": normalize_currency_code(notification.currency, invoice.currency),
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
            if notification.amount is not None:
                invoice.amount = notification.amount
            invoice.currency = normalize_currency_code(notification.currency, invoice.currency)
            invoice.last_status_check_at = timezone.now()
            invoice.raw_last_status_response = payment_event.payload
            mark_order_paid(
                order,
                invoice,
                paid_at=event_at,
                source="settlement",
                persist=False,
            )
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
