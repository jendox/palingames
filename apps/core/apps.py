import logging

from django.apps import AppConfig

from .logging import log_event


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

    _startup_logged = False

    def ready(self):
        if self.__class__._startup_logged:
            return
        self.__class__._startup_logged = True
        log_event(logging.getLogger("apps.lifecycle"), logging.INFO, "app.started", component=self.name)
