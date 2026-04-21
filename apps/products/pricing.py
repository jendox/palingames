from __future__ import annotations

from decimal import Decimal

from .models import Currency


def get_currency_code(currency: int | None) -> str:
    if currency is None:
        return str(Currency(Currency.BYN).label)
    try:
        return str(Currency(currency).label)
    except ValueError:
        return str(currency)


def format_price(amount: Decimal, currency: int | None) -> str:
    return f"{amount:.2f}".replace(".", ",") + f" {get_currency_code(currency)}"
