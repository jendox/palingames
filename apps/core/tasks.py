import logging

from celery import shared_task
from django.core.management import call_command

from apps.core.logging import log_event

logger = logging.getLogger("apps.core.tasks")


@shared_task(bind=True)
def clear_expired_sessions_task(self) -> None:
    log_event(
        logger,
        logging.INFO,
        "sessions.cleanup.started",
        task_id=self.request.id,
    )
    call_command("clearsessions")
    log_event(
        logger,
        logging.INFO,
        "sessions.cleanup.completed",
        task_id=self.request.id,
    )
