from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from libs.payments.models import (
    CancelInvoiceRequest,
    CreateInvoiceRequest,
    CreateInvoiceResult,
    GetInvoiceRequest,
    InvoiceDetails,
    InvoiceStatus,
    InvoiceStatusRequest,
    InvoiceStatusResult,
    WebhookSignatureVerification,
)
from libs.payments.protocols import PaymentProvider

from .exceptions import ExpressPayAPIError, ExpressPaySignatureError
from .models import (
    ExpressPayConfig,
    ExpressPayCreateInvoiceResponse,
    ExpressPayErrorEnvelope,
    ExpressPayInvoiceDetailsResponse,
    ExpressPayInvoiceStatusResponse,
    ExpressPayWebhookNotification,
    ExpressPayWebhookRequest,
)


class ExpressPayClient(PaymentProvider):
    def __init__(self, config: ExpressPayConfig, client: httpx.Client | None = None):
        self.config = config
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=self.config.timeout_seconds,
            headers={"Accept": "application/json"},
        )

    @property
    def base_url(self) -> str:
        host = "sandbox-api.express-pay.by" if self.config.is_test else "api.express-pay.by"
        return f"https://{host}/v1"

    def create_invoice(self, request: CreateInvoiceRequest) -> CreateInvoiceResult:
        payload = {
            "Token": self.config.token,
            "AccountNo": request.account_no,
            "Amount": self._format_amount(request.amount),
            "Currency": str(request.currency),
            "Info": request.info,
            "ReturnInvoiceUrl": "1" if request.return_invoice_url else "0",
        }
        self._set_optional(payload, "Expiration", request.expiration)
        self._set_optional(payload, "EmailNotification", request.email_notification)
        self._set_optional(payload, "SmsPhone", request.sms_phone)
        self._set_optional(payload, "Surname", request.surname)
        self._set_optional(payload, "FirstName", request.first_name)
        self._set_optional(payload, "Patronymic", request.patronymic)
        self._set_optional(payload, "City", request.city)
        self._set_optional(payload, "Street", request.street)
        self._set_optional(payload, "House", request.house)
        self._set_optional(payload, "Building", request.building)
        self._set_optional(payload, "Apartment", request.apartment)
        self._set_optional(payload, "LifeTime", request.lifetime)
        self._set_optional(payload, "InvoiceType", request.invoice_type)
        payload["IsNameEditable"] = "1" if request.is_name_editable else "0"
        payload["IsAddressEditable"] = "1" if request.is_address_editable else "0"
        payload["IsAmountEditable"] = "1" if request.is_amount_editable else "0"

        if self.config.use_signature:
            payload["signature"] = self._compute_signature(
                payload,
                mapping=[
                    "token",
                    "accountno",
                    "amount",
                    "currency",
                    "expiration",
                    "info",
                    "surname",
                    "firstname",
                    "patronymic",
                    "city",
                    "street",
                    "house",
                    "building",
                    "apartment",
                    "isnameeditable",
                    "isaddresseditable",
                    "isamounteditable",
                    "emailnotification",
                    "returninvoiceurl",
                ],
            )

        response = self._request("POST", "/invoices", payload)
        parsed = ExpressPayCreateInvoiceResponse.model_validate(response)
        return CreateInvoiceResult(
            invoice_no=parsed.invoice_no,
            invoice_url=parsed.invoice_url,
        )

    def get_invoice(self, request: GetInvoiceRequest) -> InvoiceDetails:
        params = {
            "Token": self.config.token,
            "InvoiceNo": str(request.invoice_no),
            "ReturnInvoiceUrl": "1" if request.return_invoice_url else "0",
        }
        if self.config.use_signature:
            params["signature"] = self._compute_signature(
                params,
                mapping=["token", "invoiceno", "returninvoiceurl"],
            )
        response = self._request("GET", f"/invoices/{request.invoice_no}", params)
        parsed = ExpressPayInvoiceDetailsResponse.model_validate(response)
        return InvoiceDetails(
            account_no=parsed.account_no,
            status=InvoiceStatus(parsed.status),
            created_at=datetime.strptime(parsed.created, "%Y%m%d%H%M%S"),
            expiration=parsed.expiration,
            paid_at=(
                datetime.strptime(parsed.paymented, "%Y%m%d%H%M%S")
                if parsed.paymented
                else None
            ),
            amount=parsed.amount,
            currency=parsed.currency,
            info=parsed.info,
            invoice_url=parsed.invoice_url,
        )

    def get_invoice_status(self, request: InvoiceStatusRequest) -> InvoiceStatusResult:
        params = {
            "Token": self.config.token,
            "InvoiceNo": str(request.invoice_no),
        }
        if self.config.use_signature:
            params["signature"] = self._compute_signature(
                params,
                mapping=["token", "invoiceno"],
            )
        response = self._request("GET", f"/invoices/{request.invoice_no}/status", params)
        parsed = ExpressPayInvoiceStatusResponse.model_validate(response)
        return InvoiceStatusResult(status=InvoiceStatus(parsed.status))

    def cancel_invoice(self, request: CancelInvoiceRequest) -> None:
        params = {
            "Token": self.config.token,
            "InvoiceNo": str(request.invoice_no),
        }
        if self.config.use_signature:
            params["signature"] = self._compute_signature(
                params,
                mapping=["token", "id"],
            )
        self._request("DELETE", f"/invoices/{request.invoice_no}", params)

    def verify_webhook_signature(self, request: WebhookSignatureVerification) -> bool:
        expected = self._compute_raw_signature(request.payload)
        return hmac.compare_digest(expected, request.signature.upper())

    def parse_webhook(self, request: ExpressPayWebhookRequest) -> ExpressPayWebhookNotification:
        if not self.verify_webhook_signature(
            WebhookSignatureVerification(payload=request.data, signature=request.signature),
        ):
            raise ExpressPaySignatureError("Invalid Express Pay webhook signature")
        payload = json.loads(request.data)
        return ExpressPayWebhookNotification.model_validate(payload)

    def _request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        request_params = {"token": self.config.token}
        request_kwargs: dict[str, Any] = {"params": request_params}

        if method in {"GET", "DELETE"}:
            request_kwargs["params"] = {**request_params, **params}
        else:
            request_kwargs["data"] = params

        response = self._client.request(method, path, **request_kwargs)
        response.raise_for_status()
        parsed = response.json()
        if "Error" in parsed:
            error = ExpressPayErrorEnvelope.model_validate(parsed).error
            raise ExpressPayAPIError(error.code, error.message, msg_code=error.msg_code)
        return parsed

    def close(self) -> None:
        self._client.close()

    def _compute_signature(self, payload: dict[str, Any], *, mapping: list[str]) -> str:
        normalized = {str(key).lower(): "" if value is None else str(value) for key, value in payload.items()}
        signature_payload = "".join(normalized.get(key, "") for key in mapping)
        return self._compute_raw_signature(signature_payload)

    def _compute_raw_signature(self, payload: str) -> str:
        digest = hmac.new(
            self.config.secret_word.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()
        return digest.upper()

    @staticmethod
    def _format_amount(value: Decimal) -> str:
        quantized = value.quantize(Decimal("0.01"))
        return f"{quantized:.2f}".replace(".", ",")

    @staticmethod
    def _set_optional(payload: dict[str, Any], key: str, value: Any) -> None:
        if value not in {None, ""}:
            payload[key] = str(value)
