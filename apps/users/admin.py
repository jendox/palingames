from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from apps.core.admin_site import admin_site

from .models import CustomUser, PersonalDataProcessingConsentLog


@admin.register(PersonalDataProcessingConsentLog, site=admin_site)
class PersonalDataProcessingConsentLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "email",
        "source",
        "granted",
        "policy_version",
        "user",
        "order",
        "ip",
    )
    list_filter = ("source", "granted", "policy_version")
    search_fields = ("email", "ip", "user__email", "user_agent")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    autocomplete_fields = ("user", "order")
    readonly_fields = (
        "created_at",
        "updated_at",
        "email",
        "user",
        "order",
        "policy_version",
        "granted",
        "source",
        "ip",
        "user_agent",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CustomUser, site=admin_site)
class CustomUserAdmin(UserAdmin):
    model = CustomUser

    list_display = ("email", "first_name", "last_name", "is_staff", "is_active")
    list_filter = ("is_staff", "is_active")
    fieldsets = (
        (
            _("Personal information"),
            {
                "fields": ("email", ("first_name", "last_name"), "date_joined"),
            },
        ),
        (
            _("Permissions"),
            {
                "classes": ("collapse",),
                "fields": ("is_staff", "is_active", "groups", "user_permissions"),
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_staff", "is_active", "groups", "user_permissions"),
            },
        ),
    )
    search_fields = ("email",)
    ordering = ("email",)
