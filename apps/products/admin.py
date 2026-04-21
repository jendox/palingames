import logging

import admin_thumbnails
from django import forms
from django.contrib import admin
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from .forms import ProductFileAdminForm
from .models import (
    AgeGroupTag,
    Category,
    DevelopmentAreaTag,
    Product,
    ProductFile,
    ProductImage,
    Review,
    ReviewStatus,
    SubType,
    Theme,
)
from .services.s3 import delete_product_file, upload_product_file


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "products_count", "created_at")
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("title",)

    @admin.display(description=_("Товаров"))
    def products_count(self, obj):
        return obj.products.count()


@admin.register(SubType)
class SubTypeAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "products_count", "created_at")
    list_filter = ("category",)
    search_fields = ("title", "category__title")
    autocomplete_fields = ("category",)
    ordering = ("category__title", "title")

    @admin.display(description=_("Товаров"))
    def products_count(self, obj):
        return obj.products.count()


@admin.register(AgeGroupTag)
class AgeGroupTagAdmin(admin.ModelAdmin):
    list_display = ("value", "products_count", "created_at")
    search_fields = ("value",)
    ordering = ("value",)

    @admin.display(description=_("Товаров"))
    def products_count(self, obj):
        return obj.products.count()


@admin.register(DevelopmentAreaTag)
class DevelopmentAreaTagAdmin(admin.ModelAdmin):
    list_display = ("title", "products_count", "created_at")
    search_fields = ("title",)
    ordering = ("title",)

    @admin.display(description=_("Товаров"))
    def products_count(self, obj):
        return obj.products.count()


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ("title", "products_count", "created_at")
    search_fields = ("title",)
    ordering = ("title",)

    @admin.display(description=_("Товаров"))
    def products_count(self, obj):
        return obj.products.count()


@admin_thumbnails.thumbnail("image", _("Превью"))
class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image_thumbnail", "image", "order", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("order", "id")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "price",
        "currency",
        "categories_list",
        "images_count",
        "published_reviews_count",
        "average_rating_display",
        "created_at",
    )
    list_filter = (
        "categories",
        "subtypes",
        "age_groups",
        "development_areas",
        "themes",
        "created_at",
        "updated_at",
    )
    search_fields = ("title", "slug", "description", "content")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("categories", "subtypes", "age_groups", "development_areas", "themes")
    inlines = (ProductImageInline,)
    readonly_fields = (
        "created_at",
        "updated_at",
        "average_rating_display",
        "published_reviews_count",
        "files_summary",
    )
    save_on_top = True

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "slug",
                    "price",
                    "currency",
                    "categories",
                    "subtypes",
                    "age_groups",
                    "development_areas",
                    "themes",
                ),
            },
        ),
        (_("Контент"), {"fields": ("description", "content")}),
        (
            _("Файлы"),
            {
                "fields": ("files_summary",),
            },
        ),
        (
            _("Статистика"),
            {"fields": ("average_rating_display", "published_reviews_count", "created_at", "updated_at")},
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related("categories")
            .annotate(
                _images_count=Count("images", distinct=True),
                _published_reviews_count=Count(
                    "reviews",
                    filter=Q(reviews__status=ReviewStatus.PUBLISHED),
                    distinct=True,
                ),
            )
        )

    @admin.display(description=_("Категории"))
    def categories_list(self, obj):
        return ", ".join(category.title for category in obj.categories.all()) or "—"

    @admin.display(description=_("Изображений"), ordering="_images_count")
    def images_count(self, obj):
        return getattr(obj, "_images_count", obj.images.count())

    @admin.display(description=_("Опублик. отзывов"), ordering="_published_reviews_count")
    def published_reviews_count(self, obj):
        return getattr(obj, "_published_reviews_count", obj.reviews.filter(status=ReviewStatus.PUBLISHED).count())

    @admin.display(description=_("Средний рейтинг"))
    def average_rating_display(self, obj):
        return obj.average_rating or "—"

    @admin.display(description=_("Файлы"))
    def files_summary(self, obj):
        files = list(obj.files.order_by("-is_active", "-created_at"))
        add_url = f"{reverse('admin:products_productfile_add')}?product={obj.pk}"

        if not files:
            return format_html(
                'Файлы не добавлены. <a href="{}">Добавить файл</a>',
                add_url,
            )

        rows = format_html_join(
            "",
            '<li>{}{}{} <a href="{}">Открыть</a></li>',
            (
                (
                    product_file.original_filename or product_file.file_key,
                    format_html(" ({})", product_file.mime_type) if product_file.mime_type else "",
                    format_html(" [active]") if product_file.is_active else "",
                    reverse("admin:products_productfile_change", args=[product_file.pk]),
                )
                for product_file in files
            ),
        )
        return format_html(
            '<ul style="margin:0 0 8px 0; padding-left:18px;">{}</ul><a href="{}">Добавить файл</a>',
            rows,
            add_url,
        )


@admin_thumbnails.thumbnail("image", _("Превью"))
@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("image_thumbnail", "product", "order", "created_at")
    list_filter = ("product", "created_at")
    search_fields = ("product__title", "product__slug", "image")
    autocomplete_fields = ("product",)
    readonly_fields = ("image_thumbnail", "created_at", "updated_at")
    ordering = ("product", "order", "id")


@admin.register(ProductFile)
class ProductFileAdmin(admin.ModelAdmin):
    form = ProductFileAdminForm
    list_display = ("original_filename", "product", "mime_type", "size_bytes", "is_active", "created_at")
    list_filter = ("is_active", "mime_type", "created_at")
    search_fields = ("file_key", "original_filename", "product__title", "product__slug")
    autocomplete_fields = ("product",)
    readonly_fields = (
        "file_key",
        "original_filename",
        "mime_type",
        "size_bytes",
        "checksum_sha256",
        "created_at",
        "updated_at",
    )
    ordering = ("product", "id")
    fieldsets = (
        (
            None,
            {
                "fields": ("product", "upload", "is_active"),
            },
        ),
        (
            _("Файл в storage"),
            {
                "fields": (
                    "file_key",
                    "original_filename",
                    "mime_type",
                    "size_bytes",
                    "checksum_sha256",
                ),
            },
        ),
        (
            _("Даты"),
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        product_id = request.GET.get("product")
        if product_id:
            initial["product"] = product_id
        return initial

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        uploaded_file = form.cleaned_data.get("upload")
        previous_file_key = None

        if change:
            previous_file_key = type(obj).objects.only("file_key").get(pk=obj.pk).file_key

        if uploaded_file:
            uploaded_metadata = upload_product_file(
                product_slug=obj.product.slug,
                uploaded_file=uploaded_file,
            )
            obj.file_key = uploaded_metadata["file_key"]
            obj.original_filename = uploaded_metadata["original_filename"]
            obj.mime_type = uploaded_metadata["mime_type"]
            obj.size_bytes = uploaded_metadata["size_bytes"]
            obj.checksum_sha256 = uploaded_metadata["checksum_sha256"]

        super().save_model(request, obj, form, change)

        if uploaded_file and previous_file_key and previous_file_key != obj.file_key:
            delete_product_file(file_key=previous_file_key)


class ReviewAdminForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = (
            "product",
            "user",
            "rating",
            "comment",
            "status",
            "rejection_reason",
            "moderated_at",
            "rejection_notified_at",
        )
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 5}),
            "rejection_reason": forms.Textarea(attrs={"rows": 3}),
        }


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    form = ReviewAdminForm
    list_display = ("product", "user", "rating", "status", "created_at", "moderated_at")
    list_filter = ("status", "rating", "created_at")
    search_fields = ("product__title", "user__email", "user__username", "comment", "rejection_reason")
    autocomplete_fields = ("product", "user")
    readonly_fields = ("created_at", "updated_at", "rejection_notified_at")
    ordering = ("-created_at",)
    save_on_top = True
    fieldsets = (
        (
            None,
            {
                "fields": ("product", "user", "rating", "comment", "status"),
            },
        ),
        (
            _("Модерация"),
            {
                "fields": ("rejection_reason", "moderated_at", "rejection_notified_at"),
            },
        ),
        (
            _("Даты"),
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        prev = None
        if change:
            prev = Review.objects.get(pk=obj.pk)
        if change and prev is not None:
            if prev.status == ReviewStatus.PENDING and obj.status in {
                ReviewStatus.PUBLISHED,
                ReviewStatus.REJECTED,
            }:
                obj.moderated_at = obj.moderated_at or timezone.now()
        super().save_model(request, obj, form, change)
        if change and prev is not None and prev.status != ReviewStatus.REJECTED and obj.status == ReviewStatus.REJECTED:
            send_review_rejected_user_or_log(obj)


logger_review_admin = logging.getLogger("apps.products.admin.reviews")


def send_review_rejected_user_or_log(review: Review) -> None:
    from apps.core.logging import log_event
    from apps.products.emails import send_review_rejected_user_email

    try:
        send_review_rejected_user_email(review=review)
    except Exception:
        log_event(
            logger_review_admin,
            logging.ERROR,
            "review.rejection_email.failed",
            exc_info=True,
            review_id=review.id,
        )
        return
    Review.objects.filter(pk=review.pk).update(
        rejection_notified_at=timezone.now(),
    )
