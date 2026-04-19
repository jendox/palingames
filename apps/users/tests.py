from urllib.parse import parse_qs, urlparse

from django.template.loader import render_to_string
from django.test import TestCase, override_settings


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
