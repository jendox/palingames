import enum
import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.products.models import Currency, Product
from config import settings


class Provider(models.TextChoices):
    EXPRESS_PAY = "EXPRESS_PAY", _("Express Pay")


class OrderSource(enum.StrEnum):
    SITE = "01"


def _to_base36(value: int) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if value < 0:
        msg = "Base36 value must be non-negative."
        raise ValueError(msg)
    if value == 0:
        return "0"

    encoded: list[str] = []
    current = value
    while current:
        current, remainder = divmod(current, 36)
        encoded.append(alphabet[remainder])
    return "".join(reversed(encoded))


class Order(TimeStampedModel):
    class OrderStatus(models.TextChoices):
        CREATED = "CREATED", _("Создан")
        PENDING = "PENDING", _("В обработке")
        WAITING_FOR_PAYMENT = "WAITING_FOR_PAYMENT", _("Ожидает оплату")
        PAID = "PAID", _("Оплачен")
        CANCELED = "CANCELED", _("Отменён")
        FAILED = "FAILED", _("Ошибка")

    class CheckoutType(models.TextChoices):
        AUTHENTICATED = "AUTHENTICATED", _("Авторизованный")
        GUEST = "GUEST", _("Гостевой")

    public_id = models.UUIDField(_("Публичный идентификатор"), default=uuid.uuid4, unique=True, editable=False)
    payment_account_no = models.CharField(
        _("Номер лицевого счёта для оплаты"),
        max_length=22,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    status = models.CharField(_("Статус"), max_length=20, choices=OrderStatus.choices, default=OrderStatus.CREATED)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("Пользователь"),
    )
    email = models.EmailField(_("Email"), max_length=254)
    checkout_type = models.CharField(_("Тип оформления"), max_length=16, choices=CheckoutType.choices)
    subtotal_amount = models.DecimalField(_("Сумма позиций"), max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(_("Итоговая сумма"), max_digits=10, decimal_places=2)
    currency = models.PositiveSmallIntegerField(_("Валюта"), choices=Currency.choices, default=Currency.BYN)
    items_count = models.PositiveSmallIntegerField(_("Количество позиций"))
    paid_at = models.DateTimeField(_("Оплачен в"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("Отменён в"), null=True, blank=True)
    failure_reason = models.CharField(_("Причина ошибки"), max_length=128, null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Заказ")
        verbose_name_plural = _("Заказы")

    def build_payment_account_no(self) -> str:
        order_date = (self.created_at or timezone.now()).astimezone(timezone.get_current_timezone())
        encoded_id = _to_base36(self.id).rjust(8, "0")
        return f"{OrderSource.SITE.value}{order_date:%d%m%y}{encoded_id}"

    def save(self, *args, **kwargs):
        needs_payment_account_no = self.pk is None and not self.payment_account_no
        super().save(*args, **kwargs)
        if needs_payment_account_no and not self.payment_account_no:
            self.payment_account_no = self.build_payment_account_no()
            type(self).objects.filter(pk=self.pk, payment_account_no__isnull=True).update(
                payment_account_no=self.payment_account_no,
            )

    def __str__(self) -> str:
        return f"#{self.id} · {self.email} · {self.get_status_display()}"


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name=_("Заказ"))
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items", verbose_name=_("Товар"))
    title_snapshot = models.CharField(_("Название товара"), max_length=255)
    category_snapshot = models.CharField(_("Категория товара"), max_length=100)
    unit_price_amount = models.DecimalField(_("Цена за единицу"), max_digits=10, decimal_places=2)
    quantity = models.PositiveSmallIntegerField(_("Количество"), default=1)
    line_total_amount = models.DecimalField(_("Сумма позиции"), max_digits=10, decimal_places=2)
    product_slug_snapshot = models.SlugField(_("Слаг товара"), null=True, blank=True)
    product_image_snapshot = models.URLField(_("Ссылка на изображение"), null=True, blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = _("Позиция заказа")
        verbose_name_plural = _("Позиции заказа")

    def __str__(self) -> str:
        return f"{self.title_snapshot} × {self.quantity}"


class Invoice(TimeStampedModel):
    class InvoiceStatus(models.TextChoices):
        PENDING = "PENDING", _("Ожидает оплату")
        EXPIRED = "EXPIRED", _("Истёк")
        PAID = "PAID", _("Оплачен")
        CANCELED = "CANCELED", _("Отменён")
        REFUNDED = "REFUNDED", _("Возвращён")

    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="invoice", verbose_name=_("Заказ"))
    provider = models.CharField(_("Провайдер"), max_length=32, choices=Provider.choices, default=Provider.EXPRESS_PAY)
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
    provider = models.CharField(_("Провайдер"), max_length=32, choices=Provider.choices, default=Provider.EXPRESS_PAY)
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
        ordering = ["-created_at", "-id"]
        verbose_name = _("Событие оплаты")
        verbose_name_plural = _("События оплаты")

    def __str__(self) -> str:
        return f"{self.provider} · {self.provider_event_key}"
