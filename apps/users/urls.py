from allauth.headless.constants import Client
from django.urls import include, path

from apps.users.headless_login import RememberMeLoginView

urlpatterns = [
    path("accounts/", include("allauth.urls")),
    path(
        "_allauth/browser/v1/auth/login",
        RememberMeLoginView.as_api_view(client=Client.BROWSER),
    ),
    path("_allauth/", include("allauth.headless.urls")),
]
