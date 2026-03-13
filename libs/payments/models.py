from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class PaymentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InvoiceStatus(IntEnum):
    PENDING = 1
    EXPIRED = 2
    PAID = 3
    PARTIALLY_PAID = 4
    CANCELED = 5
    PAID_BY_CARD = 6
    REFUNDED = 7


class CreateInvoiceRequest(PaymentModel):
    account_no: str = Field(min_length=1, max_length=30)
    amount: Decimal = Field(gt=Decimal("0"), max_digits=19, decimal_places=2)
    currency: int = Field(default=933)
    info: str = Field(min_length=1, max_length=1024)
    expiration: str | None = Field(default=None, min_length=8, max_length=12)
    return_invoice_url: bool = True
    email_notification: str | None = Field(default=None, max_length=255)
    sms_phone: str | None = Field(default=None, max_length=13)
    surname: str | None = Field(default=None, max_length=30)
    first_name: str | None = Field(default=None, max_length=30)
    patronymic: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=30)
    street: str | None = Field(default=None, max_length=30)
    house: str | None = Field(default=None, max_length=10)
    building: str | None = Field(default=None, max_length=10)
    apartment: str | None = Field(default=None, max_length=10)
    is_name_editable: bool = False
    is_address_editable: bool = False
    is_amount_editable: bool = False
    lifetime: int | None = Field(default=None, ge=1)
    invoice_type: int | None = Field(default=None, ge=1, le=2)


class CreateInvoiceResult(PaymentModel):
    provider_invoice_no: int = Field(alias="invoice_no")
    invoice_url: HttpUrl | None = None


class GetInvoiceRequest(PaymentModel):
    invoice_no: int = Field(gt=0)
    return_invoice_url: bool = False


class InvoiceDetails(PaymentModel):
    account_no: str
    status: InvoiceStatus
    created_at: datetime
    expiration: str | None = None
    paid_at: datetime | None = None
    amount: Decimal = Field(max_digits=19, decimal_places=2)
    currency: int
    info: str | None = None
    invoice_url: HttpUrl | None = None


class InvoiceStatusRequest(PaymentModel):
    invoice_no: int = Field(gt=0)


class InvoiceStatusResult(PaymentModel):
    status: InvoiceStatus


class CancelInvoiceRequest(PaymentModel):
    invoice_no: int = Field(gt=0)


class WebhookSignatureVerification(PaymentModel):
    payload: str
    signature: str
