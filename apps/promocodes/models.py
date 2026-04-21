from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from config import settings


class PromoCode(TimeStampedModel):
    code = models.CharField(_("Код"), max_length=32, unique=True)
    discount_percent = models.PositiveSmallIntegerField(
        _("Скидка, %"),
        validators=[MinValueValidator(1), MaxValueValidator(99)],
    )
    is_reward = models.BooleanField(_("Промокод-награда"), default=False)
    is_active = models.BooleanField(_("Активен"), default=True)
    starts_at = models.DateTimeField(_("Действует с"), null=True, blank=True)
    ends_at = models.DateTimeField(_("Действует до"), null=True, blank=True)
    min_order_amount = models.DecimalField(
        _("Минимальная сумма подходящих товаров"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    max_total_redemptions = models.PositiveIntegerField(_("Общий лимит использований"), null=True, blank=True)
    max_redemptions_per_user = models.PositiveIntegerField(
        _("Лимит на пользователя"),
        null=True,
        blank=True,
    )
    max_redemptions_per_email = models.PositiveIntegerField(_("Лимит на email"), null=True, blank=True)
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assigned_promo_codes",
        verbose_name=_("Назначен пользователю"),
        null=True,
        blank=True,
    )
    assigned_email = models.EmailField(_("Назначен email"), blank=True)
    categories = models.ManyToManyField(
        "products.Category",
        blank=True,
        related_name="promo_codes",
        verbose_name=_("Категории"),
    )
    products = models.ManyToManyField(
        "products.Product",
        blank=True,
        related_name="promo_codes",
        verbose_name=_("Товары"),
    )
    note = models.TextField(_("Заметка"), blank=True)

    class Meta:
        ordering = ["code"]
        verbose_name = _("Промокод")
        verbose_name_plural = _("Промокоды")
        constraints = [
            models.UniqueConstraint(Upper("code"), name="promocode_code_upper_unique"),
        ]

    def __str__(self) -> str:
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        self.assigned_email = self.assigned_email.strip().lower()
        super().save(*args, **kwargs)


class PromoCodeRedemption(TimeStampedModel):
    promo_code = models.ForeignKey(
        PromoCode,
        on_delete=models.PROTECT,
        related_name="redemptions",
        verbose_name=_("Промокод"),
    )
    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="promo_redemption",
        verbose_name=_("Заказ"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="promo_redemptions",
        verbose_name=_("Пользователь"),
        null=True,
        blank=True,
    )
    email = models.EmailField(_("Email"))
    subtotal_amount = models.DecimalField(_("Сумма товаров"), max_digits=10, decimal_places=2)
    eligible_amount = models.DecimalField(_("Сумма подходящих товаров"), max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(_("Скидка"), max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Использование промокода")
        verbose_name_plural = _("Использования промокодов")

    def __str__(self) -> str:
        return f"{self.promo_code.code} → {self.order_id}"
