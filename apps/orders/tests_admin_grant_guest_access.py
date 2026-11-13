from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.access.models import GuestAccess
from apps.access.services import create_guest_access, resolve_guest_access
from apps.core.admin_site import admin_site
from apps.core.models import OrderSource
from apps.notifications.admin import NotificationOutboxAdmin
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import NotificationType
from apps.orders.admin import OrderAdmin
from apps.orders.models import Order, OrderItem
from apps.products.models import Product

ENCRYPTION_TEST_SETTINGS = {
    "APP_DATA_ENCRYPTION_KEY": "5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A=",
}


@override_settings(**ENCRYPTION_TEST_SETTINGS)
class OrderAdminGrantGuestAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            email="staff@example.com",
            password="staff-pass-123",
            is_staff=True,
            is_superuser=True,
        )
        cls.viewer = get_user_model().objects.create_user(
            email="viewer@example.com",
            password="viewer-pass-123",
            is_staff=True,
        )
        cls.product = Product.objects.create(title="Grant item", slug="grant-item", price=Decimal("12.00"))
        cls.user = get_user_model().objects.create_user(email="guest-grant@example.com", password="test-pass-123")

    def _create_paid_guest_order(self, *, user=None, status=Order.OrderStatus.PAID) -> Order:
        order = Order.objects.create(
            email="guest-grant@example.com",
            source=OrderSource.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=status,
            user=user,
            subtotal_amount=Decimal("12.00"),
            total_amount=Decimal("12.00"),
            items_count=1,
            paid_at=timezone.now(),
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
        )
        return order

    def test_grant_button_visible_only_for_paid_guest_with_items(self):
        order = self._create_paid_guest_order()

        self.assertTrue(OrderAdmin._can_grant_guest_access_from_admin(order))

        created = Order.objects.create(
            email="created-guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.CREATED,
            subtotal_amount=Decimal("12.00"),
            total_amount=Decimal("12.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=created,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
        )
        self.assertFalse(OrderAdmin._can_grant_guest_access_from_admin(created))

        auth = Order.objects.create(
            email="auth@example.com",
            checkout_type=Order.CheckoutType.AUTHENTICATED,
            status=Order.OrderStatus.PAID,
            user=self.user,
            subtotal_amount=Decimal("12.00"),
            total_amount=Decimal("12.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=auth,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
        )
        self.assertFalse(OrderAdmin._can_grant_guest_access_from_admin(auth))

        empty = Order.objects.create(
            email="empty@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            items_count=0,
        )
        self.assertFalse(OrderAdmin._can_grant_guest_access_from_admin(empty))

    def test_get_confirmation_page_does_not_modify_database(self):
        order = self._create_paid_guest_order()
        guest_before = GuestAccess.objects.count()
        outbox_before = NotificationOutbox.objects.count()

        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:orders_order_grant_guest_access", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "прежние ссылки перестанут работать")
        self.assertEqual(GuestAccess.objects.count(), guest_before)
        self.assertEqual(NotificationOutbox.objects.count(), outbox_before)

    @patch("apps.access.services.enqueue_notification_outbox")
    def test_post_grants_access_and_queues_email(self, enqueue_mock):
        order = self._create_paid_guest_order(user=self.user)

        self.client.force_login(self.staff)
        url = reverse("admin:orders_order_grant_guest_access", args=[order.pk])
        response = self.client.post(url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(GuestAccess.objects.filter(order=order).count(), 1)
        outbox = NotificationOutbox.objects.get(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            object_id=order.id,
        )
        enqueue_mock.assert_called_once_with(outbox)
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("поставлено в очередь" in m.lower() for m in messages))

        change = self.client.get(reverse("admin:orders_order_change", args=[order.pk]))
        self.assertContains(change, "Выдать гостевой доступ")

    def test_repeat_post_rotates_guest_token(self):
        order = self._create_paid_guest_order()
        guest_access, raw_token = create_guest_access(
            order=order,
            product=self.product,
            expires_in=timedelta(hours=24),
        )
        self.assertIsNotNone(resolve_guest_access(raw_token))

        self.client.force_login(self.staff)
        self.client.post(reverse("admin:orders_order_grant_guest_access", args=[order.pk]))

        self.assertIsNone(resolve_guest_access(raw_token))
        guest_access.refresh_from_db()
        self.assertEqual(GuestAccess.objects.filter(order=order).count(), 1)

    def test_user_without_change_permission_gets_403(self):
        order = self._create_paid_guest_order()
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("admin:orders_order_grant_guest_access", args=[order.pk]))

        self.assertEqual(response.status_code, 403)


@override_settings(**ENCRYPTION_TEST_SETTINGS)
class NotificationOutboxAdminResendGuestAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            email="staff-outbox@example.com",
            password="staff-pass-123",
            is_staff=True,
            is_superuser=True,
        )
        cls.product = Product.objects.create(title="Outbox item", slug="outbox-grant-item", price=Decimal("9.00"))

    def test_resend_guest_download_action_still_queues_new_outbox(self):
        order = Order.objects.create(
            email="outbox-guest@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("9.00"),
            total_amount=Decimal("9.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
        )
        outbox = NotificationOutbox.objects.create(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            recipient=order.email,
            payload_encrypted=b"legacy",
            status=NotificationOutbox.Status.SENT,
            content_type=ContentType.objects.get_for_model(Order),
            object_id=order.id,
        )

        request = RequestFactory().post("/admin/")
        request.user = self.staff
        request.session = "session"
        request._messages = FallbackStorage(request)
        admin = NotificationOutboxAdmin(NotificationOutbox, admin_site=admin_site)

        with patch("apps.access.services.enqueue_notification_outbox") as enqueue_mock:
            admin.resend_guest_download_emails(request, NotificationOutbox.objects.filter(pk=outbox.pk))

        self.assertEqual(GuestAccess.objects.filter(order=order).count(), 1)
        new_outbox = (
            NotificationOutbox.objects.filter(
                notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
                object_id=order.id,
            )
            .exclude(pk=outbox.pk)
            .get()
        )
        enqueue_mock.assert_called_once_with(new_outbox)
