from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.users.managers import CustomUserManager
from config import settings


class CustomUser(AbstractUser):
    email = models.EmailField(_("email address"), unique=True)
    username = models.CharField(_("username"), max_length=30, unique=False, blank=True, null=False, default="")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        constraints = [
            models.UniqueConstraint(Lower("email"), name="uniq_user_email_lower"),
        ]

    def __str__(self):
        return self.email

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display_name(self) -> str:
        return f"{self.first_name}".strip() or self.email

    @property
    def review_name(self) -> str:
        return self.full_name or "Покупатель"


class PersonalDataProcessingConsentLog(TimeStampedModel):
    class Source(models.TextChoices):
        REGISTRATION_PASSWORD = "REGISTRATION_PASSWORD", _("Регистрация (пароль)")
        OAUTH_FIRST_LOGIN = "OAUTH_FIRST_LOGIN", _("Первый вход (OAuth)")
        GUEST_CHECKOUT = "GUEST_CHECKOUT", _("Гостевой заказ")
        POLICY_RECONFIRM = "POLICY_RECONFIRM", _("Повтор при смене политики")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pd_consent_log_entries",
        verbose_name=_("Пользователь"),
    )
    email = models.EmailField(_("Email"), max_length=254, db_index=True)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pd_consent_log_entries",
        verbose_name=_("Заказ"),
    )
    policy_version = models.PositiveSmallIntegerField(_("Версия политики"))
    granted = models.BooleanField(_("Согласие"))
    source = models.CharField(_("Источник"), max_length=32, choices=Source.choices)

    ip = models.GenericIPAddressField(_("IP адрес"), null=True, blank=True)
    user_agent = models.CharField(_("Агент пользователя"), max_length=256, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["email", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email} - {self.policy_version} - {self.granted}"
