from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase, override_settings

from apps.access.models import UserProductAccess
from apps.orders.models import Order, OrderItem
from apps.products.models import Product
from apps.promocodes.models import PromoCode

SOCIALACCOUNT_TEST_PROVIDERS = {
    "google": {
        "APPS": [
            {
                "client_id": "google-client-id",
                "secret": "google-secret",
                "key": "",
                "settings": {
                    "scope": ["profile", "email"],
                    "auth_params": {"access_type": "online"},
                },
            },
        ],
    },
    "yandex": {
        "APPS": [
            {
                "client_id": "yandex-client-id",
                "secret": "yandex-secret",
                "key": "",
            },
        ],
    },
}


class SocialLoginTests(TestCase):
    @override_settings(SOCIALACCOUNT_PROVIDERS=SOCIALACCOUNT_TEST_PROVIDERS)
    def test_social_buttons_enable_google_and_yandex_only(self):
        rendered = render_to_string("components/modals/_social_buttons.html")

        self.assertIn('data-social-login-provider="google"', rendered)
        self.assertIn('data-social-login-provider="yandex"', rendered)
        self.assertNotIn('data-social-login-provider="vk"', rendered)
        self.assertNotIn('data-social-login-provider="telegram"', rendered)

    @override_settings(SOCIALACCOUNT_PROVIDERS=SOCIALACCOUNT_TEST_PROVIDERS)
    def test_social_provider_redirects_to_configured_provider(self):
        expected_hosts = {
            "google": "accounts.google.com",
            "yandex": "oauth.yandex.com",
        }

        for provider, expected_host in expected_hosts.items():
            with self.subTest(provider=provider):
                response = self.client.post(
                    "/_allauth/browser/v1/auth/provider/redirect",
                    {
                        "provider": provider,
                        "process": "login",
                        "callback_url": "/",
                    },
                    HTTP_HOST="127.0.0.1:8000",
                )

                self.assertEqual(response.status_code, 302)
                location = urlparse(response["Location"])
                self.assertEqual(location.netloc, expected_host)
                redirect_uri = parse_qs(location.query)["redirect_uri"][0]
                self.assertEqual(
                    urlparse(redirect_uri).path,
                    f"/accounts/{provider}/login/callback/",
                )

    @override_settings(SOCIALACCOUNT_PROVIDERS=SOCIALACCOUNT_TEST_PROVIDERS)
    def test_social_provider_redirect_respects_forwarded_https(self):
        response = self.client.post(
            "/_allauth/browser/v1/auth/provider/redirect",
            {
                "provider": "google",
                "process": "login",
                "callback_url": "/",
            },
            HTTP_HOST="example.ngrok-free.app",
            HTTP_X_FORWARDED_PROTO="https",
        )

        self.assertEqual(response.status_code, 302)
        location = urlparse(response["Location"])
        redirect_uri = parse_qs(location.query)["redirect_uri"][0]
        parsed_redirect_uri = urlparse(redirect_uri)
        self.assertEqual(parsed_redirect_uri.scheme, "https")
        self.assertEqual(parsed_redirect_uri.netloc, "example.ngrok-free.app")


class GuestOrderMergeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="merge@example.com",
            password="test-pass-123",
        )
        cls.product = Product.objects.create(
            title="Merge product",
            slug="merge-product",
            price=Decimal("25.00"),
        )

    def test_email_confirmation_merges_guest_orders_and_grants_access(self):
        guest_order = Order.objects.create(
            email=self.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        OrderItem.objects.create(
            order=guest_order,
            product=self.product,
            title_snapshot=self.product.title,
            category_snapshot="",
            unit_price_amount=self.product.price,
            quantity=1,
            line_total_amount=self.product.price,
            product_slug_snapshot=self.product.slug,
            product_image_snapshot="https://example.com/product.png",
        )
        email_address = EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )

        email_confirmed.send(sender=EmailAddress, request=None, email_address=email_address)

        guest_order.refresh_from_db()
        self.assertEqual(guest_order.user, self.user)
        self.assertTrue(
            UserProductAccess.objects.filter(
                user=self.user,
                product=self.product,
                order=guest_order,
            ).exists(),
        )

    def test_email_confirmation_merges_guest_reward_promo_to_user(self):
        guest_order = Order.objects.create(
            email=self.user.email,
            source=Order.Source.PALINGAMES,
            checkout_type=Order.CheckoutType.GUEST,
            status=Order.OrderStatus.PAID,
            subtotal_amount=Decimal("25.00"),
            total_amount=Decimal("25.00"),
            items_count=1,
        )
        reward_promo = PromoCode.objects.create(
            code="GUESTREWARD",
            discount_percent=10,
            is_reward=True,
            assigned_email=self.user.email,
        )
        guest_order.reward_promo_code = reward_promo
        guest_order.save(update_fields=["reward_promo_code"])
        email_address = EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )

        email_confirmed.send(sender=EmailAddress, request=None, email_address=email_address)

        reward_promo.refresh_from_db()
        guest_order.refresh_from_db()
        self.assertEqual(guest_order.user, self.user)
        self.assertEqual(reward_promo.assigned_user, self.user)
