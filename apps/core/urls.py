from django.urls import path

from .views import HealthLiveView, HealthReadyView, MetricsView, RobotsTxtView, SitemapXmlView

urlpatterns = [
    path("health/live/", HealthLiveView.as_view(), name="health-live"),
    path("health/ready/", HealthReadyView.as_view(), name="health-ready"),
    path("metrics/", MetricsView.as_view(), name="metrics"),
    path("robots.txt", RobotsTxtView.as_view(), name="robots-txt"),
    path("sitemap.xml", SitemapXmlView.as_view(), name="sitemap-xml"),
]
