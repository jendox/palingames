import logging

from celery import shared_task

from apps.core.logging import log_event

from .services import cleanup_old_notification_outboxes, process_notification_outbox

logger = logging.getLogger("apps.notifications.tasks")


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_notification_outbox_task(self, outbox_id: int) -> None:
    log_event(
        logger,
        logging.INFO,
        "notification.outbox.task.started",
        outbox_id=outbox_id,
        task_id=self.request.id,
    )
    process_notification_outbox(outbox_id=outbox_id)


@shared_task(bind=True)
def cleanup_notification_outbox_task(self) -> dict[str, int]:
    log_event(
        logger,
        logging.INFO,
        "notification.outbox.cleanup.started",
        task_id=self.request.id,
    )
    return cleanup_old_notification_outboxes()
