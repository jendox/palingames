from django.apps import AppConfig


class FavoritesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.favorites"
    verbose_name = "Избранное"

    def ready(self):
        from . import signals  # noqa: F401
