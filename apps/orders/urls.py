from django.urls import path

from .views import CheckoutPageView, checkout_promo_apply_view, checkout_promo_remove_view

urlpatterns = [
    path("checkout/", CheckoutPageView.as_view(), name="checkout"),
    path("checkout/promo/apply/", checkout_promo_apply_view, name="checkout-promo-apply"),
    path("checkout/promo/remove/", checkout_promo_remove_view, name="checkout-promo-remove"),
]
