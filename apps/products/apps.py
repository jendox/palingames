from django.apps import AppConfig


class ProductsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.products"
    verbose_name = "Товары"

    def ready(self):
        import apps.products.admin  # noqa: F401
        import apps.products.signals  # noqa: F401
