import secrets
import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.products.models import Currency, Product
from config import settings

ACCOUNT_NO_RANDOM_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


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

    class Source(models.TextChoices):
        PALINGAMES = "PG", _("Сайт PaliGames")
        TELEGRAM = "TG", _("Telegram")
        INSTAGRAM = "IG", _("Instagram")

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
    source = models.CharField(_("Источник заказа"), max_length=2, choices=Source.choices, default=Source.PALINGAMES)
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
    promo_code = models.ForeignKey(
        "promocodes.PromoCode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("Промокод"),
    )
    promo_code_snapshot = models.CharField(_("Промокод"), max_length=32, blank=True)
    discount_percent_snapshot = models.PositiveSmallIntegerField(_("Скидка, %"), null=True, blank=True)
    promo_eligible_amount = models.DecimalField(
        _("Сумма подходящих под промокод товаров"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    discount_amount = models.DecimalField(_("Скидка"), max_digits=10, decimal_places=2, default=Decimal("0.00"))
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

    @classmethod
    def generate_payment_account_no(cls, source: str, order_date=None) -> str:
        order_date = order_date or timezone.now()
        local_order_date = order_date.astimezone(timezone.get_current_timezone())
        for _attempt in range(10):
            token = "".join(secrets.choice(ACCOUNT_NO_RANDOM_ALPHABET) for _index in range(8))
            payment_account_no = f"{source}{local_order_date:%d%m%y}{token}"
            if not cls.objects.filter(payment_account_no=payment_account_no).exists():
                return payment_account_no

        msg = "Unable to generate a unique payment account number."
        raise RuntimeError(msg)

    def build_payment_account_no(self) -> str:
        return self.generate_payment_account_no(self.source, self.created_at or timezone.now())

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
