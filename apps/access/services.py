from apps.orders.models import Order

from .models import UserProductAccess


def grant_user_product_accesses(order: Order) -> None:
    if not order.user_id:
        return

    for item in order.items.select_related("product").all():
        UserProductAccess.objects.get_or_create(
            user=order.user,
            product=item.product,
            defaults={"order": order},
        )
