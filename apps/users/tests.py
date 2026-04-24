import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed, password_reset, user_signed_up
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.contrib.messages import constants as message_constants
from django.core.cache import caches
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings

from apps.access.models import UserProductAccess
from apps.orders.models import Order, OrderItem
from apps.products.models import Product
from apps.promocodes.models import PromoCode
from apps.users.models import PersonalDataProcessingConsentLog
from apps.users.personal_data_consent import PersonalDataContext, get_client_ip_and_ua, record_personal_data_consent

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


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PERSONAL_DATA_POLICY_VERSION=7,
)
class PersonalDataConsentHeadlessTests(TestCase):
    """Headless signup writes PersonalDataProcessingConsentLog when privacy_consent is true."""

    def setUp(self):
        super().setUp()
        caches["rate_limit"].clear()

    def test_signup_without_privacy_consent_returns_400(self):
        response = self.client.post(
            "/_allauth/browser/v1/auth/signup",
            data=json.dumps(
                {
                    "email": "no-privacy@example.com",
                    "password": "test-password-123",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content.decode())
        params = {e.get("param") for e in payload.get("errors", [])}
        self.assertIn("privacy_consent", params)
        self.assertEqual(PersonalDataProcessingConsentLog.objects.filter(email="no-privacy@example.com").count(), 0)

    def test_signup_with_privacy_consent_creates_log(self):
        email = "with-privacy@example.com"
        response = self.client.post(
            "/_allauth/browser/v1/auth/signup",
            data=json.dumps(
                {
                    "email": email,
                    "password": "test-password-123",
                    "privacy_consent": True,
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        log = PersonalDataProcessingConsentLog.objects.get(email=email)
        self.assertTrue(log.granted)
        self.assertEqual(log.source, PersonalDataProcessingConsentLog.Source.REGISTRATION_PASSWORD)
        self.assertEqual(log.policy_version, 7)
        self.assertIsNotNone(log.user_id)

    def test_record_personal_data_consent_normalizes_email(self):
        ctx = PersonalDataContext(
            email="  Normalize@Example.COM ",
            source=PersonalDataProcessingConsentLog.Source.GUEST_CHECKOUT,
        )
        record_personal_data_consent(ctx)
        log = PersonalDataProcessingConsentLog.objects.get()
        self.assertEqual(log.email, "normalize@example.com")

    def test_get_client_ip_prefers_first_x_forwarded_for_hop(self):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="203.0.113.1, 10.0.0.1")
        ip, ua = get_client_ip_and_ua(request)
        self.assertEqual(ip, "203.0.113.1")
        self.assertEqual(ua, "")

    def test_get_client_ip_falls_back_to_remote_addr(self):
        request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.2")
        ip, ua = get_client_ip_and_ua(request)
        self.assertEqual(ip, "198.51.100.2")
        self.assertEqual(ua, "")

    def test_get_client_ip_truncates_user_agent(self):
        long_ua = "x" * 300
        request = RequestFactory().get("/", HTTP_USER_AGENT=long_ua)
        ip, ua = get_client_ip_and_ua(request)
        self.assertEqual(ip, "127.0.0.1")
        self.assertEqual(len(ua), 256)


class HeadlessLoginRememberTests(TestCase):
    """Session expiry on headless password login respects remember / ACCOUNT_SESSION_REMEMBER."""

    def setUp(self):
        super().setUp()
        caches["rate_limit"].clear()

    def _create_verified_user(self, email: str, password: str):
        user = get_user_model().objects.create_user(email=email, password=password)
        EmailAddress.objects.create(
            user=user,
            email=email,
            primary=True,
            verified=True,
        )
        return user

    @override_settings(SESSION_COOKIE_AGE=99_999)
    def test_remember_true_sets_expiry_to_session_cookie_age(self):
        self._create_verified_user("remember-true@example.com", "test-pass-123")
        response = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps(
                {
                    "email": "remember-true@example.com",
                    "password": "test-pass-123",
                    "remember": True,
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get("_session_expiry"), 99_999)

    def test_remember_false_sets_browser_session_expiry(self):
        self._create_verified_user("remember-false@example.com", "test-pass-123")
        response = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps(
                {
                    "email": "remember-false@example.com",
                    "password": "test-pass-123",
                    "remember": False,
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        # set_expiry(0): cookie expires on browser close; get_expiry_age() falls back to
        # SESSION_COOKIE_AGE because 0 is falsy — assert the stored flag instead.
        self.assertEqual(self.client.session.get("_session_expiry"), 0)

    def test_missing_remember_defaults_to_browser_session(self):
        self._create_verified_user("remember-missing@example.com", "test-pass-123")
        response = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps(
                {
                    "email": "remember-missing@example.com",
                    "password": "test-pass-123",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get("_session_expiry"), 0)

    @override_settings(ACCOUNT_SESSION_REMEMBER=True, SESSION_COOKIE_AGE=42)
    def test_account_session_remember_true_overrides_request(self):
        self._create_verified_user("forced-remember@example.com", "test-pass-123")
        response = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps(
                {
                    "email": "forced-remember@example.com",
                    "password": "test-pass-123",
                    "remember": False,
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get("_session_expiry"), 42)

    @override_settings(ACCOUNT_SESSION_REMEMBER=False)
    def test_account_session_remember_false_overrides_request(self):
        self._create_verified_user("forced-session@example.com", "test-pass-123")
        response = self.client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps(
                {
                    "email": "forced-session@example.com",
                    "password": "test-pass-123",
                    "remember": True,
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get("_session_expiry"), 0)


@override_settings(PERSONAL_DATA_POLICY_VERSION=7)
class SocialAccountAdapterConsentTests(TestCase):
    def test_save_user_records_oauth_first_login_consent(self):
        from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

        from apps.users.social_adapter import SocialAccountAdapter

        user = get_user_model().objects.create_user(
            email="oauth-user@example.com",
            password="unused-for-oauth-test",
        )
        request = RequestFactory().post("/_allauth/browser/v1/auth/provider/redirect")
        request.META["REMOTE_ADDR"] = "203.0.113.5"

        adapter = SocialAccountAdapter()
        sociallogin = SimpleNamespace()

        with patch.object(DefaultSocialAccountAdapter, "save_user", return_value=user) as mock_super:
            result = adapter.save_user(request, sociallogin, None)

        mock_super.assert_called_once_with(request, sociallogin, None)
        self.assertEqual(result, user)

        log = PersonalDataProcessingConsentLog.objects.get(email=user.email)
        self.assertEqual(log.source, PersonalDataProcessingConsentLog.Source.OAUTH_FIRST_LOGIN)
        self.assertEqual(log.policy_version, 7)
        self.assertEqual(str(log.ip), "203.0.113.5")
        self.assertTrue(log.granted)


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


class AuthLoggingSignalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="auth-logs@example.com",
            password="test-pass-123",
        )
        cls.product = Product.objects.create(
            title="Auth logs product",
            slug="auth-logs-product",
            price=Decimal("25.00"),
        )

    @patch("apps.users.signals.log_event")
    def test_user_logged_in_logs_social_login_success(self, log_event_mock):
        request = SimpleNamespace(
            session={
                "account_authentication_methods": [
                    {
                        "method": "socialaccount",
                        "provider": "google",
                    },
                ],
            },
        )

        user_logged_in.send(sender=self.user.__class__, request=request, user=self.user)

        self.assertEqual(log_event_mock.call_args_list[0].args[2], "auth.social.login.succeeded")
        self.assertEqual(log_event_mock.call_args_list[0].kwargs["provider"], "google")

    @patch("apps.users.signals.inc_auth_login_success")
    def test_user_logged_in_increments_login_success_metric(self, inc_auth_login_success_mock):
        request = SimpleNamespace(
            session={
                "account_authentication_methods": [
                    {
                        "method": "socialaccount",
                        "provider": "google",
                    },
                ],
            },
        )

        user_logged_in.send(sender=self.user.__class__, request=request, user=self.user)

        inc_auth_login_success_mock.assert_called_once_with(method="socialaccount")

    @patch("apps.users.signals.log_event")
    def test_user_login_failed_logs_failed_auth(self, log_event_mock):
        user_login_failed.send(
            sender=self.user.__class__,
            credentials={"email": self.user.email, "password": "wrong"},
            request=SimpleNamespace(path="/_allauth/browser/v1/auth/login"),
        )

        log_event_mock.assert_called_once()
        self.assertEqual(log_event_mock.call_args.args[2], "auth.login.failed")
        self.assertEqual(log_event_mock.call_args.kwargs["login_field"], "email")

    @patch("apps.users.signals.inc_auth_login_failed")
    def test_user_login_failed_increments_metric(self, inc_auth_login_failed_mock):
        user_login_failed.send(
            sender=self.user.__class__,
            credentials={"email": self.user.email, "password": "wrong"},
            request=SimpleNamespace(path="/_allauth/browser/v1/auth/login"),
        )

        inc_auth_login_failed_mock.assert_called_once_with(login_field="email")

    @patch("apps.users.signals.log_event")
    def test_user_signed_up_logs_signup_success(self, log_event_mock):
        sociallogin = Mock()
        sociallogin.account.provider = "google"

        user_signed_up.send(
            sender=self.user.__class__,
            request=None,
            user=self.user,
            sociallogin=sociallogin,
        )

        log_event_mock.assert_called_once()
        self.assertEqual(log_event_mock.call_args.args[2], "auth.signup.succeeded")
        self.assertEqual(log_event_mock.call_args.kwargs["method"], "socialaccount")
        self.assertEqual(log_event_mock.call_args.kwargs["provider"], "google")

    @patch("apps.users.signals.inc_auth_signup_success")
    def test_user_signed_up_increments_signup_success_metric(self, inc_auth_signup_success_mock):
        sociallogin = Mock()
        sociallogin.account.provider = "google"

        user_signed_up.send(
            sender=self.user.__class__,
            request=None,
            user=self.user,
            sociallogin=sociallogin,
        )

        inc_auth_signup_success_mock.assert_called_once_with(method="socialaccount")

    @patch("apps.users.signals.log_event")
    def test_password_reset_logs_completed_event(self, log_event_mock):
        password_reset.send(sender=self.user.__class__, request=None, user=self.user)

        log_event_mock.assert_called_once()
        self.assertEqual(log_event_mock.call_args.args[2], "auth.password_reset.completed")
        self.assertEqual(log_event_mock.call_args.kwargs["user_id"], self.user.id)

    @patch("apps.users.signals.inc_auth_password_reset_completed")
    def test_password_reset_increments_completed_metric(self, inc_auth_password_reset_completed_mock):
        password_reset.send(sender=self.user.__class__, request=None, user=self.user)

        inc_auth_password_reset_completed_mock.assert_called_once_with()

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

    def test_login_merges_guest_orders_for_verified_email(self):
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
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )

        user_logged_in.send(sender=self.user.__class__, request=None, user=self.user)

        guest_order.refresh_from_db()
        self.assertEqual(guest_order.user, self.user)
        self.assertTrue(
            UserProductAccess.objects.filter(
                user=self.user,
                product=self.product,
                order=guest_order,
            ).exists(),
        )

    @patch("apps.orders.guest_merge.inc_guest_orders_merged")
    def test_email_confirmation_increments_guest_merge_metric(self, inc_guest_orders_merged_mock):
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

        inc_guest_orders_merged_mock.assert_called_once_with(count=1)


class AccountAdapterMetricsTests(TestCase):
    @patch("apps.users.adapters.inc_auth_password_reset_requested")
    def test_send_password_reset_mail_increments_metric(self, inc_auth_password_reset_requested_mock):
        user = get_user_model().objects.create_user(
            email="reset-metric@example.com",
            password="test-pass-123",
        )

        with patch("allauth.account.adapter.DefaultAccountAdapter.send_password_reset_mail") as super_mock:
            from apps.users.adapters import AccountAdapter

            adapter = AccountAdapter(request=None)
            adapter.send_password_reset_mail(user, user.email, {"request": None})

        inc_auth_password_reset_requested_mock.assert_called_once_with()
        super_mock.assert_called_once()

    def test_add_message_skips_headless_requests(self):
        from apps.users.adapters import AccountAdapter

        adapter = AccountAdapter(request=None)
        request = RequestFactory().post("/_allauth/browser/v1/auth/login")

        with patch("allauth.account.adapter.DefaultAccountAdapter.add_message") as super_mock:
            adapter.add_message(
                request,
                message_constants.SUCCESS,
                message_template="account/messages/logged_in.txt",
            )

        super_mock.assert_not_called()

    def test_add_message_skips_logged_in_on_non_headless_paths(self):
        from apps.users.adapters import AccountAdapter

        adapter = AccountAdapter(request=None)
        request = RequestFactory().get("/account/")

        with patch("allauth.account.adapter.DefaultAccountAdapter.add_message") as super_mock:
            adapter.add_message(
                request,
                message_constants.SUCCESS,
                message_template="account/messages/logged_in.txt",
            )

        super_mock.assert_not_called()

    def test_add_message_keeps_other_templates_on_non_headless_paths(self):
        from apps.users.adapters import AccountAdapter

        adapter = AccountAdapter(request=None)
        request = RequestFactory().get("/account/")

        with patch("allauth.account.adapter.DefaultAccountAdapter.add_message") as super_mock:
            adapter.add_message(
                request,
                message_constants.SUCCESS,
                message_template="account/messages/password_changed.txt",
            )

        super_mock.assert_called_once_with(
            request,
            message_constants.SUCCESS,
            message_template="account/messages/password_changed.txt",
            message_context=None,
            extra_tags="",
            message=None,
        )
