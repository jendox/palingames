import logging

from django.apps import AppConfig
from django.conf import settings

from .logging import log_event
from .sentry import init_sentry


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

    _startup_logged = False

    def ready(self):
        if self.__class__._startup_logged:
            return
        self.__class__._startup_logged = True
        init_sentry(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            release=settings.SENTRY_RELEASE or None,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        )
        log_event(logging.getLogger("apps.lifecycle"), logging.INFO, "app.started", component=self.name)
