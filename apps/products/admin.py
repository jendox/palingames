import admin_thumbnails
from django.contrib import admin
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from .models import (
    AgeGroupTag,
    Category,
    DevelopmentAreaTag,
    Product,
    ProductFile,
    ProductImage,
    Review,
    SubType,
    Theme,
)


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


class ProductFileInline(admin.TabularInline):
    model = ProductFile
    extra = 1
    fields = ("file_key", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("id",)


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
    inlines = (ProductImageInline, ProductFileInline)
    readonly_fields = (
        "created_at",
        "updated_at",
        "average_rating_display",
        "published_reviews_count",
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
                    filter=Q(reviews__is_published=True),
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
        return getattr(obj, "_published_reviews_count", obj.reviews.filter(is_published=True).count())

    @admin.display(description=_("Средний рейтинг"))
    def average_rating_display(self, obj):
        return obj.average_rating or "—"


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
    list_display = ("file_key", "product", "created_at")
    list_filter = ("created_at",)
    search_fields = ("file_key", "product__title", "product__slug")
    autocomplete_fields = ("product",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("product", "id")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "user", "rating", "is_published", "created_at")
    list_filter = ("is_published", "rating", "created_at")
    search_fields = ("product__title", "user__email", "user__username", "comment")
    autocomplete_fields = ("product", "user")
    readonly_fields = ("created_at", "updated_at")
    list_editable = ("is_published",)
    ordering = ("-created_at",)
