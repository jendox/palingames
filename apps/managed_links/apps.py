from django.apps import AppConfig


class ManagedLinksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.managed_links"
    verbose_name = "Управляемые ссылки"

    def ready(self):
        import apps.managed_links.signals  # noqa: F401
