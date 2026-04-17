from django.contrib import admin

from .models import Favorite


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "product", "created_at")
    list_select_related = ("user", "product")
    search_fields = ("user__email", "product__title", "product__slug")
    raw_id_fields = ("user", "product")
    readonly_fields = ("created_at", "updated_at")
