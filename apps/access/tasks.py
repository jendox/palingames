import logging

from celery import shared_task

from apps.core.logging import log_event

from .email_outbox import cleanup_old_guest_access_email_outboxes, process_guest_access_email_outbox

logger = logging.getLogger("apps.access.tasks")


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_guest_access_email_outbox_task(self, outbox_id: int) -> None:
    log_event(
        logger,
        logging.INFO,
        "guest_access.email_outbox.task.started",
        outbox_id=outbox_id,
        task_id=self.request.id,
    )
    process_guest_access_email_outbox(outbox_id=outbox_id)


@shared_task(bind=True)
def cleanup_guest_access_email_outbox_task(self) -> dict[str, int]:
    log_event(
        logger,
        logging.INFO,
        "guest_access.email_outbox.cleanup.started",
        task_id=self.request.id,
    )
    return cleanup_old_guest_access_email_outboxes()
