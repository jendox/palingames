import django_celery_beat.admin  # noqa: F401 — side effect: register on django_admin.site
from allauth.account import app_settings as allauth_account_settings
from allauth.account.admin import EmailAddressAdmin, EmailConfirmationAdmin
from allauth.account.models import EmailAddress, EmailConfirmation
from allauth.socialaccount.admin import SocialAccountAdmin, SocialAppAdmin, SocialTokenAdmin
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.contrib import admin as django_admin
from django.contrib.admin import AdminSite
from django.contrib.admin.sites import NotRegistered
from django_celery_beat.admin import (
    ClockedScheduleAdmin,
    CrontabScheduleAdmin,
    IntervalScheduleAdmin,
    PeriodicTaskAdmin,
    SolarScheduleAdmin,
)
from django_celery_beat.models import (
    ClockedSchedule,
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
    SolarSchedule,
)

_CELERY_BEAT_ADMIN = (
    (PeriodicTask, PeriodicTaskAdmin),
    (IntervalSchedule, IntervalScheduleAdmin),
    (CrontabSchedule, CrontabScheduleAdmin),
    (ClockedSchedule, ClockedScheduleAdmin),
    (SolarSchedule, SolarScheduleAdmin),
)


class PaliAdminSite(AdminSite):
    site_header = "PaliGames Admin"
    site_title = "PaliGames Admin"
    index_title = "Администрирование"

    APP_ORDER = {
        "products": 10,
        "orders": 20,
        "payments": 30,
        "access": 40,
        "custom_games": 50,
        "promocodes": 60,
        "favorites": 70,
        "django_celery_beat": 75,
        "users": 80,
        "account": 81,
        "socialaccount": 82,
        "notifications": 90,
        "pages": 100,
        "cart": 110,
        "core": 120,
    }

    MODEL_ORDER = {
        "orders": {
            "Order": 10,
            "OrderItem": 20,
        },
        "payments": {
            "Invoice": 10,
            "PaymentEvent": 20,
        },
        "django_celery_beat": {
            "PeriodicTask": 10,
            "IntervalSchedule": 20,
            "CrontabSchedule": 30,
            "ClockedSchedule": 40,
            "SolarSchedule": 50,
        },
        "access": {
            "UserProductAccess": 10,
            "GuestAccess": 20,
        },
        "products": {
            "Product": 10,
            "Review": 20,
            "ProductFile": 30,
            "ProductImage": 40,
            "Category": 50,
            "SubType": 60,
            "AgeGroupTag": 70,
            "DevelopmentAreaTag": 80,
            "Theme": 90,
        },
        "custom_games": {
            "CustomGameRequest": 10,
            "CustomGameFile": 20,
            "CustomGameDownloadToken": 30,
        },
        "promocodes": {
            "PromoCode": 10,
            "PromoCodeRedemption": 20,
        },
        "favorites": {
            "Favorite": 10,
        },
        "users": {
            "CustomUser": 10,
        },
        "account": {
            "EmailAddress": 10,
            "EmailConfirmation": 20,
        },
        "socialaccount": {
            "SocialApp": 10,
            "SocialAccount": 20,
            "SocialToken": 30,
        },
        "notifications": {
            "NotificationOutbox": 10,
        },
    }

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request, app_label)

        def app_sort_key(app):
            return (
                self.APP_ORDER.get(app["app_label"], 999),
                app["name"].lower(),
            )

        for app in app_list:
            model_order = self.MODEL_ORDER.get(app["app_label"], {})
            app["models"].sort(
                key=lambda model: (
                    model_order.get(model["object_name"], 999),
                    model["name"].lower(),
                ),
            )

        return sorted(app_list, key=app_sort_key)


admin_site = PaliAdminSite(name="admin")

admin_site.register(EmailAddress, EmailAddressAdmin)
if not allauth_account_settings.EMAIL_CONFIRMATION_HMAC:
    admin_site.register(EmailConfirmation, EmailConfirmationAdmin)
admin_site.register(SocialApp, SocialAppAdmin)
admin_site.register(SocialToken, SocialTokenAdmin)
admin_site.register(SocialAccount, SocialAccountAdmin)

for _model, _admin_cls in _CELERY_BEAT_ADMIN:
    try:
        django_admin.site.unregister(_model)
    except NotRegistered:
        pass
    admin_site.register(_model, _admin_cls)
