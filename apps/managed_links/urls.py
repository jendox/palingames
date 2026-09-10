from django.urls import path

from .views import ManagedLinkRedirectView

urlpatterns = [
    path("go/<str:token>/", ManagedLinkRedirectView.as_view(), name="managed-link-redirect"),
]
