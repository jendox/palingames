from __future__ import annotations

import http
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

from apps.core.seo import build_absolute_url
from apps.products.models import Product, ProductFile
from apps.products.services.s3 import (
    ProductFileDownloadUrlError,
    ProductFileMetadataError,
    generate_presigned_download_url,
    head_product_file,
)

SmokeCheckSeverity = Literal["critical", "warning"]

PUBLIC_PAGE_PROBE_TIMEOUT_SECONDS = 10
PUBLIC_PAGE_BODY_READ_LIMIT_BYTES = 65536


@dataclass(frozen=True)
class ProductSmokeCheckProblem:
    product_id: int
    code: str
    severity: SmokeCheckSeverity
    details: dict[str, str | int | None]


@dataclass(frozen=True)
class ProductSmokeCheckResult:
    product_id: int
    problems: list[ProductSmokeCheckProblem]
    skipped: bool = False
    skip_reason: str | None = None


def _make_problem(
    product_id: int,
    *,
    code: str,
    severity: SmokeCheckSeverity,
    details: dict[str, str | int | None] | None = None,
) -> ProductSmokeCheckProblem:
    return ProductSmokeCheckProblem(
        product_id=product_id,
        code=code,
        severity=severity,
        details=details or {},
    )


def _check_basic_product_fields(product: Product) -> list[ProductSmokeCheckProblem]:
    problems: list[ProductSmokeCheckProblem] = []

    if not product.title:
        problems.append(_make_problem(product.id, code="product_title_missing", severity="critical"))
    if not product.slug:
        problems.append(_make_problem(product.id, code="product_slug_missing", severity="critical"))
    if product.price <= 0:
        problems.append(
            _make_problem(
                product.id,
                code="invalid_product_price",
                severity="critical",
                details={"product_price": product.price},
            ),
        )
    if not product.categories.all():
        problems.append(_make_problem(product.id, code="product_category_missing", severity="critical"))

    return problems


def _check_product_images(product: Product) -> list[ProductSmokeCheckProblem]:
    problems: list[ProductSmokeCheckProblem] = []
    images = list(product.images.all())

    if not images:
        return [_make_problem(product.id, code="product_image_missing", severity="critical")]

    for product_image in images:
        object_key = product_image.image.name
        image_details = {
            "image_id": product_image.id,
            "object_key": object_key,
        }
        if not object_key:
            problems.append(
                _make_problem(
                    product.id,
                    code="product_image_object_missing",
                    severity="critical",
                    details=image_details,
                ),
            )
            continue

        storage = product_image.image.storage
        if not storage.exists(object_key):
            problems.append(
                _make_problem(
                    product.id,
                    code="product_image_object_missing",
                    severity="critical",
                    details=image_details,
                ),
            )
            continue

        if storage.size(object_key) <= 0:
            problems.append(
                _make_problem(
                    product.id,
                    code="product_image_empty",
                    severity="critical",
                    details=image_details,
                ),
            )

    return problems


def _check_product_file_record(product: Product, product_file: ProductFile) -> list[ProductSmokeCheckProblem]:
    problems: list[ProductSmokeCheckProblem] = []
    file_details = {"file_id": product_file.id}

    if not product_file.file_key:
        problems.append(
            _make_problem(product.id, code="product_file_key_missing", severity="critical", details=file_details),
        )
    if not product_file.original_filename:
        problems.append(
            _make_problem(product.id, code="product_file_filename_missing", severity="critical", details=file_details),
        )
    if product_file.size_bytes is None:
        problems.append(
            _make_problem(product.id, code="product_file_size_missing", severity="critical", details=file_details),
        )
    elif product_file.size_bytes <= 0:
        problems.append(
            _make_problem(
                product.id,
                code="product_file_size_invalid",
                severity="critical",
                details={**file_details, "size_bytes": product_file.size_bytes},
            ),
        )

    return problems


def _check_product_file_s3_object(
    product: Product,
    product_file: ProductFile,
    metadata: dict,
) -> list[ProductSmokeCheckProblem]:
    problems: list[ProductSmokeCheckProblem] = []
    file_details = {
        "file_id": product_file.id,
        "file_key": product_file.file_key,
    }
    content_length = int(metadata.get("ContentLength") or 0)

    if content_length <= 0:
        problems.append(
            _make_problem(product.id, code="product_file_object_empty", severity="critical", details=file_details),
        )

    if product_file.size_bytes is not None and product_file.size_bytes != content_length:
        problems.append(
            _make_problem(
                product.id,
                code="product_file_size_mismatch",
                severity="critical",
                details={
                    "file_id": product_file.id,
                    "db_size_bytes": product_file.size_bytes,
                    "s3_size_bytes": content_length,
                },
            ),
        )

    db_mime_type = (product_file.mime_type or "").strip()
    s3_content_type = (metadata.get("ContentType") or "").strip()
    if db_mime_type and s3_content_type and db_mime_type.lower() != s3_content_type.lower():
        problems.append(
            _make_problem(
                product.id,
                code="product_file_content_type_mismatch",
                severity="warning",
                details={
                    "file_id": product_file.id,
                    "db_mime_type": db_mime_type,
                    "s3_content_type": s3_content_type,
                },
            ),
        )

    return problems


def _check_product_file_download_url(product: Product, product_file: ProductFile) -> list[ProductSmokeCheckProblem]:
    try:
        generate_presigned_download_url(
            file_key=product_file.file_key,
            original_filename=product_file.original_filename,
        )
    except ProductFileDownloadUrlError:
        return [
            _make_problem(
                product.id,
                code="product_download_url_generation_failed",
                severity="critical",
                details={
                    "file_id": product_file.id,
                    "file_key": product_file.file_key,
                },
            ),
        ]
    return []


def _check_active_product_file(product: Product) -> list[ProductSmokeCheckProblem]:
    active_files = list(product.files.filter(is_active=True))

    if not active_files:
        return [_make_problem(product.id, code="active_product_file_missing", severity="critical")]

    if len(active_files) > 1:
        return [
            _make_problem(
                product.id,
                code="multiple_active_product_files",
                severity="critical",
                details={"active_file_count": len(active_files)},
            ),
        ]

    product_file = active_files[0]
    problems = _check_product_file_record(product, product_file)
    if not product_file.file_key:
        return problems

    try:
        metadata = head_product_file(file_key=product_file.file_key)
    except ProductFileMetadataError:
        problems.append(
            _make_problem(
                product.id,
                code="product_file_object_unavailable",
                severity="critical",
                details={
                    "file_id": product_file.id,
                    "file_key": product_file.file_key,
                },
            ),
        )
        return problems

    problems.extend(_check_product_file_s3_object(product, product_file, metadata))

    if product_file.original_filename:
        problems.extend(_check_product_file_download_url(product, product_file))

    return problems


def _fetch_public_product_page(url: str) -> tuple[int | None, str]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "palingames-product-smoke-check/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=PUBLIC_PAGE_PROBE_TIMEOUT_SECONDS) as response:
            status_code = response.getcode()
            body = response.read(PUBLIC_PAGE_BODY_READ_LIMIT_BYTES).decode("utf-8", errors="replace")
            return status_code, body
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None, ""


def _check_public_product_page(product: Product) -> list[ProductSmokeCheckProblem]:
    if not product.slug:
        return []

    page_path = product.get_absolute_url()
    page_url = build_absolute_url(page_path)
    status_code, body = _fetch_public_product_page(page_url)

    if status_code != http.HTTPStatus.OK:
        return [
            _make_problem(
                product.id,
                code="product_page_unavailable",
                severity="critical",
                details={
                    "status_code": status_code,
                    "path": page_path,
                },
            ),
        ]

    if product.title and product.title not in body:
        return [
            _make_problem(
                product.id,
                code="product_page_content_invalid",
                severity="warning",
                details={"path": page_path},
            ),
        ]

    return []


def run_product_smoke_check(product_id: int) -> ProductSmokeCheckResult:
    product = (
        Product.objects.filter(pk=product_id)
        .prefetch_related("categories", "images", "files")
        .first()
    )
    if product is None:
        return ProductSmokeCheckResult(
            product_id=product_id,
            skipped=True,
            skip_reason="product_missing",
            problems=[],
        )
    if not product.is_published:
        return ProductSmokeCheckResult(
            product_id=product_id,
            skipped=True,
            skip_reason="not_published",
            problems=[],
        )

    problems: list[ProductSmokeCheckProblem] = []
    problems.extend(_check_basic_product_fields(product))
    problems.extend(_check_product_images(product))
    problems.extend(_check_active_product_file(product))
    problems.extend(_check_public_product_page(product))

    return ProductSmokeCheckResult(
        product_id=product.id,
        problems=problems,
    )
