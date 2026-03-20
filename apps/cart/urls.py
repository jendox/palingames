from django.urls import path

from .views import CartPageView, cart_clear_view, cart_remove_view, cart_toggle_view

urlpatterns = [
    path("cart/", CartPageView.as_view(), name="cart"),
    path("cart/toggle/", cart_toggle_view, name="cart-toggle"),
    path("cart/remove/<int:product_id>/", cart_remove_view, name="cart-remove"),
    path("cart/clear/", cart_clear_view, name="cart-clear"),
]
