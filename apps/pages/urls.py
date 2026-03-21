from django.urls import path

from . import views

urlpatterns = [
    path("", views.HomePageView.as_view(), name="home"),
    path("about/", views.AboutPageView.as_view(), name="about"),
    path("payment/", views.PaymentPageView.as_view(), name="payment"),
    path("custom-game/", views.CustomGamePageView.as_view(), name="custom-game"),
    path("account/", views.AccountPageView.as_view(), name="account"),
]
