from django.urls import path

from apps.custom_games.views import CustomGamePageView

from . import views

urlpatterns = [
    path("", views.HomePageView.as_view(), name="home"),
    path("about/", views.AboutPageView.as_view(), name="about"),
    path("payment/", views.PaymentPageView.as_view(), name="payment"),
    path("custom-game/", CustomGamePageView.as_view(), name="custom-game"),
    path("privacy/", views.PrivacyPolicyPageView.as_view(), name="privacy-policy"),
    path("cookies/", views.CookiePolicyPageView.as_view(), name="cookie-policy"),
    path("account/", views.AccountPageView.as_view(), name="account"),
]
