from django.urls import path

from .views import CheckoutPageView

urlpatterns = [
    path("checkout/", CheckoutPageView.as_view(), name="checkout"),
]
