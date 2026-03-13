from django.urls import path

from .views import CatalogView, ProductDetailView

urlpatterns = [
    path("catalog/", CatalogView.as_view(), name="catalog"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="product-detail"),
]
