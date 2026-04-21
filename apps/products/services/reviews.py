from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.urls import reverse
from django.utils.http import urlencode

from apps.access.services import has_user_product_access
from apps.core.logging import log_event
from apps.core.metrics import inc_review_resubmitted, inc_review_submitted
from apps.products.models import Product, Review, ReviewStatus

logger = logging.getLogger("apps.products.reviews")


@dataclass(frozen=True)
class ReviewPanelContext:
    show_login: bool
    show_purchase_required: bool
    show_pending: bool
    show_published: bool
    show_rejected_hint: bool
    show_form: bool
    rejection_reason: str
    login_next_url: str
    existing_review_id: int | None

    def as_template_dict(self) -> dict[str, Any]:
        return {
            "show_login": self.show_login,
            "show_purchase_required": self.show_purchase_required,
            "show_pending": self.show_pending,
            "show_published": self.show_published,
            "show_rejected_hint": self.show_rejected_hint,
            "show_form": self.show_form,
            "rejection_reason": self.rejection_reason,
            "login_next_url": self.login_next_url,
            "existing_review_id": self.existing_review_id,
        }


def build_login_next_url(*, product: Product, active_tab: str = "reviews") -> str:
    path = reverse("product-detail", kwargs={"slug": product.slug})
    query = urlencode({"tab": active_tab})
    return f"{path}?{query}"


def get_review_panel_context(*, user, product: Product) -> ReviewPanelContext:  # noqa: PLR0911
    login_next = build_login_next_url(product=product)
    if not getattr(user, "is_authenticated", False):
        return ReviewPanelContext(
            show_login=True,
            show_purchase_required=False,
            show_pending=False,
            show_published=False,
            show_rejected_hint=False,
            show_form=False,
            rejection_reason="",
            login_next_url=login_next,
            existing_review_id=None,
        )

    if not has_user_product_access(user, product.id):
        return ReviewPanelContext(
            show_login=False,
            show_purchase_required=True,
            show_pending=False,
            show_published=False,
            show_rejected_hint=False,
            show_form=False,
            rejection_reason="",
            login_next_url=login_next,
            existing_review_id=None,
        )

    existing = (
        Review.objects.filter(product=product, user=user)
        .only("id", "status", "rejection_reason")
        .first()
    )

    if existing is None:
        return ReviewPanelContext(
            show_login=False,
            show_purchase_required=False,
            show_pending=False,
            show_published=False,
            show_rejected_hint=False,
            show_form=True,
            rejection_reason="",
            login_next_url=login_next,
            existing_review_id=None,
        )

    if existing.status == ReviewStatus.PENDING:
        return ReviewPanelContext(
            show_login=False,
            show_purchase_required=False,
            show_pending=True,
            show_published=False,
            show_rejected_hint=False,
            show_form=False,
            rejection_reason="",
            login_next_url=login_next,
            existing_review_id=existing.id,
        )

    if existing.status == ReviewStatus.PUBLISHED:
        return ReviewPanelContext(
            show_login=False,
            show_purchase_required=False,
            show_pending=False,
            show_published=True,
            show_rejected_hint=False,
            show_form=False,
            rejection_reason="",
            login_next_url=login_next,
            existing_review_id=existing.id,
        )

    if existing.status == ReviewStatus.REJECTED:
        return ReviewPanelContext(
            show_login=False,
            show_purchase_required=False,
            show_pending=False,
            show_published=False,
            show_rejected_hint=True,
            show_form=True,
            rejection_reason=existing.rejection_reason or "",
            login_next_url=login_next,
            existing_review_id=existing.id,
        )

    return ReviewPanelContext(
        show_login=False,
        show_purchase_required=False,
        show_pending=False,
        show_published=False,
        show_rejected_hint=False,
        show_form=False,
        rejection_reason="",
        login_next_url=login_next,
        existing_review_id=existing.id,
    )


def submit_or_resubmit_review(*, user, product: Product, rating: int, comment: str) -> Review:
    if not getattr(user, "is_authenticated", False):
        raise PermissionError("auth_required")
    if not has_user_product_access(user, product.id):
        raise PermissionError("purchase_required")

    existing = Review.objects.filter(product=product, user=user).first()
    if existing is None:
        review = Review.objects.create(
            product=product,
            user=user,
            rating=rating,
            comment=comment,
            status=ReviewStatus.PENDING,
        )
        inc_review_submitted()
        _log_review_created(review)
        return review

    if existing.status == ReviewStatus.PUBLISHED:
        log_event(
            logger,
            logging.WARNING,
            "review.submit.blocked",
            reason="already_published",
            review_id=existing.id,
            product_id=product.id,
            user_id=user.id,
        )
        raise ValueError("already_published")

    if existing.status == ReviewStatus.PENDING:
        log_event(
            logger,
            logging.WARNING,
            "review.submit.blocked",
            reason="already_pending",
            review_id=existing.id,
            product_id=product.id,
            user_id=user.id,
        )
        raise ValueError("already_pending")

    if existing.status == ReviewStatus.REJECTED:
        existing.rating = rating
        existing.comment = comment
        existing.status = ReviewStatus.PENDING
        existing.rejection_reason = ""
        existing.moderated_at = None
        existing.rejection_notified_at = None
        existing.save(
            update_fields=[
                "rating",
                "comment",
                "status",
                "rejection_reason",
                "moderated_at",
                "rejection_notified_at",
                "updated_at",
            ],
        )
        log_event(
            logger,
            logging.INFO,
            "review.resubmitted",
            review_id=existing.id,
            product_id=product.id,
            user_id=user.id,
        )
        inc_review_resubmitted()
        return existing

    log_event(
        logger,
        logging.WARNING,
        "review.submit.blocked",
        reason="unexpected_status",
        review_id=existing.id,
        status=existing.status,
        product_id=product.id,
        user_id=user.id,
    )
    raise ValueError("invalid_state")


def _log_review_created(review: Review) -> None:
    log_event(
        logger,
        logging.INFO,
        "review.created",
        review_id=review.id,
        product_id=review.product_id,
        user_id=review.user_id,
    )
