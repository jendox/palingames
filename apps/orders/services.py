from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.templatetags.static import static

from apps.access.services import get_user_product_access_ids
from apps.cart.services import get_cart_product_ids
from apps.core.consent import SESSION_KEY_ANALYTICS_STORAGE
from apps.core.logging import log_event
from apps.core.metrics import inc_order_created, observe_order_creation_duration
from apps.products.models import Product
from apps.products.pricing import format_price
from apps.promocodes.models import PromoCodeRedemption
from apps.promocodes.services import (
    PromoCodeDiscount,
    PromoCodeError,
    calculate_percent_discount,
    calculate_promo_code_discount,
    create_promo_code_redemption,
    normalize_promo_code,
)

from .models import Order, OrderItem
from .purchase_guards import build_pending_purchase_message, get_user_pending_order_item_by_product_ids

logger = logging.getLogger("apps.orders")
CHECKOUT_PROMO_SESSION_KEY = "checkout_promo_code"
CHECKOUT_IDEMPOTENCY_KEY_SESSION_KEY = "checkout_idempotency_key"
CHECKOUT_CART_FINGERPRINT_SESSION_KEY = "checkout_cart_fingerprint"


@dataclass(frozen=True)
class OrderCreationResult:
    order: Order
    created: bool


def _get_ordered_cart_products(request) -> list[Product]:
    product_ids = get_cart_product_ids(request)
    if not product_ids:
        return []

    products = (
        Product.objects.filter(id__in=product_ids)
        .prefetch_related("categories", "images")
        .in_bulk(product_ids)
    )
    return [products[product_id] for product_id in product_ids if product_id in products]


def _get_checkout_cart_fingerprint(request) -> str:
    return ",".join(str(product_id) for product_id in get_cart_product_ids(request))


def ensure_checkout_idempotency_key(request) -> str:
    cart_fingerprint = _get_checkout_cart_fingerprint(request)
    session_key = request.session.get(CHECKOUT_IDEMPOTENCY_KEY_SESSION_KEY, "")
    session_cart_fingerprint = request.session.get(CHECKOUT_CART_FINGERPRINT_SESSION_KEY, "")
    if session_key and session_cart_fingerprint == cart_fingerprint:
        return str(session_key)

    checkout_idempotency_key = str(uuid.uuid4())
    request.session[CHECKOUT_IDEMPOTENCY_KEY_SESSION_KEY] = checkout_idempotency_key
    request.session[CHECKOUT_CART_FINGERPRINT_SESSION_KEY] = cart_fingerprint
    request.session.modified = True
    return checkout_idempotency_key


def clear_checkout_idempotency_key(request) -> None:
    removed = False
    for session_key in (CHECKOUT_IDEMPOTENCY_KEY_SESSION_KEY, CHECKOUT_CART_FINGERPRINT_SESSION_KEY):
        if session_key in request.session:
            del request.session[session_key]
            removed = True
    if removed:
        request.session.modified = True


def get_order_by_checkout_idempotency_key(checkout_idempotency_key) -> Order | None:
    if not checkout_idempotency_key:
        return None
    return Order.objects.filter(checkout_idempotency_key=checkout_idempotency_key).first()


class OrderCreationBlockedError(ValueError):
    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.message = message
        self.reason = reason


def _prepare_order_items(
    order: Order,
    products: list[Product],
    promo_discount: PromoCodeDiscount | None = None,
) -> list[OrderItem]:
    order_items = []
    for product in products:
        first_category = product.categories.first()
        first_image = next(iter(product.images.all()), None)
        image_url = first_image.image.url if first_image else static("images/example-product-image-1.png")
        item_discount_amount = Decimal("0.00")
        discounted_line_total_amount = None
        promo_eligible = _is_product_eligible_for_discount(product, promo_discount)
        if promo_discount and promo_eligible:
            item_discount_amount = calculate_percent_discount(product.price, promo_discount.discount_percent)
            discounted_line_total_amount = product.price - item_discount_amount

        order_items.append(
            OrderItem(
                order=order,
                product=product,
                title_snapshot=product.title,
                category_snapshot=first_category.title if first_category else "",
                unit_price_amount=product.price,
                quantity=1,
                line_total_amount=product.price,
                promo_eligible=promo_eligible,
                discount_amount=item_discount_amount,
                discounted_line_total_amount=discounted_line_total_amount,
                product_slug_snapshot=product.slug,
                product_image_snapshot=image_url,
            ),
        )
    return order_items


def _calculate_order_discount(
    *,
    products: list[Product],
    request,
    email: str,
    promo_code: str,
) -> PromoCodeDiscount | None:
    normalized_code = normalize_promo_code(promo_code)
    if not normalized_code:
        return None
    return calculate_promo_code_discount(
        code=normalized_code,
        products=products,
        user=request.user,
        email=email,
    )


def _is_product_eligible_for_discount(product: Product, promo_discount: PromoCodeDiscount | None) -> bool:
    if promo_discount is None:
        return False

    promo_code = promo_discount.promo_code
    product_ids = {restricted_product.id for restricted_product in promo_code.products.all()}
    if product.id in product_ids:
        return True

    category_ids = {category.id for category in promo_code.categories.all()}
    if not product_ids and not category_ids:
        return True
    return any(category.id in category_ids for category in product.categories.all())


def _build_checkout_item(product: Product, promo_discount: PromoCodeDiscount | None) -> dict:
    first_category = product.categories.first()
    first_image = next(iter(product.images.all()), None)
    discount_price = None
    if _is_product_eligible_for_discount(product, promo_discount):
        discount_price = product.price - calculate_percent_discount(product.price, promo_discount.discount_percent)

    return {
        "product_id": product.id,
        "item_id": str(product.id),
        "item_name": product.title,
        "item_category": first_category.title if first_category else "",
        "item_variant": first_category.title if first_category else "",
        "title": product.title,
        "kind": first_category.title if first_category else "",
        "price": format_price(product.price, product.currency),
        "price_value": float(product.price),
        "discount_price": format_price(discount_price, product.currency) if discount_price is not None else "",
        "discount_price_value": float(discount_price) if discount_price is not None else None,
        "has_discount": discount_price is not None,
        "image_url": first_image.image.url if first_image else static("images/example-product-image-1.png"),
        "currency": product.currency,
        "quantity": 1,
    }


def set_checkout_promo_code(request, code: str) -> str:
    normalized_code = normalize_promo_code(code)
    request.session[CHECKOUT_PROMO_SESSION_KEY] = normalized_code
    request.session.modified = True
    return normalized_code


def clear_checkout_promo_code(request) -> None:
    if CHECKOUT_PROMO_SESSION_KEY in request.session:
        del request.session[CHECKOUT_PROMO_SESSION_KEY]
        request.session.modified = True


def get_checkout_promo_code(request) -> str:
    return normalize_promo_code(request.session.get(CHECKOUT_PROMO_SESSION_KEY, ""))


def get_checkout_order_context(
    request,
    *,
    email: str = "",
    promo_message: str = "",
    promo_message_level: str = "",
) -> dict:
    products = _get_ordered_cart_products(request)
    subtotal = sum((product.price for product in products), Decimal("0.00"))
    currency = products[0].currency if products else None
    promo_code = get_checkout_promo_code(request)
    promo_discount = None
    if promo_code:
        try:
            promo_discount = calculate_promo_code_discount(
                code=promo_code,
                products=products,
                user=request.user,
                email=email,
                require_email_limits=bool(email),
            )
        except PromoCodeError as exc:
            promo_message = promo_message or exc.message
            promo_message_level = promo_message_level or "error"
            clear_checkout_promo_code(request)
            promo_code = ""

    discount_amount = promo_discount.discount_amount if promo_discount else Decimal("0.00")
    total = subtotal - discount_amount

    if promo_discount and not promo_message:
        promo_message = "Промокод применен."
        promo_message_level = "success"

    cart_items = [_build_checkout_item(product, promo_discount) for product in products]

    return {
        "cart_items": cart_items,
        "cart_subtotal": format_price(subtotal, currency),
        "cart_subtotal_value": float(subtotal),
        "cart_total": format_price(total, currency),
        "cart_total_value": float(total),
        "cart_total_original": format_price(subtotal, currency) if promo_discount else "",
        "cart_discount": format_price(discount_amount, currency) if promo_discount else "",
        "cart_discount_value": float(discount_amount),
        "cart_currency": currency,
        "cart_count": len(products),
        "cart_product_ids": [product.id for product in products],
        "checkout_promo_code": promo_code,
        "checkout_promo_applied": promo_discount is not None,
        "checkout_promo_message": promo_message,
        "checkout_promo_message_level": promo_message_level,
        "checkout_analytics_items": [
            {
                "item_id": item["item_id"],
                "item_name": item["item_name"],
                "item_category": item["item_category"],
                "item_variant": item["item_variant"],
                "price": (
                    item["discount_price_value"]
                    if item["discount_price_value"] is not None
                    else item["price_value"]
                ),
                "quantity": item["quantity"],
            }
            for item in cart_items
        ],
    }


def _create_new_order_from_products(
    *,
    request,
    email: str,
    products: list[Product],
    promo_code: str,
    checkout_idempotency_key,
    checkout_type: str,
) -> Order:
    with transaction.atomic():
        if not products:
            msg = "Cannot create an order from an empty cart."
            raise ValueError(msg)

        if request.user.is_authenticated:
            purchased_ids = get_user_product_access_ids(
                request.user,
                product_ids=[product.id for product in products],
            )
            if purchased_ids:
                msg = "Cannot create an order for already purchased products."
                raise OrderCreationBlockedError(msg, reason="already_purchased")
            pending_order_item = get_user_pending_order_item_by_product_ids(
                request.user,
                product_ids=[product.id for product in products],
            )
            if pending_order_item is not None:
                raise OrderCreationBlockedError(
                    build_pending_purchase_message(
                        order=pending_order_item.order,
                        product_title=pending_order_item.title_snapshot,
                    ),
                    reason="pending_purchase",
                )

        subtotal_amount = sum((product.price for product in products), Decimal("0.00"))
        promo_discount = _calculate_order_discount(
            products=products,
            request=request,
            email=email,
            promo_code=promo_code,
        )
        discount_amount = promo_discount.discount_amount if promo_discount else Decimal("0.00")
        total_amount = subtotal_amount - discount_amount
        analytics_storage_consent = bool(request.session.get(SESSION_KEY_ANALYTICS_STORAGE, False))
        order = Order.objects.create(
            checkout_idempotency_key=checkout_idempotency_key,
            user=request.user if request.user.is_authenticated else None,
            email=email,
            source=Order.Source.PALINGAMES,
            checkout_type=checkout_type,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=subtotal_amount,
            promo_code=promo_discount.promo_code if promo_discount else None,
            promo_code_snapshot=promo_discount.code if promo_discount else "",
            discount_percent_snapshot=promo_discount.discount_percent if promo_discount else None,
            promo_eligible_amount=promo_discount.eligible_amount if promo_discount else Decimal("0.00"),
            discount_amount=discount_amount,
            total_amount=total_amount,
            currency=products[0].currency,
            items_count=len(products),
            analytics_storage_consent=analytics_storage_consent,
        )

        order_items = _prepare_order_items(order, products, promo_discount)
        OrderItem.objects.bulk_create(order_items)
        if promo_discount:
            create_promo_code_redemption(order=order, promo_discount=promo_discount)
            clear_checkout_promo_code(request)
        return order


def create_order_from_cart(
    *,
    request,
    email: str,
    promo_code: str = "",
    checkout_idempotency_key=None,
) -> OrderCreationResult:
    existing_order = get_order_by_checkout_idempotency_key(checkout_idempotency_key)
    if existing_order is not None:
        return OrderCreationResult(order=existing_order, created=False)

    products = _get_ordered_cart_products(request)
    checkout_type = (
        Order.CheckoutType.AUTHENTICATED if request.user.is_authenticated else Order.CheckoutType.GUEST
    )
    log_event(
        logger,
        logging.INFO,
        "order.creation.started",
        cart_items_count=len(products),
        checkout_type=checkout_type,
        user_id=request.user.id if request.user.is_authenticated else None,
    )
    started_at = perf_counter()
    try:
        try:
            order = _create_new_order_from_products(
                request=request,
                email=email,
                products=products,
                promo_code=promo_code,
                checkout_idempotency_key=checkout_idempotency_key,
                checkout_type=checkout_type,
            )
        except IntegrityError:
            existing_order = get_order_by_checkout_idempotency_key(checkout_idempotency_key)
            if existing_order is not None:
                return OrderCreationResult(order=existing_order, created=False)
            raise
    except OrderCreationBlockedError as exc:
        log_event(
            logger,
            logging.WARNING,
            "order.creation.blocked",
            checkout_type=checkout_type,
            cart_items_count=len(products),
            reason=exc.reason,
        )
        observe_order_creation_duration(
            checkout_type=checkout_type,
            result="blocked",
            duration_seconds=perf_counter() - started_at,
        )
        raise
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "order.creation.failed",
            exc_info=exc,
            checkout_type=checkout_type,
            cart_items_count=len(products),
            error_type=type(exc).__name__,
        )
        observe_order_creation_duration(
            checkout_type=checkout_type,
            result="error",
            duration_seconds=perf_counter() - started_at,
        )
        raise

    log_event(
        logger,
        logging.INFO,
        "order.creation.success",
        order_id=order.id,
        order_public_id=order.public_id,
        checkout_type=order.checkout_type,
        items_count=order.items_count,
        total_amount=order.total_amount,
        currency=order.currency,
    )
    inc_order_created(checkout_type=order.checkout_type, source=order.source)
    observe_order_creation_duration(
        checkout_type=order.checkout_type,
        result="success",
        duration_seconds=perf_counter() - started_at,
    )

    return OrderCreationResult(order=order, created=True)


def _manual_order_clear_totals(order: Order) -> None:
    PromoCodeRedemption.objects.filter(order=order).delete()
    order.subtotal_amount = Decimal("0.00")
    order.discount_amount = Decimal("0.00")
    order.total_amount = Decimal("0.00")
    order.items_count = 0
    order.promo_code = None
    order.promo_code_snapshot = ""
    order.discount_percent_snapshot = None
    order.promo_eligible_amount = Decimal("0.00")
    order.save(
        update_fields=[
            "subtotal_amount",
            "discount_amount",
            "total_amount",
            "items_count",
            "promo_code",
            "promo_code_snapshot",
            "discount_percent_snapshot",
            "promo_eligible_amount",
            "updated_at",
        ],
    )


def _manual_order_validate_items(items: list[OrderItem]) -> None:
    for item in items:
        if item.product_id is None:
            msg = "У каждой позиции должен быть выбран товар."
            raise ValidationError(msg)
    currencies = {item.product.currency for item in items}
    if len(currencies) != 1:
        msg = "Все товары в заказе должны быть в одной валюте."
        raise ValidationError(msg)


def _manual_order_expanded_products(items: list[OrderItem]) -> list[Product]:
    expanded: list[Product] = []
    for item in items:
        expanded.extend([item.product] * item.quantity)
    return expanded


def _manual_order_resolve_promo(
    order: Order,
    expanded_products: list[Product],
) -> PromoCodeDiscount | None:
    if not order.promo_code_id:
        return None
    try:
        return calculate_promo_code_discount(
            code=order.promo_code.code,
            products=expanded_products,
            user=order.user if order.user_id else AnonymousUser(),
            email=order.email,
            require_email_limits=True,
        )
    except PromoCodeError as exc:
        raise ValidationError(exc.message) from exc


def _manual_order_save_line_item(
    item: OrderItem,
    promo_discount: PromoCodeDiscount | None,
) -> tuple[Decimal, Decimal]:
    product = item.product
    first_category = product.categories.first()
    first_image = next(iter(product.images.all()), None)
    image_url = first_image.image.url if first_image else static("images/example-product-image-1.png")
    line_subtotal = product.price * item.quantity
    promo_eligible = _is_product_eligible_for_discount(product, promo_discount)
    if promo_discount and promo_eligible:
        unit_discount = calculate_percent_discount(product.price, promo_discount.discount_percent)
        line_discount = unit_discount * item.quantity
        discounted_line_total = line_subtotal - line_discount
    else:
        line_discount = Decimal("0.00")
        discounted_line_total = None

    item.title_snapshot = product.title
    item.category_snapshot = first_category.title if first_category else ""
    item.unit_price_amount = product.price
    item.line_total_amount = line_subtotal
    item.promo_eligible = promo_eligible
    item.discount_amount = line_discount
    item.discounted_line_total_amount = discounted_line_total
    item.product_slug_snapshot = product.slug
    item.product_image_snapshot = image_url
    item.save(
        update_fields=[
            "title_snapshot",
            "category_snapshot",
            "unit_price_amount",
            "line_total_amount",
            "promo_eligible",
            "discount_amount",
            "discounted_line_total_amount",
            "product_slug_snapshot",
            "product_image_snapshot",
            "updated_at",
        ],
    )
    return line_subtotal, line_discount


def _manual_order_apply_promo_to_header(order: Order, promo_discount: PromoCodeDiscount | None) -> None:
    if promo_discount:
        order.promo_code = promo_discount.promo_code
        order.promo_code_snapshot = promo_discount.code
        order.discount_percent_snapshot = promo_discount.discount_percent
        order.promo_eligible_amount = promo_discount.eligible_amount
    else:
        order.promo_code = None
        order.promo_code_snapshot = ""
        order.discount_percent_snapshot = None
        order.promo_eligible_amount = Decimal("0.00")


def recalculate_manual_order_from_items(*, order_id: int) -> None:
    """
    After admin edits to order lines or promo, recompute snapshots, per-line discounts and header totals.
    Only safe for unpaid CREATED orders (manual / off-site flow).
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.OrderStatus.CREATED:
            return

        items = list(
            order.items.select_related("product")
            .prefetch_related("product__categories", "product__images")
            .order_by("pk"),
        )

        if not items:
            _manual_order_clear_totals(order)
            return

        _manual_order_validate_items(items)
        expanded = _manual_order_expanded_products(items)
        promo_discount = _manual_order_resolve_promo(order, expanded)

        subtotal = Decimal("0.00")
        discount_sum = Decimal("0.00")
        items_count = 0
        for item in items:
            line_sub, line_disc = _manual_order_save_line_item(item, promo_discount)
            subtotal += line_sub
            discount_sum += line_disc
            items_count += item.quantity

        order.subtotal_amount = subtotal
        order.discount_amount = discount_sum
        order.total_amount = subtotal - discount_sum
        order.items_count = items_count
        order.currency = items[0].product.currency
        _manual_order_apply_promo_to_header(order, promo_discount)
        order.save(
            update_fields=[
                "subtotal_amount",
                "discount_amount",
                "total_amount",
                "items_count",
                "currency",
                "promo_code",
                "promo_code_snapshot",
                "discount_percent_snapshot",
                "promo_eligible_amount",
                "updated_at",
            ],
        )

        PromoCodeRedemption.objects.filter(order=order).delete()
        if promo_discount:
            create_promo_code_redemption(order=order, promo_discount=promo_discount)
