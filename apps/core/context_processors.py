from apps.products.pricing import get_currency_code


def default_currency(request):
    return {
        "default_currency_code": get_currency_code(None),
    }
