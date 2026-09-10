from __future__ import annotations

import io
import logging
import re
from pathlib import Path

import qrcode
import segno
from django.conf import settings
from django.contrib.staticfiles import finders
from PIL import Image, ImageOps, UnidentifiedImageError
from qrcode.constants import ERROR_CORRECT_H

from apps.core.seo import build_absolute_url
from apps.managed_links.models import ManagedLink

logger = logging.getLogger("apps.managed_links.qr")

DEFAULT_LOGO_STATIC_PATH = "images/logo-qr-mark.png"
MAX_LOGO_AREA_RATIO = 0.18
QR_LOGO_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class QrGenerationError(Exception):
    pass


class QrLogoValidationError(QrGenerationError):
    pass


def build_managed_link_qr_url(*, token: str) -> str:
    return build_absolute_url(f"go/{token}/")


def _safe_download_filename(title: str) -> str:
    normalized = re.sub(r"[^\w\s-]+", "", title, flags=re.UNICODE).strip()
    normalized = re.sub(r"[-\s]+", "-", normalized).strip("-")
    return normalized[:80] or "managed-link"


def _load_default_logo_image() -> Image.Image | None:
    logo_path = finders.find(DEFAULT_LOGO_STATIC_PATH)
    if not logo_path:
        return None
    try:
        with Image.open(logo_path) as image:
            return image.convert("RGBA")
    except (OSError, UnidentifiedImageError):
        return None


def _load_managed_link_logo_image(managed_link: ManagedLink) -> Image.Image | None:
    if managed_link.qr_logo:
        try:
            with Image.open(managed_link.qr_logo) as image:
                return image.convert("RGBA")
        except (OSError, UnidentifiedImageError) as exc:
            raise QrLogoValidationError("Unable to read QR logo image.") from exc
    return _load_default_logo_image()


def validate_qr_logo_upload(*, filename: str, size_bytes: int) -> None:
    extension = Path(filename).suffix.lower()
    if extension not in QR_LOGO_IMAGE_EXTENSIONS:
        raise QrLogoValidationError("QR logo must be PNG, JPEG, or WebP.")

    if size_bytes <= 0 or size_bytes > settings.MANAGED_LINK_QR_LOGO_MAX_BYTES:
        max_mb = settings.MANAGED_LINK_QR_LOGO_MAX_BYTES / 1048576
        raise QrLogoValidationError(f"QR logo must not exceed {max_mb:.0f}MB.")


def _compose_logo_on_qr(base_image: Image.Image, logo_image: Image.Image) -> Image.Image:
    qr_width, qr_height = base_image.size
    max_logo_side = int(min(qr_width, qr_height) * MAX_LOGO_AREA_RATIO)
    if max_logo_side <= 0:
        return base_image

    logo = ImageOps.contain(logo_image, (max_logo_side, max_logo_side), method=Image.Resampling.LANCZOS)
    pad_size = int(max(logo.width, logo.height) * 1.15)
    pad = Image.new("RGBA", (pad_size, pad_size), (255, 255, 255, 255))
    pad_x = (pad_size - logo.width) // 2
    pad_y = (pad_size - logo.height) // 2
    pad.paste(logo, (pad_x, pad_y), logo)

    result = base_image.convert("RGBA")
    paste_x = (qr_width - pad_size) // 2
    paste_y = (qr_height - pad_size) // 2
    result.paste(pad, (paste_x, paste_y), pad)
    return result.convert("RGB")


def generate_qr_png(*, managed_link: ManagedLink, with_logo: bool = True) -> bytes:
    qr_url = build_managed_link_qr_url(token=managed_link.token)
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=max(1, settings.MANAGED_LINK_QR_BOX_SIZE),
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    target_size = settings.MANAGED_LINK_QR_PNG_SIZE_PX
    if max(image.size) != target_size:
        image = image.resize((target_size, target_size), resample=Image.Resampling.NEAREST)

    if with_logo:
        logo_image = _load_managed_link_logo_image(managed_link)
        if logo_image is not None:
            image = _compose_logo_on_qr(image, logo_image)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(300, 300))
    return buffer.getvalue()


def generate_qr_svg(*, managed_link: ManagedLink) -> bytes:
    qr_url = build_managed_link_qr_url(token=managed_link.token)
    code = segno.make(qr_url, error="h")
    buffer = io.BytesIO()
    code.save(buffer, kind="svg", scale=8, border=4, dark="#000000", light="#ffffff")
    return buffer.getvalue()


def qr_png_content_disposition(*, managed_link: ManagedLink) -> str:
    filename = _safe_download_filename(managed_link.title)
    return f'attachment; filename="{filename}.png"'


def qr_svg_content_disposition(*, managed_link: ManagedLink) -> str:
    filename = _safe_download_filename(managed_link.title)
    return f'attachment; filename="{filename}.svg"'
