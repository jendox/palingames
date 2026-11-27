"""On-demand Telegram report on issued invoices (amounts by creation date, Europe/Minsk).

Unrelated to the monthly NPD report in ``apps/payments/reports.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from django.db.models import Case, Count, Q, QuerySet, Sum, Value, When
from django.utils import timezone

from apps.core.logging import log_event
from apps.payments.models import Invoice
from apps.products.models import Currency

logger = logging.getLogger("apps.payments.invoice_report")

REPORT_TIMEZONE = ZoneInfo("Europe/Minsk")
NBSP = "\u00a0"
DECEMBER = 12

TARGET_ORDER = "order"
TARGET_CUSTOM_GAME_REQUEST = "custom_game_request"

MONTH_NAMES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


class ReportPeriod(StrEnum):
    TODAY = "today"
    CURRENT_MONTH = "current_month"
    LAST_MONTH = "last_month"
    ALL_TIME = "all_time"


class StatusGroup(StrEnum):
    TOTAL = "total"
    PAID = "paid"
    PENDING = "pending"
    CLOSED = "closed"  # canceled / expired / refunded


_STATUS_TO_GROUP = {
    Invoice.InvoiceStatus.PAID: StatusGroup.PAID,
    Invoice.InvoiceStatus.PENDING: StatusGroup.PENDING,
    Invoice.InvoiceStatus.CANCELED: StatusGroup.CLOSED,
    Invoice.InvoiceStatus.EXPIRED: StatusGroup.CLOSED,
    Invoice.InvoiceStatus.REFUNDED: StatusGroup.CLOSED,
}


@dataclass(frozen=True)
class PeriodRange:
    period: ReportPeriod
    start: datetime | None  # inclusive, aware (Minsk)
    end: datetime | None  # exclusive, aware (Minsk)
    title: str


@dataclass
class Bucket:
    amount: Decimal = Decimal("0.00")
    count: int = 0

    def add(self, amount: Decimal, count: int) -> None:
        self.amount += amount
        self.count += count


def _new_groups() -> dict[StatusGroup, Bucket]:
    return {group: Bucket() for group in StatusGroup}


@dataclass
class InvoiceReport:
    period: PeriodRange
    # target kind -> currency code -> status group -> bucket
    data: dict[str, dict[int, dict[StatusGroup, Bucket]]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not any(
            groups[StatusGroup.TOTAL].count for by_currency in self.data.values() for groups in by_currency.values()
        )

    def currencies(self) -> list[int]:
        found = {currency for by_currency in self.data.values() for currency in by_currency}
        return sorted(found, key=lambda code: (code != Currency.BYN, currency_code(code)))

    def section(self, kind: str | None, currency: int) -> dict[StatusGroup, Bucket]:
        """Buckets for a target kind, or the sum over all kinds when ``kind`` is None."""
        if kind is not None:
            return self.data.get(kind, {}).get(currency) or _new_groups()
        result = _new_groups()
        for by_currency in self.data.values():
            for group, bucket in (by_currency.get(currency) or {}).items():
                result[group].add(bucket.amount, bucket.count)
        return result


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=REPORT_TIMEZONE)


def _next_month_start(day: date) -> datetime:
    if day.month == DECEMBER:
        return _month_start(day.year + 1, 1)
    return _month_start(day.year, day.month + 1)


def resolve_period(period: ReportPeriod | str, *, now: datetime | None = None) -> PeriodRange:
    period = ReportPeriod(period)
    now_local = (now or timezone.now()).astimezone(REPORT_TIMEZONE)
    today = now_local.date()

    if period is ReportPeriod.TODAY:
        start = datetime(today.year, today.month, today.day, tzinfo=REPORT_TIMEZONE)
        end = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=REPORT_TIMEZONE)
        return PeriodRange(period, start, end, f"Сегодня ({today:%d.%m.%Y})")

    if period is ReportPeriod.CURRENT_MONTH:
        start = _month_start(today.year, today.month)
        end = _next_month_start(today)
        title = f"{MONTH_NAMES[today.month - 1]} {today.year} (01.{today:%m}–{today:%d.%m})"
        return PeriodRange(period, start, end, title)

    if period is ReportPeriod.LAST_MONTH:
        end = _month_start(today.year, today.month)
        last_day = (end - timedelta(days=1)).date()
        start = _month_start(last_day.year, last_day.month)
        title = f"{MONTH_NAMES[last_day.month - 1]} {last_day.year} (01.{last_day:%m}–{last_day:%d.%m})"
        return PeriodRange(period, start, end, title)

    return PeriodRange(period, None, None, "Всё время")


def currency_code(currency: int) -> str:
    try:
        return Currency(currency).label
    except ValueError:
        return str(currency)


def format_amount(amount: Decimal, currency: int) -> str:
    quantized = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, _, fraction = f"{quantized:.2f}".partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    grouped = f"{int(whole):,}".replace(",", NBSP)
    return f"{sign}{grouped},{fraction}{NBSP}{currency_code(currency)}"


def paid_percent(paid: Decimal, total: Decimal) -> int | None:
    if not total:
        return None
    return int((Decimal(paid) * 100 / Decimal(total)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _issued_invoices(period: PeriodRange) -> QuerySet[Invoice]:
    queryset = Invoice.objects.filter(provider_invoice_no__isnull=False).exclude(provider_invoice_no="")
    if period.start is not None:
        queryset = queryset.filter(created_at__gte=period.start)
    if period.end is not None:
        queryset = queryset.filter(created_at__lt=period.end)
    return queryset


def build_invoice_report(period: ReportPeriod | str, *, now: datetime | None = None) -> InvoiceReport:
    period_range = resolve_period(period, now=now)
    rows = (
        _issued_invoices(period_range)
        .annotate(
            kind=Case(
                When(Q(order__isnull=False), then=Value(TARGET_ORDER)),
                default=Value(TARGET_CUSTOM_GAME_REQUEST),
            ),
        )
        .order_by()
        .values("kind", "currency", "status")
        .annotate(total_amount=Sum("amount"), total_count=Count("id"))
    )

    report = InvoiceReport(period=period_range)
    for row in rows:
        group = _STATUS_TO_GROUP.get(row["status"])
        if group is None:
            log_event(logger, logging.WARNING, "invoice_report.unknown_status", status=row["status"])
            group = StatusGroup.CLOSED
        groups = report.data.setdefault(row["kind"], {}).setdefault(row["currency"], _new_groups())
        amount = row["total_amount"] or Decimal("0.00")
        groups[StatusGroup.TOTAL].add(amount, row["total_count"])
        groups[group].add(amount, row["total_count"])
    return report


def _format_lines(groups: dict[StatusGroup, Bucket], currency: int) -> list[str]:
    def money(bucket: Bucket) -> str:
        return f"<b>{format_amount(bucket.amount, currency)}</b>{NBSP}({bucket.count})"

    total = groups[StatusGroup.TOTAL]
    paid = groups[StatusGroup.PAID]
    percent = paid_percent(paid.amount, total.amount)
    percent_text = f" · {percent}%" if percent is not None else ""
    return [
        f"Всего: {money(total)}",
        f"✅ Оплачено: {money(paid)}{percent_text}",
        f"⏳ Ожидает оплаты: {money(groups[StatusGroup.PENDING])}",
        f"❌ Отменено / истекло / возврат: {money(groups[StatusGroup.CLOSED])}",
    ]


def format_invoice_report_html(report: InvoiceReport, *, now: datetime | None = None) -> str:
    now_local = (now or timezone.now()).astimezone(REPORT_TIMEZONE)
    header = f"<b>📊 Отчёт по счетам · {report.period.title}</b>"
    footer = f"<i>Сформировано {now_local:%d.%m.%Y %H:%M} (Минск)</i>"

    if report.is_empty:
        return f"{header}\n\nСчетов за период нет.\n\n{footer}"

    currencies = report.currencies()
    multi_currency = len(currencies) > 1
    blocks = [header]
    for title, kind in (
        ("🛒 Заказы", TARGET_ORDER),
        ("🎲 Игры на заказ", TARGET_CUSTOM_GAME_REQUEST),
        ("💼 Итого", None),
    ):
        lines = [f"<b>{title}</b>"]
        for index, currency in enumerate(currencies):
            if multi_currency:
                if index:
                    lines.append("")
                lines.append(f"<i>{currency_code(currency)}</i>")
            lines.extend(_format_lines(report.section(kind, currency), currency))
        blocks.append("\n".join(lines))
    blocks.append(footer)
    return "\n\n".join(blocks)


def request_invoice_period_report(
    period: ReportPeriod | str,
    *,
    now: datetime | None = None,
    correlation_id: str = "",
) -> bool:
    """Build the report and enqueue it to the Telegram notifications topic.

    Returns True when a notification was enqueued, False when it was skipped.
    """
    from apps.payments.notifications import (
        has_invoice_period_report_for_correlation_id,
        notify_payments_invoice_period_report_admin_telegram,
    )

    now = now or timezone.now()
    period_value = ReportPeriod(period).value
    # Cheap early exit for a redelivered stream entry: skip the aggregation too.
    if has_invoice_period_report_for_correlation_id(period=period_value, correlation_id=correlation_id):
        return False
    report = build_invoice_report(period, now=now)
    return notify_payments_invoice_period_report_admin_telegram(
        period=report.period.period.value,
        period_start=report.period.start,
        period_end=report.period.end,
        report_text=format_invoice_report_html(report, now=now),
        now=now,
        correlation_id=correlation_id,
    )
