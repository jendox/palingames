from django.urls import path

from .views import GuestProductDownloadView

urlpatterns = [
    path("downloads/guest/<str:token>/", GuestProductDownloadView.as_view(), name="guest-product-download"),
]
