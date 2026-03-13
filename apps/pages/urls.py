from django.urls import path

from . import views

urlpatterns = [
    path("", views.HomePageView.as_view(), name="home"),
    path("about/", views.AboutPageView.as_view(), name="about"),
    path("payment/", views.PaymentPageView.as_view(), name="payment"),
    path("product/", views.ProductPageView.as_view(), name="product"),
    path("cart/", views.CartPageView.as_view(), name="cart"),
    path("account/", views.AccountPageView.as_view(), name="account"),
]
