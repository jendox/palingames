from django.db import models
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
