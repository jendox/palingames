from django.apps import AppConfig


class CustomGamesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.custom_games"
    verbose_name = "Игры на заказ"
