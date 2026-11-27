from __future__ import annotations

import itertools
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.custom_games.models import CustomGameRequest
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import decrypt_outbox_payload
from apps.notifications.telegram_commands import process_telegram_command_stream
from apps.notifications.types import NotificationType
from apps.orders.models import Order
from apps.payments.invoice_report import (
    NBSP,
    Bucket,
    InvoiceReport,
    ReportPeriod,
    StatusGroup,
    _new_groups,
    build_invoice_report,
    format_amount,
    format_invoice_report_html,
    paid_percent,
    request_invoice_period_report,
    resolve_period,
)
from apps.payments.models import Invoice
from apps.products.models import Currency

# 2026-11-27 16:57 in Minsk (UTC+3)
NOW = datetime(2026, 11, 27, 13, 57, tzinfo=UTC)
D = Decimal

TELEGRAM_SETTINGS = {
    "TELEGRAM_REDIS_URL": "redis://localhost:6379/1",
    "TELEGRAM_FORUM_CHAT_ID": "-1001234567890",
    "TELEGRAM_NOTIFICATIONS_THREAD_ID": 3,
}

_counter = itertools.count(1)


def make_invoice(
    *,
    kind: str = "order",
    amount: str = "10.00",
    status: str = Invoice.InvoiceStatus.PENDING,
    created_at: datetime = NOW,
    currency: int = Currency.BYN,
    provider_invoice_no: str | None = None,
) -> Invoice:
    n = next(_counter)
    if provider_invoice_no is None:
        provider_invoice_no = f"INV{n}"
    target: dict = {}
    if kind == "order":
        target["order"] = Order.objects.create(
            email=f"o{n}@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.WAITING_FOR_PAYMENT,
            subtotal_amount=D(amount),
            total_amount=D(amount),
            items_count=1,
        )
    else:
        target["custom_game_request"] = CustomGameRequest.objects.create(
            contact_name="Анна",
            contact_email=f"c{n}@example.com",
            subject="Космос",
            idea="Нужна игра про космос для детей с заданиями на внимание.",
            audience="Дети 6-8 лет",
            page_count="8",
            quoted_price=D(amount),
            deadline=NOW.date(),
            status=CustomGameRequest.Status.WAITING_FOR_PAYMENT,
        )
    invoice = Invoice.objects.create(
        amount=D(amount),
        currency=currency,
        status=status,
        provider_invoice_no=provider_invoice_no or None,
        paid_at=NOW if status == Invoice.InvoiceStatus.PAID else None,
        **target,
    )
    if provider_invoice_no == "":  # noqa: PLC1901
        Invoice.objects.filter(pk=invoice.pk).update(provider_invoice_no="")
    Invoice.objects.filter(pk=invoice.pk).update(created_at=created_at)
    return invoice


def groups(total: tuple[str, int], paid: tuple[str, int], pending: tuple[str, int], closed: tuple[str, int]):
    result = _new_groups()
    for group, (amount, count) in zip(
        (StatusGroup.TOTAL, StatusGroup.PAID, StatusGroup.PENDING, StatusGroup.CLOSED),
        (total, paid, pending, closed),
        strict=True,
    ):
        result[group] = Bucket(D(amount), count)
    return result


class PureFunctionsTests(TestCase):
    def test_format_amount(self):
        self.assertEqual(format_amount(D("1234.5"), Currency.BYN), f"1{NBSP}234,50{NBSP}BYN")
        self.assertEqual(format_amount(D("0"), Currency.USD), f"0,00{NBSP}USD")
        self.assertEqual(format_amount(D("1234567.891"), Currency.EUR), f"1{NBSP}234{NBSP}567,89{NBSP}EUR")

    def test_paid_percent_rounds_half_up(self):
        self.assertEqual(paid_percent(D("2190"), D("2847.50")), 77)
        self.assertEqual(paid_percent(D("1"), D("8")), 13)  # 12.5 -> 13
        self.assertEqual(paid_percent(D("1"), D("200")), 1)  # 0.5 -> 1
        self.assertIsNone(paid_percent(D("0"), D("0")))

    def test_periods(self):
        today = resolve_period("today", now=NOW)
        self.assertEqual(today.title, "Сегодня (27.11.2026)")
        self.assertEqual(today.start.isoformat(), "2026-11-27T00:00:00+03:00")
        self.assertEqual(today.end.isoformat(), "2026-11-28T00:00:00+03:00")

        current = resolve_period("current_month", now=NOW)
        self.assertEqual(current.title, "Ноябрь 2026 (01.11–27.11)")
        self.assertEqual(current.start.isoformat(), "2026-11-01T00:00:00+03:00")
        self.assertEqual(current.end.isoformat(), "2026-12-01T00:00:00+03:00")

        last = resolve_period("last_month", now=NOW)
        self.assertEqual(last.title, "Октябрь 2026 (01.10–31.10)")
        self.assertEqual(last.end.isoformat(), "2026-11-01T00:00:00+03:00")

        all_time = resolve_period("all_time", now=NOW)
        self.assertEqual(all_time.title, "Всё время")
        self.assertIsNone(all_time.start)
        self.assertIsNone(all_time.end)

    def test_january_rollover(self):
        now = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
        last = resolve_period("last_month", now=now)
        self.assertEqual(last.start.isoformat(), "2025-12-01T00:00:00+03:00")
        self.assertEqual(last.end.isoformat(), "2026-01-01T00:00:00+03:00")
        self.assertEqual(last.title, "Декабрь 2025 (01.12–31.12)")

    def test_december_current_month_end(self):
        now = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
        self.assertEqual(resolve_period("current_month", now=now).end.isoformat(), "2027-01-01T00:00:00+03:00")

    def test_today_uses_minsk_date_near_utc_midnight(self):
        now = datetime(2026, 11, 26, 21, 30, tzinfo=UTC)  # already 27.11 in Minsk
        self.assertEqual(resolve_period("today", now=now).title, "Сегодня (27.11.2026)")


class FormatterSnapshotTests(TestCase):
    def test_matches_reference_template(self):
        report = InvoiceReport(period=resolve_period("current_month", now=NOW))
        report.data = {
            "order": {
                Currency.BYN: groups(
                    ("2847.50", 12),
                    ("2190.00", 9),
                    ("457.50", 2),
                    ("200.00", 1),
                ),
            },
            "custom_game_request": {
                Currency.BYN: groups(("1320.00", 3), ("1320.00", 3), ("0", 0), ("0", 0)),
            },
        }

        n = NBSP
        expected = "\n".join(
            [
                "<b>📊 Отчёт по счетам · Ноябрь 2026 (01.11–27.11)</b>",
                "",
                "<b>🛒 Заказы</b>",
                f"Всего: <b>2{n}847,50{n}BYN</b>{n}(12)",
                f"✅ Оплачено: <b>2{n}190,00{n}BYN</b>{n}(9) · 77%",
                f"⏳ Ожидает оплаты: <b>457,50{n}BYN</b>{n}(2)",
                f"❌ Отменено / истекло / возврат: <b>200,00{n}BYN</b>{n}(1)",
                "",
                "<b>🎲 Игры на заказ</b>",
                f"Всего: <b>1{n}320,00{n}BYN</b>{n}(3)",
                f"✅ Оплачено: <b>1{n}320,00{n}BYN</b>{n}(3) · 100%",
                f"⏳ Ожидает оплаты: <b>0,00{n}BYN</b>{n}(0)",
                f"❌ Отменено / истекло / возврат: <b>0,00{n}BYN</b>{n}(0)",
                "",
                "<b>💼 Итого</b>",
                f"Всего: <b>4{n}167,50{n}BYN</b>{n}(15)",
                f"✅ Оплачено: <b>3{n}510,00{n}BYN</b>{n}(12) · 84%",
                f"⏳ Ожидает оплаты: <b>457,50{n}BYN</b>{n}(2)",
                f"❌ Отменено / истекло / возврат: <b>200,00{n}BYN</b>{n}(1)",
                "",
                "<i>Сформировано 27.11.2026 16:57 (Минск)</i>",
            ],
        )
        self.assertEqual(format_invoice_report_html(report, now=NOW), expected)

    def test_empty_period(self):
        report = InvoiceReport(period=resolve_period("today", now=NOW))
        self.assertEqual(
            format_invoice_report_html(report, now=NOW),
            "<b>📊 Отчёт по счетам · Сегодня (27.11.2026)</b>\n\n"
            "Счетов за период нет.\n\n"
            "<i>Сформировано 27.11.2026 16:57 (Минск)</i>",
        )


class AggregationTests(TestCase):
    def test_minsk_day_boundaries(self):
        inside_start = make_invoice(created_at=datetime(2026, 11, 26, 21, 0, 0, tzinfo=UTC), amount="1.00")
        make_invoice(created_at=datetime(2026, 11, 26, 20, 59, 59, tzinfo=UTC), amount="100.00")
        inside_end = make_invoice(created_at=datetime(2026, 11, 27, 20, 59, 59, tzinfo=UTC), amount="2.00")
        make_invoice(created_at=datetime(2026, 11, 27, 21, 0, 0, tzinfo=UTC), amount="200.00")

        report = build_invoice_report("today", now=NOW)

        total = report.section(None, Currency.BYN)[StatusGroup.TOTAL]
        self.assertEqual((total.amount, total.count), (D("3.00"), 2))
        self.assertTrue(inside_start.pk and inside_end.pk)

    def test_current_and_last_month(self):
        make_invoice(created_at=datetime(2026, 10, 31, 21, 0, tzinfo=UTC), amount="5.00")  # 01.11 Minsk
        make_invoice(created_at=datetime(2026, 10, 31, 20, 59, tzinfo=UTC), amount="7.00")  # 31.10 Minsk
        make_invoice(created_at=datetime(2026, 9, 30, 21, 0, tzinfo=UTC), amount="11.00")  # 01.10 Minsk
        make_invoice(created_at=datetime(2026, 9, 30, 20, 59, tzinfo=UTC), amount="13.00")  # 30.09 Minsk

        current = build_invoice_report("current_month", now=NOW).section(None, Currency.BYN)
        last = build_invoice_report("last_month", now=NOW).section(None, Currency.BYN)
        everything = build_invoice_report("all_time", now=NOW).section(None, Currency.BYN)

        self.assertEqual(current[StatusGroup.TOTAL].amount, D("5.00"))
        self.assertEqual(last[StatusGroup.TOTAL].amount, D("18.00"))
        self.assertEqual(everything[StatusGroup.TOTAL].count, 4)

    def test_january_rollover_selection(self):
        now = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
        make_invoice(created_at=datetime(2025, 12, 31, 20, 59, tzinfo=UTC), amount="4.00")  # 31.12 Minsk
        make_invoice(created_at=datetime(2025, 12, 31, 21, 0, tzinfo=UTC), amount="9.00")  # 01.01 Minsk

        last = build_invoice_report("last_month", now=now).section(None, Currency.BYN)
        self.assertEqual(last[StatusGroup.TOTAL].amount, D("4.00"))

    def test_status_groups_and_arithmetic(self):
        S = Invoice.InvoiceStatus
        make_invoice(amount="10.00", status=S.PAID)
        make_invoice(amount="20.00", status=S.REFUNDED)
        make_invoice(amount="30.00", status=S.PENDING)
        make_invoice(amount="40.00", status=S.CANCELED)
        make_invoice(amount="50.00", status=S.EXPIRED)

        section = build_invoice_report("today", now=NOW).section("order", Currency.BYN)

        self.assertEqual((section[StatusGroup.PAID].amount, section[StatusGroup.PAID].count), (D("10.00"), 1))
        self.assertEqual((section[StatusGroup.PENDING].amount, section[StatusGroup.PENDING].count), (D("30.00"), 1))
        self.assertEqual((section[StatusGroup.CLOSED].amount, section[StatusGroup.CLOSED].count), (D("110.00"), 3))
        total = section[StatusGroup.TOTAL]
        self.assertEqual(total.amount, D("150.00"))
        parts = (StatusGroup.PAID, StatusGroup.PENDING, StatusGroup.CLOSED)
        self.assertEqual(total.amount, sum(section[g].amount for g in parts))
        self.assertEqual(total.count, sum(section[g].count for g in parts))
        self.assertEqual(total.count, 5)

    def test_both_target_kinds_and_total(self):
        make_invoice(kind="order", amount="10.00", status=Invoice.InvoiceStatus.PAID)
        make_invoice(kind="custom_game_request", amount="25.00", status=Invoice.InvoiceStatus.PENDING)

        report = build_invoice_report("today", now=NOW)

        self.assertEqual(report.section("order", Currency.BYN)[StatusGroup.TOTAL].amount, D("10.00"))
        self.assertEqual(report.section("custom_game_request", Currency.BYN)[StatusGroup.TOTAL].amount, D("25.00"))
        self.assertEqual(report.section(None, Currency.BYN)[StatusGroup.TOTAL].amount, D("35.00"))

    def test_not_issued_invoices_are_excluded(self):
        make_invoice(amount="10.00", provider_invoice_no="")
        invoice = make_invoice(amount="20.00")
        Invoice.objects.filter(pk=invoice.pk).update(provider_invoice_no=None)

        report = build_invoice_report("today", now=NOW)

        self.assertTrue(report.is_empty)

    def test_multiple_currencies_are_not_summed(self):
        make_invoice(amount="10.00", currency=Currency.BYN, status=Invoice.InvoiceStatus.PAID)
        make_invoice(amount="5.00", currency=Currency.USD, status=Invoice.InvoiceStatus.PAID)

        report = build_invoice_report("today", now=NOW)
        html = format_invoice_report_html(report, now=NOW)

        self.assertEqual(report.currencies(), [Currency.BYN, Currency.USD])
        self.assertEqual(report.section(None, Currency.BYN)[StatusGroup.TOTAL].amount, D("10.00"))
        self.assertEqual(report.section(None, Currency.USD)[StatusGroup.TOTAL].amount, D("5.00"))
        self.assertEqual(html.count("<i>BYN</i>"), 3)
        self.assertEqual(html.count("<i>USD</i>"), 3)
        self.assertLess(html.index("<i>BYN</i>"), html.index("<i>USD</i>"))
        self.assertIn(f"<b>5,00{NBSP}USD</b>", html)

    def test_single_currency_has_no_currency_subheader(self):
        make_invoice(amount="10.00")
        html = format_invoice_report_html(build_invoice_report("today", now=NOW), now=NOW)
        self.assertNotIn("<i>BYN</i>", html)

    def test_orders_only_still_renders_games_block_with_zeros(self):
        make_invoice(kind="order", amount="10.00", status=Invoice.InvoiceStatus.PAID)

        html = format_invoice_report_html(build_invoice_report("today", now=NOW), now=NOW)

        self.assertIn("<b>🎲 Игры на заказ</b>", html)
        games = html.split("<b>🎲 Игры на заказ</b>\n")[1].split("\n\n")[0]
        self.assertEqual(
            games,
            "\n".join(
                [
                    f"Всего: <b>0,00{NBSP}BYN</b>{NBSP}(0)",
                    f"✅ Оплачено: <b>0,00{NBSP}BYN</b>{NBSP}(0)",
                    f"⏳ Ожидает оплаты: <b>0,00{NBSP}BYN</b>{NBSP}(0)",
                    f"❌ Отменено / истекло / возврат: <b>0,00{NBSP}BYN</b>{NBSP}(0)",
                ],
            ),
        )

    def test_empty_period_text(self):
        html = format_invoice_report_html(build_invoice_report("today", now=NOW), now=NOW)
        self.assertIn("Счетов за период нет.", html)
        self.assertTrue(html.endswith("<i>Сформировано 27.11.2026 16:57 (Минск)</i>"))


@override_settings(**TELEGRAM_SETTINGS)
class NotifyTests(TestCase):
    def test_enqueues_outbox_when_route_configured(self):
        make_invoice(amount="10.00")

        self.assertTrue(request_invoice_period_report(ReportPeriod.TODAY, now=NOW))

        outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.PAYMENTS_INVOICE_PERIOD_REPORT_ADMIN,
        )
        self.assertEqual(outbox.channel, NotificationOutbox.Channel.TELEGRAM)
        self.assertTrue(outbox.recipient.startswith("invoice_period_report:today:"))
        payload = decrypt_outbox_payload(outbox.payload_encrypted)
        self.assertEqual(payload["destination"], "notifications")
        self.assertEqual(payload["period"], "today")
        self.assertEqual(payload["period_start"], "2026-11-27T00:00:00+03:00")
        self.assertIn("Отчёт по счетам", payload["report_text"])

    def test_no_dedupe_between_requests(self):
        request_invoice_period_report(ReportPeriod.ALL_TIME, now=NOW)
        request_invoice_period_report(ReportPeriod.ALL_TIME, now=NOW)

        self.assertEqual(
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.PAYMENTS_INVOICE_PERIOD_REPORT_ADMIN,
            ).count(),
            2,
        )
        payload = decrypt_outbox_payload(NotificationOutbox.objects.first().payload_encrypted)
        self.assertIsNone(payload["period_start"])

    @override_settings(TELEGRAM_NOTIFICATIONS_THREAD_ID=0)
    def test_skips_when_route_disabled(self):
        self.assertFalse(request_invoice_period_report(ReportPeriod.TODAY, now=NOW))
        self.assertFalse(NotificationOutbox.objects.exists())


@override_settings(**TELEGRAM_SETTINGS)
class CommandStreamTests(TestCase):
    def _run(self, new_entries, pending_entries=()):
        client = MagicMock()
        client.xreadgroup.side_effect = [
            [("telegram:commands", list(pending_entries))] if pending_entries else [],
            [("telegram:commands", list(new_entries))] if new_entries else [],
        ]
        with (
            patch("apps.notifications.telegram_commands.get_telegram_redis_client", return_value=client),
            patch("apps.payments.invoice_report.request_invoice_period_report") as request_mock,
        ):
            result = process_telegram_command_stream()
        return client, request_mock, result

    def test_valid_command_is_processed_and_acked(self):
        fields = {"command": "invoice_report", "period": "last_month", "requested_by_id": "999"}
        client, request_mock, result = self._run([("1-0", fields)])

        request_mock.assert_called_once_with(ReportPeriod.LAST_MONTH)
        client.xack.assert_called_once_with("telegram:commands", "django-telegram-commands", "1-0")
        self.assertEqual(result, {"processed": 1, "failed": 0})

    def test_unknown_command_is_acked_without_processing(self):
        client, request_mock, _ = self._run([("1-0", {"command": "nope"})])

        request_mock.assert_not_called()
        client.xack.assert_called_once()

    def test_invalid_period_is_acked(self):
        client, request_mock, _ = self._run([("1-0", {"command": "invoice_report", "period": "year"})])

        request_mock.assert_not_called()
        client.xack.assert_called_once()

    def test_handler_failure_is_not_acked(self):
        fields = {"command": "invoice_report", "period": "today", "requested_by_id": "1"}
        client = MagicMock()
        client.xreadgroup.side_effect = [[], [("telegram:commands", [("9999999999999-0", fields)])]]
        with (
            patch("apps.notifications.telegram_commands.get_telegram_redis_client", return_value=client),
            patch("apps.payments.invoice_report.request_invoice_period_report", side_effect=RuntimeError("boom")),
        ):
            result = process_telegram_command_stream()

        client.xack.assert_not_called()
        self.assertEqual(result, {"processed": 0, "failed": 1})


@override_settings(**TELEGRAM_SETTINGS)
class OutboxHandlerTests(TestCase):
    @patch("apps.notifications.handlers.send_telegram_message")
    def test_handler_sends_html_to_notifications(self, send_mock):
        from apps.notifications.destinations import TelegramDestination
        from apps.notifications.services import process_notification_outbox

        request_invoice_period_report(ReportPeriod.TODAY, now=NOW)
        outbox = NotificationOutbox.objects.get()

        self.assertTrue(process_notification_outbox(outbox_id=outbox.id))

        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs["destination"], TelegramDestination.NOTIFICATIONS)
        self.assertEqual(kwargs["parse_mode"], "HTML")
        self.assertEqual(kwargs["correlation_id"], str(outbox.id))
        self.assertIn("Счетов за период нет.", kwargs["text"])
