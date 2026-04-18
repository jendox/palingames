from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.templatetags.static import static

from apps.access.services import get_user_product_access_ids
from apps.cart.services import get_cart_product_ids
from apps.core.logging import log_event
from apps.core.metrics import inc_order_created
from apps.products.models import Product
from apps.products.pricing import format_price
from apps.promocodes.services import (
    PromoCodeDiscount,
    PromoCodeError,
    calculate_percent_discount,
    calculate_promo_code_discount,
    create_promo_code_redemption,
    normalize_promo_code,
)

from .models import Order, OrderItem

logger = logging.getLogger("apps.orders")
CHECKOUT_PROMO_SESSION_KEY = "checkout_promo_code"


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
        "title": product.title,
        "kind": first_category.title if first_category else "",
        "price": format_price(product.price, product.currency),
        "discount_price": format_price(discount_price, product.currency) if discount_price is not None else "",
        "has_discount": discount_price is not None,
        "image_url": first_image.image.url if first_image else static("images/example-product-image-1.png"),
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

    return {
        "cart_items": [_build_checkout_item(product, promo_discount) for product in products],
        "cart_subtotal": format_price(subtotal, currency),
        "cart_total": format_price(total, currency),
        "cart_total_original": format_price(subtotal, currency) if promo_discount else "",
        "cart_discount": format_price(discount_amount, currency) if promo_discount else "",
        "cart_count": len(products),
        "cart_product_ids": [product.id for product in products],
        "checkout_promo_code": promo_code,
        "checkout_promo_applied": promo_discount is not None,
        "checkout_promo_message": promo_message,
        "checkout_promo_message_level": promo_message_level,
    }


@transaction.atomic
def create_order_from_cart(*, request, email: str, promo_code: str = "") -> Order:
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
    try:
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
                raise ValueError(msg)

        subtotal_amount = sum((product.price for product in products), Decimal("0.00"))
        promo_discount = _calculate_order_discount(
            products=products,
            request=request,
            email=email,
            promo_code=promo_code,
        )
        discount_amount = promo_discount.discount_amount if promo_discount else Decimal("0.00")
        total_amount = subtotal_amount - discount_amount
        order = Order.objects.create(
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
        )

        order_items = _prepare_order_items(order, products, promo_discount)
        OrderItem.objects.bulk_create(order_items)
        if promo_discount:
            create_promo_code_redemption(order=order, promo_discount=promo_discount)
            clear_checkout_promo_code(request)
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

    return order
