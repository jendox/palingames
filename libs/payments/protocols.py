from __future__ import annotations

from typing import Protocol

from .models import (
    CancelInvoiceRequest,
    CreateInvoiceRequest,
    CreateInvoiceResult,
    GetInvoiceRequest,
    InvoiceDetails,
    InvoiceStatusRequest,
    InvoiceStatusResult,
    WebhookSignatureVerification,
)


class PaymentProvider(Protocol):
    def create_invoice(self, request: CreateInvoiceRequest) -> CreateInvoiceResult: ...

    def get_invoice(self, request: GetInvoiceRequest) -> InvoiceDetails: ...

    def get_invoice_status(self, request: InvoiceStatusRequest) -> InvoiceStatusResult: ...

    def cancel_invoice(self, request: CancelInvoiceRequest) -> None: ...

    def verify_webhook_signature(self, request: WebhookSignatureVerification) -> bool: ...
