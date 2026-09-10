from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

from apps.managed_links.models import ManagedLink
from apps.managed_links.services.storage import (
    ManagedLinkDownloadUrlError,
    generate_presigned_download_url,
)

logger = logging.getLogger("apps.managed_links.redirect")


@dataclass(frozen=True)
class ManagedLinkRedirectResult:
    url: str
    source: str


def validate_external_redirect_url(external_url: str) -> str:
    parsed = urlsplit(external_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("Unsupported external URL scheme.")
    if not parsed.netloc:
        raise ValidationError("Invalid external URL.")
    return external_url.strip()


def resolve_managed_link_redirect(*, managed_link: ManagedLink) -> ManagedLinkRedirectResult:
    if managed_link.s3_file_key:
        try:
            download_url = generate_presigned_download_url(
                file_key=managed_link.s3_file_key,
                original_filename=managed_link.original_filename or "download",
            )
        except ManagedLinkDownloadUrlError:
            if managed_link.external_url:
                return ManagedLinkRedirectResult(
                    url=validate_external_redirect_url(managed_link.external_url),
                    source="external_fallback",
                )
            raise
        return ManagedLinkRedirectResult(url=download_url, source="s3")

    if managed_link.external_url:
        return ManagedLinkRedirectResult(
            url=validate_external_redirect_url(managed_link.external_url),
            source="external",
        )

    raise ValidationError("Managed link has no destination.")
