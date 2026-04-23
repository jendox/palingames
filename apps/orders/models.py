import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core import payment_account_numbers
from apps.core.models import OrderSource, TimeStampedModel
from apps.products.models import Currency, Product
from config import settings

ACCOUNT_NO_RANDOM_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class Order(TimeStampedModel):
    class OrderStatus(models.TextChoices):
        CREATED = "CREATED", _("Создан")
        WAITING_FOR_PAYMENT = "WAITING_FOR_PAYMENT", _("Ожидает оплату")
        PAID = "PAID", _("Оплачен")
        CANCELED = "CANCELED", _("Отменён")
        FAILED = "FAILED", _("Ошибка")

    class CheckoutType(models.TextChoices):
        AUTHENTICATED = "AUTHENTICATED", _("Авторизованный")
        GUEST = "GUEST", _("Гостевой")

    Source = OrderSource

    public_id = models.UUIDField(_("Публичный идентификатор"), default=uuid.uuid4, unique=True, editable=False)
    checkout_idempotency_key = models.UUIDField(
        _("Ключ идемпотентности оформления"),
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
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
    subtotal_amount = models.DecimalField(
        _("Сумма позиций"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
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
    total_amount = models.DecimalField(
        _("Итоговая сумма"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    reward_promo_code = models.ForeignKey(
        "promocodes.PromoCode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rewarded_orders",
        verbose_name=_("Выданный промокод за заказ"),
    )
    reward_issued_at = models.DateTimeField(_("Промокод за заказ выдан в"), null=True, blank=True)
    reward_email_sent_at = models.DateTimeField(_("Письмо с промокодом за заказ отправлено в"), null=True, blank=True)
    currency = models.PositiveSmallIntegerField(_("Валюта"), choices=Currency.choices, default=Currency.BYN)
    items_count = models.PositiveSmallIntegerField(_("Количество позиций"), default=0)
    paid_at = models.DateTimeField(_("Оплачен в"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("Отменён в"), null=True, blank=True)
    failure_reason = models.CharField(_("Причина ошибки"), max_length=128, null=True, blank=True)

    analytics_storage_consent = models.BooleanField(_("Согласие на аналитику (GA4) при оформлении"), default=False)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Заказ")
        verbose_name_plural = _("Заказы")

    @classmethod
    def generate_payment_account_no(cls, source: str, order_date=None) -> str:
        return payment_account_numbers.generate_payment_account_no(
            source=source,
            account_date=order_date,
            exists=lambda account_no: payment_account_numbers.payment_account_no_exists(
                account_no,
                model_labels=("orders.Order", "custom_games.CustomGameRequest"),
            ),
        )

    def __str__(self) -> str:
        return f"#{self.id} · {self.email} · {self.get_status_display()}"

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


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name=_("Заказ"))
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items", verbose_name=_("Товар"))
    title_snapshot = models.CharField(_("Название товара"), max_length=255)
    category_snapshot = models.CharField(_("Категория товара"), max_length=100)
    unit_price_amount = models.DecimalField(_("Цена за единицу"), max_digits=10, decimal_places=2)
    quantity = models.PositiveSmallIntegerField(_("Количество"), default=1)
    line_total_amount = models.DecimalField(_("Сумма позиции"), max_digits=10, decimal_places=2)
    promo_eligible = models.BooleanField(_("Применён промокод"), default=False)
    discount_amount = models.DecimalField(_("Скидка"), max_digits=10, decimal_places=2, default=Decimal("0.00"))
    discounted_line_total_amount = models.DecimalField(
        _("Сумма позиции со скидкой"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    product_slug_snapshot = models.SlugField(_("Слаг товара"), null=True, blank=True)
    product_image_snapshot = models.URLField(_("Ссылка на изображение"), null=True, blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = _("Позиция заказа")
        verbose_name_plural = _("Позиции заказа")

    def _hydrate_from_product_if_incomplete(self) -> None:
        """Admin inline only submits editable fields; readonly prices/snapshots must be filled before INSERT."""
        if not self.product_id:
            return
        if (
            self.unit_price_amount is not None
            and self.line_total_amount is not None
            and self.title_snapshot
        ):
            return
        product = (
            Product.objects.prefetch_related("categories")
            .filter(pk=self.product_id)
            .first()
        )
        if product is None:
            return
        qty = self.quantity if self.quantity else 1
        if not self.title_snapshot:
            self.title_snapshot = product.title
        first_category = product.categories.first()
        self.category_snapshot = first_category.title if first_category else ""
        self.unit_price_amount = product.price
        self.line_total_amount = product.price * qty

    def save(self, *args, **kwargs):
        self._hydrate_from_product_if_incomplete()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.title_snapshot} × {self.quantity}"
