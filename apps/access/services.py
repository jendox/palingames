from apps.orders.models import Order

from .models import UserProductAccess


def get_user_product_access_ids(user, *, product_ids=None) -> set[int]:
    if not getattr(user, "is_authenticated", False):
        return set()

    queryset = UserProductAccess.objects.filter(user=user)
    if product_ids is not None:
        queryset = queryset.filter(product_id__in=product_ids)
    return set(queryset.values_list("product_id", flat=True))


def has_user_product_access(user, product_id: int) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return UserProductAccess.objects.filter(user=user, product_id=product_id).exists()


def grant_user_product_accesses(order: Order) -> None:
    if not order.user_id:
        return

    for item in order.items.select_related("product").all():
        UserProductAccess.objects.get_or_create(
            user=order.user,
            product=item.product,
            defaults={"order": order},
        )
