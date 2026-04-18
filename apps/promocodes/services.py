from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from apps.orders.models import Order
from apps.products.models import Product

from .models import PromoCode, PromoCodeRedemption

MONEY_QUANT = Decimal("0.01")


class PromoCodeError(ValueError):
    default_message = "Промокод недействителен."

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)


class PromoCodeNotFoundError(PromoCodeError):
    default_message = "Промокод недействителен."


class PromoCodeInactiveError(PromoCodeError):
    default_message = "Срок действия промокода истек или он отключен."


class PromoCodeNotApplicableError(PromoCodeError):
    default_message = "Промокод не применяется к товарам в заказе."


class PromoCodeLimitExceededError(PromoCodeError):
    default_message = "Лимит использования промокода исчерпан."


@dataclass(frozen=True)
class PromoCodeDiscount:
    promo_code: PromoCode
    code: str
    discount_percent: int
    eligible_amount: Decimal
    discount_amount: Decimal


def normalize_promo_code(value: str) -> str:
    return value.strip().upper()


def calculate_percent_discount(amount: Decimal, percent: int) -> Decimal:
    return (amount * Decimal(percent) / Decimal("100")).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _redemption_base_queryset(promo_code: PromoCode):
    return PromoCodeRedemption.objects.filter(promo_code=promo_code).exclude(
        order__status__in=[
            Order.OrderStatus.CANCELED,
            Order.OrderStatus.FAILED,
        ],
    )


def _validate_promo_code_availability(promo_code: PromoCode, normalized_email: str, user) -> None:
    now = timezone.now()
    if not promo_code.is_active:
        raise PromoCodeInactiveError
    if promo_code.starts_at and promo_code.starts_at > now:
        raise PromoCodeInactiveError
    if promo_code.ends_at and promo_code.ends_at < now:
        raise PromoCodeInactiveError
    if promo_code.assigned_user_id and promo_code.assigned_user_id != getattr(user, "id", None):
        raise PromoCodeNotFoundError
    if promo_code.assigned_email and promo_code.assigned_email != normalized_email:
        raise PromoCodeNotFoundError


def _validate_promo_code_limits(
    promo_code: PromoCode,
    *,
    user,
    normalized_email: str,
    require_email_limits: bool,
) -> None:
    redemptions = _redemption_base_queryset(promo_code)
    if promo_code.max_total_redemptions is not None and redemptions.count() >= promo_code.max_total_redemptions:
        raise PromoCodeLimitExceededError
    if (
        getattr(user, "is_authenticated", False)
        and promo_code.max_redemptions_per_user is not None
        and redemptions.filter(user=user).count() >= promo_code.max_redemptions_per_user
    ):
        raise PromoCodeLimitExceededError
    email_limit_exceeded = (
        require_email_limits
        and normalized_email
        and promo_code.max_redemptions_per_email is not None
        and redemptions.filter(email__iexact=normalized_email).count() >= promo_code.max_redemptions_per_email
    )
    if email_limit_exceeded:
        raise PromoCodeLimitExceededError


def _validate_promo_code_state(promo_code: PromoCode, *, user, email: str, require_email_limits: bool) -> None:
    normalized_email = email.strip().lower()
    _validate_promo_code_availability(promo_code, normalized_email, user)
    _validate_promo_code_limits(
        promo_code,
        user=user,
        normalized_email=normalized_email,
        require_email_limits=require_email_limits,
    )


def _has_product_restrictions(promo_code: PromoCode) -> bool:
    return promo_code.categories.exists() or promo_code.products.exists()


def _is_product_eligible(product: Product, promo_code: PromoCode) -> bool:
    restricted_product_ids = set(promo_code.products.values_list("id", flat=True))
    if product.id in restricted_product_ids:
        return True

    restricted_category_ids = set(promo_code.categories.values_list("id", flat=True))
    if not restricted_category_ids:
        return not restricted_product_ids

    return any(category.id in restricted_category_ids for category in product.categories.all())


def calculate_promo_code_discount(
    *,
    code: str,
    products: list[Product],
    user,
    email: str,
    require_email_limits: bool = True,
) -> PromoCodeDiscount:
    normalized_code = normalize_promo_code(code)
    if not normalized_code:
        raise PromoCodeNotFoundError

    promo_code = (
        PromoCode.objects.prefetch_related("categories", "products")
        .filter(code__iexact=normalized_code)
        .first()
    )
    if promo_code is None:
        raise PromoCodeNotFoundError

    _validate_promo_code_state(
        promo_code,
        user=user,
        email=email,
        require_email_limits=require_email_limits,
    )

    if _has_product_restrictions(promo_code):
        eligible_products = [product for product in products if _is_product_eligible(product, promo_code)]
    else:
        eligible_products = products

    eligible_amount = sum((product.price for product in eligible_products), Decimal("0.00"))
    if eligible_amount <= 0:
        raise PromoCodeNotApplicableError
    if promo_code.min_order_amount is not None and eligible_amount < promo_code.min_order_amount:
        raise PromoCodeNotApplicableError(
            f"Промокод действует для подходящих товаров от {promo_code.min_order_amount} BYN.",
        )

    discount_amount = calculate_percent_discount(eligible_amount, promo_code.discount_percent)
    subtotal_amount = sum((product.price for product in products), Decimal("0.00"))
    if subtotal_amount - discount_amount <= 0:
        raise PromoCodeNotApplicableError("Промокод не может сделать заказ бесплатным.")

    return PromoCodeDiscount(
        promo_code=promo_code,
        code=promo_code.code,
        discount_percent=promo_code.discount_percent,
        eligible_amount=eligible_amount,
        discount_amount=discount_amount,
    )


def create_promo_code_redemption(
    *,
    order: Order,
    promo_discount: PromoCodeDiscount,
) -> PromoCodeRedemption:
    return PromoCodeRedemption.objects.create(
        promo_code=promo_discount.promo_code,
        order=order,
        user=order.user,
        email=order.email,
        subtotal_amount=order.subtotal_amount,
        eligible_amount=promo_discount.eligible_amount,
        discount_amount=promo_discount.discount_amount,
    )
