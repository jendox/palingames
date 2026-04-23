from django.templatetags.static import static
from django.urls import reverse

from apps.core.seo import build_seo_context
from apps.products.pricing import get_currency_code


def default_currency(request):
    return {
        "default_currency_code": get_currency_code(None),
    }


def analytics(request):
    from django.conf import settings

    analytics_on = settings.ANALYTICS_ENABLED and bool(settings.GTM_ID)
    cookie_consent_client_config = None
    if analytics_on:
        cookie_consent_client_config = {
            "consentApiUrl": reverse("cookie-consent-api"),
            "policyVersion": settings.COOKIE_CONSENT_POLICY_VERSION,
            "maxAgeSeconds": settings.COOKIE_CONSENT_MAX_AGE_SECONDS,
            "gtmId": settings.GTM_ID,
            "analyticsJsUrl": static("js/analytics.js"),
        }

    return {
        "analytics_enabled": analytics_on,
        "gtm_id": settings.GTM_ID,
        "cookie_consent_policy_version": settings.COOKIE_CONSENT_POLICY_VERSION,
        "cookie_consent_max_age_seconds": settings.COOKIE_CONSENT_MAX_AGE_SECONDS,
        "cookie_consent_ui_enabled": analytics_on,
        "yandex_metrika_id": settings.YANDEX_METRIKA_ID,
        "cookie_consent_client_config": cookie_consent_client_config,
    }


def seo_defaults(request):
    return build_seo_context(
        title="PaliGames",
        canonical_url=request.path,
    )
