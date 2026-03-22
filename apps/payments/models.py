from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.orders.models import Order
from apps.products.models import Currency


class PaymentProvider(models.TextChoices):
    EXPRESS_PAY = "EXPRESS_PAY", _("Express Pay")


class Invoice(TimeStampedModel):
    class InvoiceStatus(models.TextChoices):
        PENDING = "PENDING", _("Ожидает оплату")
        EXPIRED = "EXPIRED", _("Истёк")
        PAID = "PAID", _("Оплачен")
        CANCELED = "CANCELED", _("Отменён")
        REFUNDED = "REFUNDED", _("Возвращён")

    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="invoice", verbose_name=_("Заказ"))
    provider = models.CharField(
        _("Провайдер"),
        max_length=32,
        choices=PaymentProvider.choices,
        default=PaymentProvider.EXPRESS_PAY,
    )
    provider_invoice_no = models.CharField(_("Номер инвойса у провайдера"), max_length=255, null=True, blank=True)
    status = models.CharField(
        _("Статус инвойса"),
        max_length=16,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.PENDING,
    )
    invoice_url = models.URLField(_("Ссылка на оплату"), null=True, blank=True)
    amount = models.DecimalField(_("Сумма"), max_digits=10, decimal_places=2)
    currency = models.PositiveSmallIntegerField(_("Валюта"), choices=Currency.choices, default=Currency.BYN)
    paid_at = models.DateTimeField(_("Оплачен в"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("Отменён в"), null=True, blank=True)
    expires_at = models.DateTimeField(_("Истекает в"), null=True, blank=True)
    last_status_check_at = models.DateTimeField(_("Последняя проверка статуса"), null=True, blank=True)
    raw_create_response = models.JSONField(_("Ответ создания инвойса"), null=True, blank=True)
    raw_last_status_response = models.JSONField(_("Последний ответ статуса"), null=True, blank=True)

    class Meta:
        db_table = "orders_invoice"
        ordering = ["-created_at", "-id"]
        verbose_name = _("Инвойс")
        verbose_name_plural = _("Инвойсы")

    def __str__(self) -> str:
        return f"#{self.id} · {self.provider} · {self.get_status_display()}"


class PaymentEvent(TimeStampedModel):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name=_("Инвойс"),
    )
    provider = models.CharField(
        _("Провайдер"),
        max_length=32,
        choices=PaymentProvider.choices,
        default=PaymentProvider.EXPRESS_PAY,
    )
    provider_event_key = models.CharField(_("Ключ события провайдера"), max_length=255, unique=True)
    cmd_type = models.PositiveSmallIntegerField(_("Тип команды"))
    provider_payment_no = models.CharField(_("Номер платежа у провайдера"), max_length=255, null=True, blank=True)
    provider_invoice_no = models.CharField(_("Номер инвойса у провайдера"), max_length=255, null=True, blank=True)
    provider_status_code = models.PositiveSmallIntegerField(_("Код статуса провайдера"), null=True, blank=True)
    invoice_status = models.CharField(
        _("Нормализованный статус"),
        max_length=16,
        choices=Invoice.InvoiceStatus.choices,
        null=True,
        blank=True,
    )
    amount = models.DecimalField(_("Сумма"), max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.PositiveSmallIntegerField(_("Валюта"), choices=Currency.choices, default=Currency.BYN)
    payload = models.JSONField(_("Payload"))
    is_processed = models.BooleanField(_("Обработано"), default=False)
    processed_at = models.DateTimeField(_("Обработано в"), null=True, blank=True)
    processing_error = models.TextField(_("Ошибка обработки"), null=True, blank=True)

    class Meta:
        db_table = "orders_paymentevent"
        ordering = ["-created_at", "-id"]
        verbose_name = _("Событие оплаты")
        verbose_name_plural = _("События оплаты")

    def __str__(self) -> str:
        return f"{self.provider} · {self.provider_event_key}"
