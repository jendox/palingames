from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class EmailLog(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", _("В очереди")
        SENT = "SENT", _("Отправлено")
        FAILED = "FAILED", _("Ошибка")
        SUPPRESSED = "SUPPRESSED", _("Подавлено")

    class Provider(models.TextChoices):
        SMTP = "SMTP", _("SMTP")
        SES_SMTP = "SES_SMTP", _("Amazon SES SMTP")
        SES_API = "SES_API", _("Amazon SES API")

    notification_outbox = models.ForeignKey(
        "notifications.NotificationOutbox",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_logs",
        verbose_name=_("Outbox-уведомление"),
    )
    recipient = models.EmailField(_("Получатель"), max_length=254, db_index=True)
    subject = models.CharField(_("Тема"), max_length=998)
    template_key = models.CharField(_("Ключ шаблона"), max_length=255, blank=True)
    notification_type = models.CharField(_("Тип уведомления"), max_length=64, blank=True, db_index=True)
    status = models.CharField(
        _("Статус"),
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    provider = models.CharField(
        _("Провайдер"),
        max_length=16,
        choices=Provider.choices,
        default=Provider.SMTP,
    )
    message_id = models.CharField(_("Message-ID"), max_length=255, null=True, blank=True)
    smtp_response = models.TextField(_("Ответ SMTP"), null=True, blank=True)
    error = models.TextField(_("Ошибка"), null=True, blank=True)
    sent_at = models.DateTimeField(_("Отправлено в"), null=True, blank=True)
    metadata = models.JSONField(_("Метаданные"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Лог email")
        verbose_name_plural = _("Логи email")
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["notification_type", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.notification_type or 'email'} #{self.id} -> {self.recipient} ({self.status})"


class EmailSuppression(TimeStampedModel):
    class Reason(models.TextChoices):
        HARD_BOUNCE = "HARD_BOUNCE", _("Hard bounce")
        COMPLAINT = "COMPLAINT", _("Complaint")
        MANUAL = "MANUAL", _("Manual")
        UNSUBSCRIBE = "UNSUBSCRIBE", _("Unsubscribe")

    email = models.EmailField(_("Email"), max_length=254, unique=True)
    reason = models.CharField(_("Причина"), max_length=16, choices=Reason.choices)
    active = models.BooleanField(_("Активна"), default=True, db_index=True)
    source = models.CharField(_("Источник"), max_length=64, blank=True)
    suppressed_at = models.DateTimeField(_("Подавлено в"), auto_now_add=True)
    details = models.JSONField(_("Детали"), null=True, blank=True)

    class Meta:
        ordering = ["-suppressed_at", "-id"]
        verbose_name = _("Подавление email")
        verbose_name_plural = _("Подавления email")
        indexes = [
            models.Index(fields=["active", "email"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.reason}, active={self.active})"

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)
