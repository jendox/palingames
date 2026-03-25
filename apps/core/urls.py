from django.urls import path

from .views import HealthLiveView, HealthReadyView, MetricsView

urlpatterns = [
    path("health/live/", HealthLiveView.as_view(), name="health-live"),
    path("health/ready/", HealthReadyView.as_view(), name="health-ready"),
    path("metrics/", MetricsView.as_view(), name="metrics"),
]
