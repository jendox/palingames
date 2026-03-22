import os

from celery import Celery

from apps.core.celery_logging import ContextTask

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("palingames")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.Task = ContextTask
app.autodiscover_tasks()
