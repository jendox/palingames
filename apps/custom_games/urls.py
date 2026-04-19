from django.urls import path

from .views import CustomGameDownloadView

urlpatterns = [
    path("custom-game/downloads/<str:token>/", CustomGameDownloadView.as_view(), name="custom-game-download"),
]
