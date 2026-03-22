from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from enum import IntEnum
from typing import Any

from pydantic import Field, field_validator

from libs.payments.models import PaymentModel


class ExpressPayCommandType(IntEnum):
    NEW_PAYMENT = 1
    PAYMENT_CANCELED = 2
    INVOICE_STATUS_CHANGED = 3
    ERIP_SETTLEMENT = 4
    EPOS_SETTLEMENT = 5


class ExpressPayConfig(PaymentModel):
    token: str = Field(min_length=1, max_length=32)
    secret_word: str = Field(default="")
    use_signature: bool = True
    is_test: bool = False
    timeout_seconds: float = Field(default=15.0, gt=0)


class ExpressPayErrorResponse(PaymentModel):
    code: int = Field(alias="Code")
    message: str = Field(alias="Msg")
    msg_code: int | None = Field(default=None, alias="MsgCode")


class ExpressPayErrorEnvelope(PaymentModel):
    error: ExpressPayErrorResponse = Field(alias="Error")


class ExpressPayCreateInvoiceResponse(PaymentModel):
    invoice_no: int = Field(alias="InvoiceNo", gt=0)
    invoice_url: str | None = Field(default=None, alias="InvoiceUrl")


class ExpressPayInvoiceDetailsResponse(PaymentModel):
    account_no: str = Field(alias="AccountNo")
    status: int = Field(alias="Status")
    created: str = Field(alias="Created")
    expiration: str | None = Field(default=None, alias="Expiration")
    paymented: str | None = Field(default=None, alias="Paymented")
    amount: Decimal = Field(alias="Amount", max_digits=19, decimal_places=2)
    currency: int = Field(alias="Currency")
    info: str | None = Field(default=None, alias="Info")
    invoice_url: str | None = Field(default=None, alias="InvoiceUrl")


class ExpressPayInvoiceStatusResponse(PaymentModel):
    status: int = Field(alias="Status")


class ExpressPayWebhookRequest(PaymentModel):
    data: str = Field(alias="Data")
    signature: str = Field(alias="Signature")


class ExpressPayWebhookNotification(PaymentModel):
    cmd_type: int = Field(alias="CmdType")
    status: int | None = Field(default=None, alias="Status")
    account_no: str = Field(alias="AccountNo")
    invoice_no: int | None = Field(default=None, alias="InvoiceNo")
    payment_no: int | None = Field(default=None, alias="PaymentNo")
    amount: Decimal | None = Field(default=None, alias="Amount", max_digits=19, decimal_places=2)
    currency: str | None = Field(default=None, alias="Currency")
    created_at: datetime | None = Field(default=None, alias="Created")
    service: str | None = Field(default=None, alias="Service")
    payer: str | None = Field(default=None, alias="Payer")
    address: str | None = Field(default=None, alias="Address")
    card_invoice_no: int | None = Field(default=None, alias="CardInvoiceNo")

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, value: Any) -> datetime | None:
        if value in {None, ""}:
            return None
        return datetime.strptime(str(value), "%Y%m%d%H%M%S")

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(",", ".")
        return value


class ExpressPaySettlementNotificationBase(PaymentModel):
    cmd_type: int = Field(alias="CmdType")
    service_id: int | None = Field(default=None, alias="ServiceId")
    payment_no: int | None = Field(default=None, alias="PaymentNo")
    amount: Decimal | None = Field(default=None, alias="Amount", max_digits=19, decimal_places=2)
    currency: int | None = Field(default=None, alias="Currency")
    created_at: datetime | None = Field(default=None, alias="PaymentDateTime")

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_settlement_created_at(cls, value: Any) -> datetime | None:
        if value in {None, ""}:
            return None
        return datetime.strptime(str(value), "%Y%m%d%H%M%S")

    @field_validator("amount", mode="before")
    @classmethod
    def parse_settlement_amount(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(",", ".")
        return value


def _parse_express_pay_datetime(value: Any, *formats: str) -> datetime | None:
    if value in {None, ""}:
        return None

    for fmt in formats:
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue

    raise ValueError(f"Unsupported datetime format: {value}")


ACCOUNT_NUMBER_PATTERN = re.compile(r"^[A-Z]{2}\d{6}[0-9A-Z]{8}$")


def _normalize_account_number(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    normalized = value.strip().upper()
    if ACCOUNT_NUMBER_PATTERN.fullmatch(normalized):
        return normalized

    candidate = normalized.rsplit("-", maxsplit=1)[-1]
    if ACCOUNT_NUMBER_PATTERN.fullmatch(candidate):
        return candidate

    return normalized


class ExpressPayERIPSettlementNotification(ExpressPaySettlementNotificationBase):
    account_number: str = Field(alias="AccountNumber")
    invoice_number: int | None = Field(default=None, alias="InvoiceNumber")
    money_amount: Decimal | None = Field(default=None, alias="MoneyAmmount", max_digits=19, decimal_places=2)
    transferred_money_amount: Decimal | None = Field(
        default=None,
        alias="TransferredMoneyAmount",
        max_digits=19,
        decimal_places=2,
    )

    @field_validator("money_amount", "transferred_money_amount", mode="before")
    @classmethod
    def parse_erip_amounts(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(",", ".")
        return value

    @field_validator("account_number", mode="before")
    @classmethod
    def normalize_account_number(cls, value: Any) -> Any:
        return _normalize_account_number(value)


class ExpressPayEPOSSettlementNotification(ExpressPaySettlementNotificationBase):
    account_number: str = Field(alias="AccountNumber")
    currency: str | int | None = Field(default=None, alias="Currency")
    transaction_id: str | int | None = Field(default=None, alias="TransactionId")
    erip_transaction_id: int | None = Field(default=None, alias="EripTransactionId")
    date_result_utc: datetime | None = Field(default=None, alias="DateResultUtc")
    date_verified_utc: datetime | None = Field(default=None, alias="DateVerifiedUtc")
    transfer_amount: Decimal | None = Field(default=None, alias="TransferAmount", max_digits=19, decimal_places=2)
    bank_commission: Decimal | None = Field(default=None, alias="BankComission", max_digits=19, decimal_places=2)
    erip_commission: Decimal | None = Field(default=None, alias="EripComission", max_digits=19, decimal_places=2)
    aggregator_commission: Decimal | None = Field(
        default=None,
        alias="AggregatorComission",
        max_digits=19,
        decimal_places=2,
    )
    rate: Decimal | None = Field(default=None, alias="Rate", max_digits=19, decimal_places=4)
    bank_code: int | None = Field(default=None, alias="BankCode")
    auth_type: str | None = Field(default=None, alias="AuthType")
    mem_order_date: datetime | None = Field(default=None, alias="MemOrderDate")
    mem_order_num: int | None = Field(default=None, alias="MemOrderNum")

    @field_validator("date_result_utc", mode="before")
    @classmethod
    def parse_date_result_utc(cls, value: Any) -> datetime | None:
        return _parse_express_pay_datetime(value, "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S")

    @field_validator("date_verified_utc", "mem_order_date", mode="before")
    @classmethod
    def parse_optional_epos_datetime(cls, value: Any) -> datetime | None:
        return _parse_express_pay_datetime(value, "%Y%m%d%H%M%S")

    @field_validator(
        "transfer_amount",
        "bank_commission",
        "erip_commission",
        "aggregator_commission",
        "rate",
        mode="before",
    )
    @classmethod
    def parse_transfer_amount(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(",", ".")
        return value

    @field_validator("account_number", mode="before")
    @classmethod
    def normalize_account_number(cls, value: Any) -> Any:
        return _normalize_account_number(value)
