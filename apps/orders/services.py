from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.templatetags.static import static

from apps.cart.services import get_cart_product_ids
from apps.core.logging import log_event
from apps.products.models import Product

from .models import Order, OrderItem

logger = logging.getLogger("apps.orders")


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


def _prepare_order_items(order: Order, products: list[Product]) -> list[OrderItem]:
    order_items = []
    for product in products:
        first_category = product.categories.first()
        first_image = next(iter(product.images.all()), None)
        image_url = first_image.image.url if first_image else static("images/example-product-image-1.png")
        order_items.append(
            OrderItem(
                order=order,
                product=product,
                title_snapshot=product.title,
                category_snapshot=first_category.title if first_category else "",
                unit_price_amount=product.price,
                quantity=1,
                line_total_amount=product.price,
                product_slug_snapshot=product.slug,
                product_image_snapshot=image_url,
            ),
        )
    return order_items


@transaction.atomic
def create_order_from_cart(*, request, email: str) -> Order:
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

        total_amount = sum((product.price for product in products), Decimal("0.00"))
        order = Order.objects.create(
            user=request.user if request.user.is_authenticated else None,
            email=email,
            source=Order.Source.PALINGAMES,
            checkout_type=checkout_type,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=total_amount,
            total_amount=total_amount,
            currency=products[0].currency,
            items_count=len(products),
        )

        order_items = _prepare_order_items(order, products)
        OrderItem.objects.bulk_create(order_items)
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

    return order
