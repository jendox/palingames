from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.seo import build_absolute_url

from .tokens import generate_managed_link_token, managed_link_token_prefix


class ManagedLink(TimeStampedModel):
    class ActiveSource(models.TextChoices):
        S3 = "s3", _("S3")
        EXTERNAL = "external", _("Внешняя ссылка")
        UNAVAILABLE = "unavailable", _("Недоступно")

    title = models.CharField(_("Название"), max_length=255)
    token = models.CharField(
        _("Токен"),
        max_length=32,
        unique=True,
        editable=False,
        default=generate_managed_link_token,
    )
    token_prefix = models.CharField(_("Префикс токена"), max_length=16, editable=False, db_index=True)
    s3_file_key = models.CharField(_("Ключ S3"), max_length=512, blank=True, db_index=True)
    original_filename = models.CharField(_("Имя файла"), max_length=255, blank=True)
    mime_type = models.CharField(_("MIME тип"), max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(_("Размер"), null=True, blank=True)
    checksum_sha256 = models.CharField(max_length=64, null=True, blank=True)
    external_url = models.URLField(_("Внешняя ссылка"), max_length=2048, blank=True)
    qr_logo = models.ImageField(
        _("Логотип QR"),
        upload_to="managed_links/qr_logos/",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(_("Активна"), default=False)

    class Meta:
        verbose_name = _("Управляемая ссылка")
        verbose_name_plural = _("Управляемые ссылки")
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(is_active=False) | ~Q(s3_file_key="") | ~Q(external_url=""),
                name="managed_link_active_requires_destination",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def permanent_url(self) -> str:
        return build_absolute_url(f"go/{self.token}/")

    @property
    def active_source(self) -> str:
        if self.s3_file_key:
            return self.ActiveSource.S3
        if self.external_url:
            return self.ActiveSource.EXTERNAL
        return self.ActiveSource.UNAVAILABLE

    @property
    def has_external_fallback(self) -> bool:
        return bool(self.external_url.strip())

    def clean(self):
        super().clean()
        external_url = (self.external_url or "").strip()
        s3_file_key = (self.s3_file_key or "").strip()

        if external_url:
            parsed = urlsplit(external_url)
            if parsed.scheme not in {"http", "https"}:
                raise ValidationError(
                    {"external_url": _("Разрешены только ссылки http и https.")},
                )
            if not parsed.netloc:
                raise ValidationError(
                    {"external_url": _("Укажите корректный адрес внешней ссылки.")},
                )

        if self.is_active and not s3_file_key and not external_url:
            raise ValidationError(
                _("Активная ссылка должна иметь файл в S3 или внешний адрес назначения."),
            )

        if s3_file_key and not self.original_filename:
            raise ValidationError(
                {"original_filename": _("Укажите имя файла для S3-объекта.")},
            )

    def save(self, *args, **kwargs):
        if not self.token_prefix:
            self.token_prefix = managed_link_token_prefix(self.token)
        if self.pk:
            previous_token = type(self).objects.filter(pk=self.pk).values_list("token", flat=True).first()
            if previous_token and previous_token != self.token:
                msg = "Token cannot be changed after creation."
                raise ValidationError(msg)
        super().save(*args, **kwargs)
