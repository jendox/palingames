import markdown
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Q
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Currency(models.IntegerChoices):
    BYN = 933, _("BYN")
    USD = 840, _("USD")
    EUR = 978, _("EUR")
    RUB = 643, _("RUB")


class Category(TimeStampedModel):
    title = models.CharField("Название", unique=True, max_length=100)
    slug = models.SlugField("Слаг", unique=True)

    class Meta:
        ordering = ["id"]
        verbose_name = _("Категория")
        verbose_name_plural = _("Категории")

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)


class AgeGroup(models.TextChoices):
    AGE_2_3 = "2-3", _("2-3 года")
    AGE_4_5 = "4-5", _("4-5 лет")
    AGE_6_7 = "6-7", _("6-7 лет")


class AgeGroupTag(TimeStampedModel):
    value = models.CharField("Значение", choices=AgeGroup.choices, max_length=10, unique=True)

    class Meta:
        verbose_name = _("Возрастная группа")
        verbose_name_plural = _("Возрастные группы")

    def __str__(self) -> str:
        return self.value


class SubType(TimeStampedModel):
    title = models.CharField("Название", unique=True, max_length=100)
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="subtypes",
        verbose_name="Категория",
    )

    class Meta:
        verbose_name = _("Подтип")
        verbose_name_plural = _("Подтипы")

    def __str__(self) -> str:
        return f"{self.title} ({self.category.title})"


class DevelopmentAreaTag(TimeStampedModel):
    title = models.CharField("Название", max_length=100, unique=True)

    class Meta:
        verbose_name = _("Область развития")
        verbose_name_plural = _("Области развития")

    def __str__(self):
        return self.title


class Theme(TimeStampedModel):
    title = models.CharField("Название", max_length=100, unique=True)

    class Meta:
        verbose_name = _("Тема")
        verbose_name_plural = _("Темы")

    def __str__(self):
        return self.title


class Product(TimeStampedModel):
    title = models.CharField(_("Название"), unique=True, max_length=255)
    slug = models.SlugField("Слаг", unique=True)
    content = models.TextField(_("Контент"), blank=True)
    description = models.TextField(_("Описание"), blank=True)
    price = models.DecimalField(_("Цена"), max_digits=10, decimal_places=2)
    currency = models.PositiveSmallIntegerField(_("Валюта"), choices=Currency.choices, default=Currency.BYN)

    categories = models.ManyToManyField(Category, related_name="products", verbose_name="Категории")
    subtypes = models.ManyToManyField(SubType, blank=True, related_name="products", verbose_name="Подтипы")
    age_groups = models.ManyToManyField(
        AgeGroupTag,
        blank=True,
        related_name="products",
        verbose_name="Возрастные группы",
    )
    development_areas = models.ManyToManyField(
        DevelopmentAreaTag,
        blank=True,
        related_name="products",
        verbose_name="Области развития",
    )
    themes = models.ManyToManyField(Theme, blank=True, related_name="products", verbose_name="Темы")

    class Meta:
        verbose_name = _("Продукт")
        verbose_name_plural = _("Продукты")

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("product-detail", kwargs={"slug": self.slug})

    def description_as_html(self):
        return mark_safe(markdown.markdown(self.description))

    def content_as_html(self):
        return mark_safe(markdown.markdown(self.content))

    @property
    def average_rating(self) -> float:
        avg = self.reviews.published_only().aggregate(Avg("rating"))["rating__avg"]
        return round(avg, 1) if avg else 0.0

    @property
    def published_reviews(self) -> models.QuerySet:
        return self.reviews.published_only()


def product_image_upload_to(instance, filename):
    slug = instance.product.slug or slugify(instance.product.title)
    return f"products/{slug}/{filename}"


class ProductImage(TimeStampedModel):
    image = models.ImageField(upload_to=product_image_upload_to)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        verbose_name = _("Изображение")
        verbose_name_plural = _("Изображения")


class ProductFile(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="files")
    file_key = models.CharField(_("Ключ файла"), max_length=512, unique=True)
    original_filename = models.CharField(_("Имя файла"), max_length=255, blank=True)
    mime_type = models.CharField(_("MIME тип"), max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(_("Размер"), null=True, blank=True)
    checksum_sha256 = models.CharField(max_length=64, null=True, blank=True)
    is_active = models.BooleanField(_("Активен"), default=True)

    class Meta:
        verbose_name = _("Файл")
        verbose_name_plural = _("Файлы")
        constraints = [
            models.UniqueConstraint(
                fields=("product",),
                condition=Q(is_active=True),
                name="product_file_unique_active_per_product",
            ),
        ]

    def __str__(self) -> str:
        return self.original_filename or self.file_key


class ReviewStatus(models.TextChoices):
    PENDING = "PENDING", _("На модерации")
    PUBLISHED = "PUBLISHED", _("Опубликован")
    REJECTED = "REJECTED", _("Отклонён")


class ReviewQuerySet(models.QuerySet):
    def published_only(self):
        return self.filter(status=ReviewStatus.PUBLISHED)


class ReviewManager(models.Manager.from_queryset(ReviewQuerySet)):
    pass


class Review(TimeStampedModel):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("Продукт"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("Пользователь"),
    )
    rating = models.PositiveSmallIntegerField(_("Рейтинг"), validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(_("Комментарий"))
    status = models.CharField(
        _("Статус"),
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        db_index=True,
    )
    rejection_reason = models.TextField(_("Причина отклонения (для пользователя)"), blank=True)
    moderated_at = models.DateTimeField(_("Обработан в"), null=True, blank=True)
    rejection_notified_at = models.DateTimeField(
        _("Пользователь уведомлён об отклонении в"),
        null=True,
        blank=True,
    )

    objects = ReviewManager()

    class Meta:
        verbose_name = _("Отзыв")
        verbose_name_plural = _("Отзывы")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("product", "user"),
                name="review_unique_per_product_user",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.rating}⭐ - {self.product}"

    @property
    def is_public_on_storefront(self) -> bool:
        return self.status == ReviewStatus.PUBLISHED

    @property
    def is_waiting_moderation(self) -> bool:
        return self.status == ReviewStatus.PENDING

    @property
    def is_rejected(self) -> bool:
        return self.status == ReviewStatus.REJECTED

    @property
    def allows_resubmit(self) -> bool:
        return self.status == ReviewStatus.REJECTED

    @property
    def customer_should_see_pending_message(self) -> bool:
        return self.status == ReviewStatus.PENDING

    @property
    def customer_should_see_published_copy(self) -> bool:
        return self.status == ReviewStatus.PUBLISHED

    def clean(self):
        super().clean()
        if self.status == ReviewStatus.REJECTED and not (self.rejection_reason or "").strip():
            raise ValidationError(
                {"rejection_reason": _("Укажите причину отклонения для пользователя.")},
            )
