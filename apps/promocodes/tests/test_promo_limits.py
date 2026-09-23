import threading
from decimal import Decimal

from django.db import connection, transaction
from django.test import TransactionTestCase

from apps.orders.models import Order
from apps.products.models import Category, Currency, Product
from apps.promocodes.models import PromoCode
from apps.promocodes.services import (
    PromoCodeLimitExceededError,
    PromoValidationOptions,
    calculate_promo_code_discount,
)


class PromoCodeLimitTests(TransactionTestCase):
    def setUp(self):
        category = Category.objects.create(title="Cat", slug="cat-promo-limit")
        self.product = Product.objects.create(
            title="Product",
            slug="product-promo-limit",
            price=Decimal("10.00"),
            currency=Currency.BYN,
            is_published=True,
        )
        self.product.categories.add(category)

    def test_max_total_blocks_second_unpaid_order(self):
        promo = PromoCode.objects.create(code="ONE", discount_percent=10, max_total_redemptions=1)
        Order.objects.create(
            email="a@example.com",
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
            promo_code=promo,
            subtotal_amount=Decimal("10.00"),
            discount_amount=Decimal("1.00"),
            total_amount=Decimal("9.00"),
            items_count=1,
        )

        with self.assertRaises(PromoCodeLimitExceededError):
            calculate_promo_code_discount(
                code="ONE",
                products=[self.product],
                user=None,
                email="b@example.com",
            )

    def test_concurrent_checkout_respects_max_total_redemptions(self):
        promo = PromoCode.objects.create(code="RACE", discount_percent=10, max_total_redemptions=1)
        barrier = threading.Barrier(2)
        results: list[str | None] = []

        def attempt(email: str) -> None:
            connection.close()

            try:
                barrier.wait(timeout=5)
                with transaction.atomic():
                    calculate_promo_code_discount(
                        code="RACE",
                        products=[self.product],
                        user=None,
                        email=email,
                        validation=PromoValidationOptions(lock_promo=True),
                    )
                    Order.objects.create(
                        email=email,
                        source=Order.Source.PALINGAMES,
                        checkout_type=Order.CheckoutType.GUEST,
                        status=Order.OrderStatus.CREATED,
                        promo_code=promo,
                        subtotal_amount=Decimal("10.00"),
                        discount_amount=Decimal("1.00"),
                        total_amount=Decimal("9.00"),
                        items_count=1,
                    )
                results.append("ok")
            except PromoCodeLimitExceededError:
                results.append("limit")
            except Exception as exc:  # pragma: no cover - test harness
                results.append(type(exc).__name__)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=attempt, args=("one@example.com",)),
            threading.Thread(target=attempt, args=("two@example.com",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(sorted(results), ["limit", "ok"])
        self.assertEqual(
            Order.objects.filter(promo_code=promo).exclude(
                status__in=[Order.OrderStatus.CANCELED, Order.OrderStatus.FAILED],
            ).count(),
            1,
        )
