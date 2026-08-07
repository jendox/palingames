from __future__ import annotations

from datetime import datetime
from typing import Any

import markdown
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.products.storage import (
    build_collection_cover_object_key,
    build_product_image_object_key,
    get_product_image_storage,
)


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

    class Meta:
        verbose_name = _("Подтип")
        verbose_name_plural = _("Подтипы")

    def __str__(self) -> str:
        return self.title


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


def product_image_upload_to(instance: ProductImage, filename: str) -> str:
    slug = instance.product.slug or slugify(instance.product.title)
    return build_product_image_object_key(product_slug=slug, filename=filename)


class ProductImage(TimeStampedModel):
    image = models.ImageField(
        upload_to=product_image_upload_to,
        storage=get_product_image_storage,
    )
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
    reward_promo_code = models.ForeignKey(
        "promocodes.PromoCode",
        on_delete=models.SET_NULL,
        related_name="rewarded_reviews",
        verbose_name=_("Выданный промокод за отзыв"),
        null=True,
        blank=True,
    )
    reward_issued_at = models.DateTimeField(_("Промокод выдан в"), null=True, blank=True)
    reward_email_sent_at = models.DateTimeField(_("Письмо с промокодом отправлено в"), null=True, blank=True)

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


def collection_cover_upload_to(instance: ProductCollection, filename: str) -> str:
    slug = instance.slug or slugify(instance.title)
    return build_collection_cover_object_key(collection_slug=slug, filename=filename)


COLLECTION_ROBOTS_INDEX_FOLLOW = "index,follow"
COLLECTION_ROBOTS_NOINDEX_FOLLOW = "noindex,follow"

COLLECTION_ROBOTS_CHOICES = (
    (COLLECTION_ROBOTS_INDEX_FOLLOW, "index,follow"),
    (COLLECTION_ROBOTS_NOINDEX_FOLLOW, "noindex,follow"),
)


class ProductCollection(TimeStampedModel):
    title = models.CharField(_("Название"), max_length=120)
    slug = models.SlugField(_("Слаг"), unique=True, max_length=120)
    short_description = models.CharField(_("Краткое описание"), max_length=240, blank=True)
    description = models.TextField(_("Описание"), blank=True)
    seo_title = models.CharField(_("SEO title"), max_length=70, blank=True)
    seo_description = models.CharField(_("SEO description"), max_length=160, blank=True)
    cover_image = models.ImageField(
        _("Обложка"),
        upload_to=collection_cover_upload_to,
        storage=get_product_image_storage,
        blank=True,
    )
    is_published = models.BooleanField(_("Опубликовано"), default=False)
    robots = models.CharField(
        _("Robots"),
        max_length=32,
        choices=COLLECTION_ROBOTS_CHOICES,
        default=COLLECTION_ROBOTS_INDEX_FOLLOW,
    )
    show_on_homepage = models.BooleanField(_("Показывать на главной"), default=False)
    show_in_catalog = models.BooleanField(_("Показывать в каталоге"), default=False)
    show_in_navigation = models.BooleanField(_("Показывать в навигации"), default=False)
    show_in_footer = models.BooleanField(_("Показывать в футере"), default=False)
    sort_order = models.PositiveSmallIntegerField(_("Порядок"), default=0)
    badge = models.CharField(_("Бейдж"), max_length=32, blank=True)
    campaign_code = models.SlugField(_("Код кампании (UTM)"), max_length=64, blank=True)
    publish_starts_at = models.DateTimeField(_("Публикация с"), null=True, blank=True)
    publish_ends_at = models.DateTimeField(_("Публикация до"), null=True, blank=True)
    min_products_to_publish = models.PositiveSmallIntegerField(
        _("Минимум товаров для публикации"), default=5,
    )
    products = models.ManyToManyField(
        Product,
        through="CollectionProduct",
        related_name="collections",
        verbose_name=_("Продукты"),
    )

    class Meta:
        ordering = ["sort_order", "title"]
        verbose_name = _("Тематическая подборка")
        verbose_name_plural = _("Тематические подборки")

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("collection-detail", kwargs={"slug": self.slug})

    def is_within_publish_window(self, *, at: datetime | None = None) -> bool:
        moment = at or timezone.now()
        if self.publish_starts_at and moment < self.publish_starts_at:
            return False
        if self.publish_ends_at and moment > self.publish_ends_at:
            return False
        return True

    def visible_products_queryset(self):
        return (
            Product.objects.filter(
                collection_products__collection=self,
                files__is_active=True,
            )
            .distinct()
            .order_by("collection_products__sort_order", "collection_products__id")
        )

    def visible_products_count(self) -> int:
        return self.visible_products_queryset().count()

    def can_publish(self) -> bool:
        return self.visible_products_count() >= self.min_products_to_publish

    def _check_slug_unique(self, errors: dict[str, Any]) -> None:
        slug = (self.slug or "").strip()
        if slug:
            if Category.objects.filter(slug=slug).exists():
                errors["slug"] = _("Слаг уже используется категорией.")
            if Product.objects.filter(slug=slug).exists():
                errors["slug"] = _("Слаг уже используется продуктом.")

    def clean(self):
        super().clean()
        errors: dict[str, Any] = {}

        self._check_slug_unique(errors)

        validate_publish = getattr(self, "_validate_publish", True)
        if validate_publish:
            if self.is_published and not self.can_publish():
                errors["is_published"] = _(
                    "Нельзя опубликовать: нужно минимум %(min)s продуктов с активным файлом, сейчас %(count)s.",
                ) % {"min": self.min_products_to_publish, "count": self.visible_products_count()}

            if self.is_published and not self.is_within_publish_window():
                errors["is_published"] = _("Нельзя публиковать вне окна публикации.")

        if self.publish_starts_at and self.publish_ends_at and self.publish_starts_at >= self.publish_ends_at:
            errors["publish_ends_at"] = _("Дата окончания должна быть позже даты начала.")

        if errors:
            raise ValidationError(errors)


class CollectionProduct(TimeStampedModel):
    collection = models.ForeignKey(
        ProductCollection,
        on_delete=models.CASCADE,
        related_name="collection_products",
        verbose_name=_("Подборка"),
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="collection_products",
        verbose_name=_("Продукт"),
    )
    sort_order = models.PositiveSmallIntegerField(_("Порядок"), default=0)
    is_featured = models.BooleanField(_("Выделить"), default=False)
    label = models.CharField(_("Метка"), max_length=64, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = _("Товар в подборке")
        verbose_name_plural = _("Товары в подборке")
        constraints = [
            models.UniqueConstraint(
                fields=("collection", "product"),
                name="collection_product_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.collection.title} → {self.product.title}"
