import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core import payment_account_numbers
from apps.core.models import OrderSource, TimeStampedModel
from apps.products.models import Currency
from config import settings

CUSTOM_GAME_SUBJECT_MAX_LENGTH = 200
CUSTOM_GAME_AUDIENCE_MAX_LENGTH = 160
CUSTOM_GAME_PAGE_COUNT_MAX_LENGTH = 64


class CustomGameRequest(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "NEW", _("Новая")
        IN_PROGRESS = "IN_PROGRESS", _("В работе")
        READY = "READY", _("Готова")
        WAITING_FOR_PAYMENT = "WAITING_FOR_PAYMENT", _("Ожидает оплаты")
        PAYMENT_EXPIRED = "PAYMENT_EXPIRED", _("Срок оплаты истёк")
        DELIVERED = "DELIVERED", _("Передана")
        CANCELLED = "CANCELLED", _("Отменена")

    public_id = models.UUIDField(
        _("Публичный идентификатор"),
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    payment_account_no = models.CharField(
        _("Номер лицевого счёта для оплаты"),
        max_length=22,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custom_game_requests",
        verbose_name=_("Пользователь"),
    )
    status = models.CharField(_("Статус"), max_length=20, choices=Status.choices, default=Status.NEW)
    source = models.CharField(
        _("Источник заявки"),
        max_length=2,
        choices=OrderSource.choices,
        default=OrderSource.PALINGAMES,
    )

    contact_name = models.CharField(_("Имя"), max_length=120)
    contact_email = models.EmailField(_("Имейл"))

    subject = models.CharField(_("Название игры"), max_length=CUSTOM_GAME_SUBJECT_MAX_LENGTH)
    audience = models.CharField(_("Возраст"), max_length=CUSTOM_GAME_AUDIENCE_MAX_LENGTH)
    page_count = models.CharField(_("Количество страниц"), max_length=CUSTOM_GAME_PAGE_COUNT_MAX_LENGTH)
    idea = models.TextField(_("Пожелания к содержанию"), blank=True)

    quoted_price = models.DecimalField(_("Цена"), max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.PositiveSmallIntegerField(_("Валюта"), choices=Currency.choices, default=Currency.BYN)
    deadline = models.DateField(_("Дедлайн"), null=True, blank=True)

    admin_notes = models.TextField(_("Заметки"), null=True, blank=True)

    deadline_reminder_sent_at = models.DateTimeField(_("Напоминание отправлено"), null=True, blank=True)
    delivered_at = models.DateTimeField(_("Отправлена"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("Отменена"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Игра на заказ")
        verbose_name_plural = _("Игры на заказ")

    def __str__(self) -> str:
        return f"#{self.id} · {self.contact_email} · {self.get_status_display()}"

    @property
    def has_active_files(self) -> bool:
        if not self.pk:
            return False
        return self.files.filter(is_active=True).exists()

    def build_payment_account_no(self) -> str:
        return payment_account_numbers.generate_payment_account_no(
            source=self.source,
            account_date=self.created_at or timezone.now(),
            exists=lambda account_no: payment_account_numbers.payment_account_no_exists(
                account_no,
                model_labels=("orders.Order", "custom_games.CustomGameRequest"),
            ),
        )

    def save(self, *args, **kwargs):
        needs_payment_account_no = self.pk is None and not self.payment_account_no
        super().save(*args, **kwargs)
        if needs_payment_account_no and not self.payment_account_no:
            self.payment_account_no = self.build_payment_account_no()
            type(self).objects.filter(pk=self.pk, payment_account_no__isnull=True).update(
                payment_account_no=self.payment_account_no,
            )

    def clean(self):
        super().clean()
        if self.status == self.Status.IN_PROGRESS:
            self.validate_can_mark_in_progress()
        if self.status == self.Status.READY:
            self.validate_can_mark_ready()

    def validate_can_mark_in_progress(self) -> None:
        errors = {}
        if self.quoted_price is None:
            errors["quoted_price"] = _("Укажите цену перед переводом заявки в работу.")
        if self.deadline is None:
            errors["deadline"] = _("Укажите дедлайн перед переводом заявки в работу.")
        if errors:
            raise ValidationError(errors)

    def validate_can_mark_ready(self) -> None:
        if not self.has_active_files:
            raise ValidationError(_("Загрузите хотя бы один активный файл перед переводом заявки в статус готовности."))

    def mark_in_progress(self, *, save=True) -> None:
        self.validate_can_mark_in_progress()
        self.status = self.Status.IN_PROGRESS
        if save:
            self.save(update_fields=["status", "updated_at"])

    def mark_ready(self, *, save=True) -> None:
        self.validate_can_mark_ready()
        self.status = self.Status.READY
        if save:
            self.save(update_fields=["status", "updated_at"])

    def mark_cancelled(self, *, save=True) -> None:
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        if save:
            self.save(update_fields=["status", "cancelled_at", "updated_at"])

    def mark_delivered(self, *, save=True) -> None:
        self.status = self.Status.DELIVERED
        self.delivered_at = timezone.now()
        if save:
            self.save(update_fields=["status", "delivered_at", "updated_at"])


class CustomGameFile(TimeStampedModel):
    request = models.ForeignKey(
        CustomGameRequest,
        on_delete=models.CASCADE,
        related_name="files",
        verbose_name=_("Заказ"),
    )
    file_key = models.CharField(_("Ключ файла"), max_length=512, unique=True)
    original_filename = models.CharField(_("Имя файла"), max_length=255, blank=True)
    mime_type = models.CharField(_("MIME тип"), max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(_("Размер"), null=True, blank=True)
    checksum_sha256 = models.CharField(max_length=64, null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Загрузил"),
    )
    uploaded_at = models.DateTimeField(_("Загружено"), auto_now_add=True)
    is_active = models.BooleanField(_("Активен"), default=True)

    class Meta:
        verbose_name = _("Файл")
        verbose_name_plural = _("Файлы")
        ordering = ["-uploaded_at", "-id"]

    def __str__(self) -> str:
        return self.original_filename or self.file_key


class CustomGameDownloadToken(TimeStampedModel):
    request = models.ForeignKey(
        CustomGameRequest,
        on_delete=models.CASCADE,
        related_name="download_tokens",
        verbose_name=_("Заказ"),
    )
    token_hash = models.CharField(_("Хеш токена"), max_length=128, unique=True)
    token_prefix = models.CharField(_("Префикс токена"), max_length=16, db_index=True)
    email = models.EmailField(_("Имейл"))

    expires_at = models.DateTimeField(_("Истекает"))
    max_downloads = models.PositiveIntegerField(_("Максимальное количество загрузок"), default=3)
    downloads_count = models.PositiveIntegerField(_("Количество загрузок"), default=0)
    last_downloaded_at = models.DateTimeField(_("Последняя загрузка"), null=True, blank=True)
    sent_at = models.DateTimeField(_("Отправлено"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("Токен загрузки")
        verbose_name_plural = _("Токены загрузки")

    def __str__(self) -> str:
        return f"#{self.request_id} · {self.email} · {self.token_prefix}"
