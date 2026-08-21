import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from libs.express_pay.models import ExpressPayPayment

TELEGRAM_MAX_MESSAGE_LIMIT = 4050

CURRENCY_CODE_TO_STR: dict[int, str] = {
    933: "BYN",
    840: "USD",
    978: "EUR",
    643: "RUB",
}


def _format_currency(currency: int | str) -> str:
    if isinstance(currency, str):
        if currency.isdigit():
            return CURRENCY_CODE_TO_STR.get(int(currency), currency)
        return currency
    return CURRENCY_CODE_TO_STR.get(currency, "UNKNOWN")


def _get_minsk_ts(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=ZoneInfo("Europe/Minsk"))
    return dt.astimezone(ZoneInfo("Europe/Minsk")).strftime("%d.%m.%Y %H:%M")


def _build_report_header(*, period_start: date, period_end: date, page: int | None = None) -> list[str]:
    title = "<b>Отчет о платежах (для НПД)</b>"
    if page is not None and page > 1:
        title = f"<b>Отчет о платежах (для НПД) — стр. {page}</b>"
    return [
        title,
        "",
        "<b>Период</b>",
        f"{period_start.strftime('%d.%m.%Y')} - {period_end.strftime('%d.%m.%Y')}",
        "",
    ]


def build_npd_monthly_report(
    payments: list[ExpressPayPayment],
    *,
    period_start: date,
    period_end: date,
) -> list[str]:
    lines = _build_report_header(period_start=period_start, period_end=period_end)
    if not payments:
        lines.append("В заданном периоде платежей не было.")
        return ["\n".join(lines)]

    report_text: list[str] = []
    page = 1
    for payment in payments:
        currency_code = _format_currency(payment.currency)
        line = (
            f"{_get_minsk_ts(payment.created_at)} - "
            f"{html.escape(payment.account_no)} - "
            f"{payment.amount:.2f} {currency_code}"
        )
        lines.append(line)
        text_length = len("\n".join(lines))
        if text_length >= TELEGRAM_MAX_MESSAGE_LIMIT:
            lines.extend(["", f"Страница: {page}"])
            report_text.append("\n".join(lines))
            page += 1
            lines = _build_report_header(period_start=period_start, period_end=period_end, page=page)
            text_length = len("\n".join(lines))

    if lines:
        if page > 1 or report_text:
            lines.extend(["", f"Страница: {page}"])
        report_text.append("\n".join(lines))

    return report_text
