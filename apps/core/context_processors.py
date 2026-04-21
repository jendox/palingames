from apps.products.pricing import get_currency_code


def default_currency(request):
    return {
        "default_currency_code": get_currency_code(None),
    }


def analytics(request):
    from django.conf import settings

    return {
        "analytics_enabled": settings.ANALYTICS_ENABLED and bool(settings.GTM_ID),
        "gtm_id": settings.GTM_ID,
    }
