from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class NotificationOutbox(TimeStampedModel):
    class Channel(models.TextChoices):
        EMAIL = "EMAIL", _("Email")

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Ожидает отправки")
        PROCESSING = "PROCESSING", _("Отправка")
        SENT = "SENT", _("Отправлено")
        FAILED = "FAILED", _("Ошибка")

    channel = models.CharField(_("Канал"), choices=Channel.choices, max_length=16, default=Channel.EMAIL)
    notification_type = models.CharField(_("Тип уведомления"), max_length=64, db_index=True)
    recipient = models.CharField(_("Получатель"), max_length=255, db_index=True)
    payload_encrypted = models.BinaryField(_("Зашифрованное содержимое"))
    status = models.CharField(
        _("Статус"),
        choices=Status.choices,
        max_length=16,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(_("Количество попыток"), default=0)
    last_error = models.CharField(_("Последняя ошибка"), max_length=512, null=True, blank=True)
    last_attempt_at = models.DateTimeField(_("Последняя попытка"), null=True, blank=True)
    sent_at = models.DateTimeField(_("Отправлено"), null=True, blank=True)

    content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Тип объекта"),
    )
    object_id = models.PositiveBigIntegerField(_("ID объекта"), null=True, blank=True)
    target = GenericForeignKey("content_type", "object_id")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Уведомление")
        verbose_name_plural = _("Уведомления")
        indexes = [
            models.Index(fields=["notification_type", "status"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.notification_type} #{self.id} -> {self.recipient}"
