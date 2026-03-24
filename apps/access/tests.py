from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.orders.models import Order
from apps.products.models import Product

from .models import GuestAccess
from .services import create_guest_access, mark_guest_access_used, resolve_guest_access


class GuestAccessModelTests(TestCase):
    def test_guest_access_is_active_when_not_used_not_revoked_and_not_expired(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив", slug="archive-access", price="10.00")
        guest_access = GuestAccess.objects.create(
            order=order,
            product=product,
            token_hash="a" * 64,
            email=order.email,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.assertTrue(guest_access.is_active)

    def test_guest_access_is_not_active_when_used(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 2", slug="archive-access-2", price="10.00")
        guest_access = GuestAccess.objects.create(
            order=order,
            product=product,
            token_hash="b" * 64,
            email=order.email,
            expires_at=timezone.now() + timedelta(hours=1),
            used_at=timezone.now(),
        )

        self.assertFalse(guest_access.is_active)


class GuestAccessServiceTests(TestCase):
    def test_create_guest_access_returns_raw_token_and_stores_hash(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 3", slug="archive-access-3", price="10.00")

        guest_access, raw_token = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
        )

        self.assertTrue(raw_token)
        self.assertEqual(len(guest_access.token_hash), 64)
        self.assertNotEqual(guest_access.token_hash, raw_token)

    def test_resolve_guest_access_returns_active_grant(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 4", slug="archive-access-4", price="10.00")
        guest_access, raw_token = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
        )

        resolved = resolve_guest_access(raw_token)

        self.assertEqual(resolved, guest_access)

    def test_mark_guest_access_used_sets_used_at(self):
        order = Order.objects.create(
            email="guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            subtotal_amount="10.00",
            total_amount="10.00",
            items_count=1,
        )
        product = Product.objects.create(title="Архив 5", slug="archive-access-5", price="10.00")
        guest_access, _ = create_guest_access(
            order=order,
            product=product,
            expires_in=timedelta(hours=24),
        )

        mark_guest_access_used(guest_access)
        guest_access.refresh_from_db()

        self.assertIsNotNone(guest_access.used_at)
