from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from config import settings


class UserProductAccess(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_accesses",
        verbose_name=_("Пользователь"),
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="user_accesses",
        verbose_name=_("Товар"),
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_accesses",
        verbose_name=_("Заказ"),
    )
    granted_at = models.DateTimeField(_("Выдано в"), auto_now_add=True)

    class Meta:
        ordering = ["-granted_at", "-id"]
        verbose_name = _("Доступ к товару")
        verbose_name_plural = _("Доступы к товарам")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "product"),
                name="access_user_product_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.product}"


class GuestAccess(TimeStampedModel):
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="guest_accesses",
        verbose_name=_("Заказ"),
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="guest_accesses",
        verbose_name=_("Товар"),
    )
    token_hash = models.CharField(_("Хеш токена"), max_length=64, unique=True)
    expires_at = models.DateTimeField(_("Истекает"), db_index=True)
    last_used_at = models.DateTimeField(_("Последнее использование"), null=True, blank=True)
    downloads_count = models.PositiveSmallIntegerField(_("Количество использований"), default=0)
    max_downloads = models.PositiveSmallIntegerField(_("Максимум использований"), default=3)
    revoked_at = models.DateTimeField(_("Отозван"), null=True, blank=True)
    email = models.EmailField(_("Email"), db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Гостевой доступ к товару")
        verbose_name_plural = _("Гостевые доступы к товарам")
        constraints = [
            models.UniqueConstraint(
                fields=("order", "product"),
                name="guest_access_order_product_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.email} → {self.product}"

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_used(self) -> bool:
        return self.downloads_count > 0

    @property
    def has_remaining_downloads(self) -> bool:
        return self.downloads_count < self.max_downloads

    @property
    def remaining_downloads(self) -> int:
        return max(self.max_downloads - self.downloads_count, 0)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_active(self) -> bool:
        return not self.is_expired and self.has_remaining_downloads and not self.is_revoked


class GuestAccessEmailOutbox(TimeStampedModel):
    class GuestAccessEmailStatus(models.TextChoices):
        PENDING = "PENDING", _("Ожидает отправки")
        PROCESSING = "PROCESSING", _("Отправка")
        SENT = "SENT", _("Отправлено")
        FAILED = "FAILED", _("Ошибка")

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="guest_access_email_outboxes",
        verbose_name=_("Заказ"),
    )
    email = models.EmailField(_("Email"), db_index=True)
    payload_encrypted = models.BinaryField(_("Зашифрованное содержимое"))
    status = models.CharField(
        _("Статус"),
        choices=GuestAccessEmailStatus.choices,
        max_length=16,
        default=GuestAccessEmailStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(_("Количество попыток"), default=0)
    last_error = models.CharField(_("Последняя ошибка"), max_length=512, null=True, blank=True)
    last_attempt_at = models.DateTimeField(_("Последняя попытка"), null=True, blank=True)
    sent_at = models.DateTimeField(_("Отправлено"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Письмо с гостевыми ссылками")
        verbose_name_plural = _("Письма с гостевыми ссылками")

    def __str__(self) -> str:
        return f"Guest email #{self.id} → {self.email}"
