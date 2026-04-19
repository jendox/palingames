from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        abstract = True


class OrderSource(models.TextChoices):
    PALINGAMES = "PG", _("Сайт PaliGames")
    TELEGRAM = "TG", _("Telegram")
    INSTAGRAM = "IG", _("Instagram")
