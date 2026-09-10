from __future__ import annotations

import logging

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from apps.core.logging import log_event
from apps.managed_links.models import ManagedLink
from apps.managed_links.services.storage import ManagedLinkDeleteError, delete_managed_link_file

logger = logging.getLogger("apps.managed_links.storage")


@receiver(pre_delete, sender=ManagedLink)
def delete_managed_link_file_on_row_delete(sender, instance: ManagedLink, **kwargs) -> None:
    file_key = (instance.s3_file_key or "").strip()
    if not file_key:
        return
    try:
        delete_managed_link_file(file_key=file_key)
        log_event(logger, logging.INFO, "managed_link.row_delete.deleted", file_key=file_key)
    except ManagedLinkDeleteError as exc:
        log_event(
            logger,
            logging.WARNING,
            "managed_link.row_delete.delete_failed",
            exc_info=exc,
            file_key=file_key,
            error_type=type(exc).__name__,
        )
